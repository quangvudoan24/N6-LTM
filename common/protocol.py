"""
Mã hóa / giải mã tin nhắn và xử lý "ranh giới tin nhắn" của TCP.
Cả Agent và Server DÙNG CHUNG file này -> không ai tự viết lại.

Cách dùng:
    # Gửi
    sock.sendall(encode({"type": MsgType.HEARTBEAT, "agent_id": "PC-01", "timestamp": 1}))

    # Nhận (TCP là luồng byte, 1 lần recv có thể chứa nhiều/nửa tin nhắn)
    buf = LineBuffer()
    data = sock.recv(BUFFER_SIZE)
    for msg in buf.feed(data):
        print(msg)
"""
import json
import time

from common.constants import ENCODING, MESSAGE_DELIMITER, MsgType

# Trường bắt buộc cho từng loại tin nhắn (xem docs/PROTOCOL.md)
REQUIRED_FIELDS = {
    MsgType.REGISTER: ["agent_id", "hostname", "ip", "os"],
    MsgType.HEARTBEAT: ["agent_id", "timestamp"],
    MsgType.METRICS: ["agent_id", "cpu", "ram", "disk", "timestamp"],
    MsgType.ACK: ["status"],
    MsgType.ERROR: ["message"],
}


class ProtocolError(Exception):
    """Tin nhắn sai định dạng hoặc thiếu trường."""


def now() -> int:
    """Timestamp chuẩn của dự án: số giây Unix (int)."""
    return int(time.time())


def encode(msg: dict) -> bytes:
    validate(msg)
    return json.dumps(msg, ensure_ascii=False).encode(ENCODING) + MESSAGE_DELIMITER


def decode(line: bytes) -> dict:
    try:
        msg = json.loads(line.decode(ENCODING))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ProtocolError(f"JSON không hợp lệ: {e}")
    validate(msg)
    return msg


def validate(msg: dict) -> None:
    if not isinstance(msg, dict) or "type" not in msg:
        raise ProtocolError("Thiếu trường 'type'")
    required = REQUIRED_FIELDS.get(msg["type"])
    if required is None:
        raise ProtocolError(f"Loại tin nhắn không hỗ trợ: {msg['type']}")
    missing = [f for f in required if f not in msg]
    if missing:
        raise ProtocolError(f"{msg['type']} thiếu trường: {missing}")


class LineBuffer:
    """Gom dữ liệu recv() và tách thành từng tin nhắn hoàn chỉnh theo '\\n'."""

    def __init__(self):
        self._buf = b""

    def feed(self, data: bytes) -> list:
        self._buf += data
        messages = []
        while MESSAGE_DELIMITER in self._buf:
            line, self._buf = self._buf.split(MESSAGE_DELIMITER, 1)
            if line.strip():
                messages.append(decode(line))
        return messages


# ---- Hàm tiện ích tạo tin nhắn (đảm bảo đúng tên trường) ----
def make_register(agent_id, hostname, ip, os_name):
    return {"type": MsgType.REGISTER, "agent_id": agent_id, "hostname": hostname,
            "ip": ip, "os": os_name}


def make_heartbeat(agent_id):
    return {"type": MsgType.HEARTBEAT, "agent_id": agent_id, "timestamp": now()}


def make_metrics(agent_id, cpu, ram, disk):
    return {"type": MsgType.METRICS, "agent_id": agent_id, "cpu": cpu, "ram": ram,
            "disk": disk, "timestamp": now()}


def make_ack(status="ok"):
    return {"type": MsgType.ACK, "status": status}


def make_error(message):
    return {"type": MsgType.ERROR, "message": message}
