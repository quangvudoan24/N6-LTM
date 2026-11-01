"""Các thao tác SQLite cho thiết bị Agent và số liệu tài nguyên."""
from contextlib import contextmanager
import sqlite3
import time
from typing import Optional, Tuple

from common.constants import DB_PATH, MsgType, Status


_SQLITE_INTEGER_MAX = (1 << 63) - 1


class DatabaseError(RuntimeError):
    """SQLite không thể hoàn tất thao tác cơ sở dữ liệu."""


@contextmanager
def _connection(db_path):
    connection = None
    try:
        connection = sqlite3.connect(str(db_path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        # SQLite yêu cầu bật khóa ngoại trên từng kết nối.
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            yield connection
    except sqlite3.Error as exc:
        raise DatabaseError(f"Lỗi thao tác SQLite: {exc}") from exc
    finally:
        if connection is not None:
            connection.close()


def _required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field}' phải là chuỗi không rỗng")
    return value


def _optional_text(value, field):
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"'{field}' phải là chuỗi không rỗng hoặc None")
    return value


def _timestamp(value, field):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > _SQLITE_INTEGER_MAX
    ):
        raise ValueError(f"'{field}' phải nằm trong miền số nguyên SQLite 64-bit có dấu")
    return value


def _status(value):
    allowed = {Status.ONLINE, Status.WARNING, Status.OFFLINE}
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"'status' phải là một trong {sorted(allowed)}")
    return value


def _metric_value(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"'{field}' phải là số từ 0 đến 100")
    if not 0 <= value <= 100:
        raise ValueError(f"'{field}' phải nằm trong khoảng 0–100")
    return value


def register_agent(
    agent_id,
    hostname,
    ip_address,
    os_name,
    *,
    status=Status.ONLINE,
    db_path=DB_PATH,
):
    """Thêm Agent hoặc cập nhật thông tin khi Agent đăng ký lại."""
    agent_id = _required_text(agent_id, "agent_id")
    hostname = _required_text(hostname, "hostname")
    ip_address = _required_text(ip_address, "ip_address")
    os_name = _required_text(os_name, "os")
    status = _status(status)

    created_at = int(time.time())
    with _connection(db_path) as connection:
        connection.execute(
            """
            INSERT INTO agents (
                agent_id, hostname, ip_address, os, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                hostname = excluded.hostname,
                ip_address = excluded.ip_address,
                os = excluded.os,
                status = excluded.status
            """,
            (agent_id, hostname, ip_address, os_name, status, created_at),
        )


def update_agent(
    agent_id,
    *,
    hostname=None,
    ip_address=None,
    os_name=None,
    status=None,
    last_seen=None,
    db_path=DB_PATH,
):
    """Cập nhật các trường được cung cấp; báo lỗi nếu Agent chưa tồn tại."""
    agent_id = _required_text(agent_id, "agent_id")
    hostname = _optional_text(hostname, "hostname")
    ip_address = _optional_text(ip_address, "ip_address")
    os_name = _optional_text(os_name, "os")
    if status is not None:
        status = _status(status)
    if last_seen is not None:
        last_seen = _timestamp(last_seen, "last_seen")

    if all(value is None for value in (hostname, ip_address, os_name, status, last_seen)):
        raise ValueError("Cần cung cấp ít nhất một trường để cập nhật")

    with _connection(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE agents SET
                hostname = COALESCE(?, hostname),
                ip_address = COALESCE(?, ip_address),
                os = COALESCE(?, os),
                status = COALESCE(?, status),
                last_seen = COALESCE(?, last_seen)
            WHERE agent_id = ?
            """,
            (hostname, ip_address, os_name, status, last_seen, agent_id),
        )
        if cursor.rowcount == 0:
            raise LookupError(f"Không tìm thấy Agent '{agent_id}'")


def save_metrics(agent_id, cpu, ram, disk, timestamp, *, db_path=DB_PATH):
    """Lưu một mẫu CPU/RAM/Disk; Agent phải tồn tại trước đó."""
    agent_id = _required_text(agent_id, "agent_id")
    cpu = _metric_value(cpu, "cpu")
    ram = _metric_value(ram, "ram")
    disk = _metric_value(disk, "disk")
    timestamp = _timestamp(timestamp, "timestamp")

    with _connection(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO metrics (agent_id, cpu, ram, disk, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (agent_id, cpu, ram, disk, timestamp),
        )
        return cursor.lastrowid


def list_agents(status=None, *, db_path=DB_PATH):
    """Trả danh sách Agent, có thể lọc theo trạng thái."""
    if status is not None:
        status = _status(status)

    query = """
        SELECT agent_id, hostname, ip_address, os, status, last_seen, created_at
        FROM agents
    """
    parameters = ()
    if status is not None:
        query += " WHERE status = ?"
        parameters = (status,)
    query += " ORDER BY hostname, agent_id"

    with _connection(db_path) as connection:
        rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]


def get_agent_status(agent_id, *, db_path=DB_PATH) -> Optional[str]:
    """Trả trạng thái hiện tại, hoặc None nếu Agent chưa có trong DB."""
    agent_id = _required_text(agent_id, "agent_id")
    with _connection(db_path) as connection:
        row = connection.execute(
            "SELECT status FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        return row["status"] if row else None


def get_metrics(agent_id, limit=50, *, db_path=DB_PATH) -> list:
    """Trả lịch sử metrics mới nhất trước, giới hạn số hàng kết quả."""
    agent_id = _required_text(agent_id, "agent_id")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("'limit' phải là số nguyên dương")

    with _connection(db_path) as connection:
        rows = connection.execute(
            """
            SELECT cpu, ram, disk, timestamp
            FROM metrics
            WHERE agent_id = ?
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (agent_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def handle_server_message(
    message: dict,
    peer_address: Tuple[str, int],
    received_at: int,
    *,
    db_path=DB_PATH,
):
    """Callback SQLite tương thích với giao diện bàn giao của TCP Server."""
    if not isinstance(message, dict):
        raise ValueError("'message' phải là dict")
    received_at = _timestamp(received_at, "received_at")
    message_type = message.get("type")

    if message_type == MsgType.REGISTER:
        register_agent(
            message.get("agent_id"),
            message.get("hostname"),
            message.get("ip"),
            message.get("os"),
            status=Status.ONLINE,
            db_path=db_path,
        )
    elif message_type == MsgType.HEARTBEAT:
        _timestamp(message.get("timestamp"), "timestamp")
        update_agent(
            message.get("agent_id"), last_seen=received_at, db_path=db_path
        )
    elif message_type == MsgType.METRICS:
        save_metrics(
            message.get("agent_id"),
            message.get("cpu"),
            message.get("ram"),
            message.get("disk"),
            message.get("timestamp"),
            db_path=db_path,
        )
    else:
        raise ValueError(f"Loại bản tin không hỗ trợ: {message_type}")
