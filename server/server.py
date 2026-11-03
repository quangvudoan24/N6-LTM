"""
SERVER - nhận dữ liệu giám sát từ các Agent qua TCP.

Chạy từ thư mục gốc dự án:   python -m server.server

Chức năng:
  - Lắng nghe TCP tại SERVER_HOST:SERVER_PORT (lấy từ common/constants.py).
  - Mỗi Agent được phục vụ trong 1 thread riêng (handle_client).
  - Tách tin nhắn theo '\\n', kiểm tra cấu trúc, gọi lớp lưu trữ, trả ACK / ERROR.
  - Mất kết nối, JSON sai, lỗi socket chỉ ảnh hưởng đúng 1 Agent, không làm sập Server.

monitor_loop() (giám sát heartbeat) còn TODO.
Lưu SQLite qua module database/db.py (xem "HỢP ĐỒNG LƯU TRỮ" bên dưới).
"""
import logging
import socket
import sys
import threading

from common.constants import (
    BUFFER_SIZE, MESSAGE_DELIMITER, SERVER_HOST, SERVER_PORT, MsgType,
)

from common.protocol import ProtocolError, decode, encode, make_ack, make_error, now

log = logging.getLogger("server")
# Cài đặt riêng của Server (Agent không dùng)
LISTEN_BACKLOG = 5        # Số kết nối chờ tối đa trong hàng đợi của listen()
ACCEPT_TIMEOUT = 1        # Giây; để vòng accept() nhận được Ctrl+C (cần trên Windows)
CLIENT_TIMEOUT = 30       # Giây; Agent im lặng quá lâu thì Server đóng kết nối
MAX_MESSAGE_SIZE = 65536  # Byte; tin nhắn dài hơn mà chưa có '\n' thì ngắt kết nối

# Loại tin nhắn mà Agent được phép gửi lên Server (ACK/ERROR là chiều Server -> Agent)
AGENT_MESSAGES = (MsgType.REGISTER, MsgType.HEARTBEAT, MsgType.METRICS)


# ======================================================================
# HỢP ĐỒNG LƯU TRỮ (giao diện giữa Server và lớp SQLite)
# ----------------------------------------------------------------------
# Server gọi 3 hàm sau trong module `database/db.py`. Nếu module
# chưa tồn tại, Server dùng NullStorage (chỉ ghi log) để vẫn chạy/test được.
#
#   save_agent(agent_id, hostname, ip, os_name, last_seen)
#       -> upsert bảng agents, status = 'ONLINE', last_seen = last_seen
#          (có thể ghi event WENT_ONLINE nếu trước đó không phải ONLINE)
#   save_metrics(agent_id, cpu, ram, disk, timestamp)
#       -> INSERT bảng metrics
#   touch_agent(agent_id, last_seen)
#       -> UPDATE agents SET last_seen = ?  (gọi khi nhận HEARTBEAT / METRICS)
#
# Lưu ý: mỗi client chạy trong 1 thread khác nhau, mà sqlite3 mặc định
# không cho dùng chung connection giữa các thread -> mở connection mới trong mỗi
# hàm (hoặc dùng 1 Lock). Có lỗi thì cứ raise: Server sẽ bắt, ghi log và trả ERROR.
# ======================================================================
class NullStorage:
    """Lớp lưu trữ giả: chỉ ghi log. Dùng khi chưa có database/db.py."""

    def save_agent(self, agent_id, hostname, ip, os_name, last_seen):
        log.info("[NullStorage] save_agent %s (%s, %s, %s)", agent_id, hostname, ip, os_name)

    def save_metrics(self, agent_id, cpu, ram, disk, timestamp):
        log.info("[NullStorage] save_metrics %s cpu=%s ram=%s disk=%s", agent_id, cpu, ram, disk)

    def touch_agent(self, agent_id, last_seen):
        log.debug("[NullStorage] touch_agent %s", agent_id)


_STORAGE_API = ("save_agent", "save_metrics", "touch_agent")


def load_storage():
    """Dùng database/db.py nếu có đủ 3 hàm trong hợp đồng, ngược lại dùng NullStorage."""
    try:
        from database import db
    except ImportError:
        log.warning("Chưa có database/db.py -> dữ liệu CHƯA được lưu vào SQLite (chỉ ghi log).")
        return NullStorage()
    missing = [name for name in _STORAGE_API if not hasattr(db, name)]
    if missing:
        log.warning("database/db.py thiếu hàm %s -> dùng NullStorage.", missing)
        return NullStorage()
    return db


# ======================================================================
# Kiểm tra & xử lý tin nhắn
# ======================================================================
class Session:
    """Trạng thái của 1 kết nối: Agent nào đang ở đầu bên kia."""

    def __init__(self, addr):
        self.addr = addr
        self.agent_id = None  # Được gán sau khi REGISTER thành công


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_values(msg):
    """Kiểm tra KIỂU và KHOẢNG GIÁ TRỊ (protocol.validate chỉ kiểm tra có đủ trường chưa)."""
    if msg["type"] not in AGENT_MESSAGES:
        raise ProtocolError(f"Server không nhận tin nhắn loại {msg['type']} từ Agent")

    agent_id = msg["agent_id"]
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ProtocolError("agent_id phải là chuỗi không rỗng")

    if msg["type"] == MsgType.REGISTER:
        for field in ("hostname", "ip", "os"):
            if not isinstance(msg[field], str) or not msg[field].strip():
                raise ProtocolError(f"{field} phải là chuỗi không rỗng")
        return

    timestamp = msg["timestamp"]
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        raise ProtocolError("timestamp phải là số nguyên giây Unix")

    if msg["type"] == MsgType.METRICS:
        for field in ("cpu", "ram", "disk"):
            value = msg[field]
            # NaN không thỏa 0 <= x <= 100 nên cũng bị loại ở đây
            if not _is_number(value) or not 0 <= value <= 100:
                raise ProtocolError(f"{field} phải là số trong khoảng 0-100")


def process_message(msg, session, storage):
    """Xử lý 1 tin nhắn đã decode. Trả về tin nhắn phản hồi; raise ProtocolError nếu sai."""
    check_values(msg)
    msg_type = msg["type"]
    agent_id = msg["agent_id"]

    if msg_type == MsgType.REGISTER:
        storage.save_agent(agent_id, msg["hostname"], msg["ip"], msg["os"], now())
        session.agent_id = agent_id
        log.info("REGISTER %s từ %s:%s (%s)", agent_id, *session.addr[:2], msg["hostname"])
        return make_ack()

    if session.agent_id is None:
        raise ProtocolError("Chưa REGISTER: hãy gửi REGISTER trước")
    if agent_id != session.agent_id:
        raise ProtocolError(
            f"agent_id '{agent_id}' không khớp với agent đã đăng ký '{session.agent_id}'")

    if msg_type == MsgType.METRICS:
        storage.save_metrics(agent_id, msg["cpu"], msg["ram"], msg["disk"], msg["timestamp"])
        log.debug("METRICS %s cpu=%s ram=%s disk=%s",
                  agent_id, msg["cpu"], msg["ram"], msg["disk"])
    else:
        log.debug("HEARTBEAT %s", agent_id)
    # last_seen dùng đồng hồ của Server (không tin đồng hồ Agent) để monitor_loop tính đúng
    storage.touch_agent(agent_id, now())
    return make_ack()


def handle_line(line, session, storage):
    """1 dòng thô -> 1 tin nhắn phản hồi (ACK hoặc ERROR). Không bao giờ raise."""
    try:
        msg = decode(line)
        return process_message(msg, session, storage)
    except (ProtocolError, TypeError) as e:  # TypeError: vd {"type": []} không hash được
        log.warning("Bản tin không hợp lệ từ %s:%s: %s", *session.addr[:2], e)
        return make_error(str(e))
    except Exception:
        # Thường là lỗi DB. Ghi đầy đủ traceback, báo gọn cho Agent, giữ kết nối.
        log.exception("Lỗi nội bộ khi xử lý tin nhắn từ %s:%s", *session.addr[:2])
        return make_error("Lỗi nội bộ Server, dữ liệu chưa được lưu")


# ======================================================================
# Phần socket
# ======================================================================
def handle_client(conn, addr, storage=None):
    """Mỗi client chạy trong 1 thread riêng."""
    storage = storage if storage is not None else NullStorage()
    session = Session(addr)
    buf = b""
    log.info("Kết nối mới từ %s:%s", *addr[:2])
    try:
        conn.settimeout(CLIENT_TIMEOUT)
        while True:
            try:
                data = conn.recv(BUFFER_SIZE)
            except socket.timeout:
                log.warning("%s:%s im lặng quá %ss -> đóng kết nối", *addr[:2], CLIENT_TIMEOUT)
                break
            if not data:  # Agent đóng kết nối bình thường
                log.info("Agent %s (%s:%s) đã ngắt kết nối", session.agent_id, *addr[:2])
                break

            buf += data
            # TCP là luồng byte: 1 lần recv có thể chứa nhiều / nửa tin nhắn.
            # Tách từng dòng và decode riêng để 1 dòng hỏng không làm mất các dòng tốt.
            while MESSAGE_DELIMITER in buf:
                line, buf = buf.split(MESSAGE_DELIMITER, 1)
                if line.strip():
                    conn.sendall(encode(handle_line(line, session, storage)))

            if len(buf) > MAX_MESSAGE_SIZE:  # Chưa có '\n' mà đã quá dài -> chống tràn bộ nhớ
                log.warning("%s:%s gửi tin nhắn quá dài (>%s byte) -> ngắt", *addr[:2],
                            MAX_MESSAGE_SIZE)
                conn.sendall(encode(make_error("Tin nhắn quá dài")))
                break
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        log.warning("Mất kết nối đột ngột với %s (%s:%s)", session.agent_id, *addr[:2])
    except OSError as e:
        log.error("Lỗi socket với %s:%s: %s", *addr[:2], e)
    except Exception:
        log.exception("Lỗi không lường trước ở %s:%s", *addr[:2])
    finally:
        try:
            conn.close()
        except OSError:
            pass


def monitor_loop():
    """Luồng giám sát: mỗi MONITOR_SCAN_INTERVAL giây kiểm tra last_seen."""
    # TODO: tính WARNING/OFFLINE theo constants, ghi event
    pass


def create_server_socket(host=SERVER_HOST, port=SERVER_PORT):
    """Tạo socket lắng nghe. Ném OSError nếu cổng đang bị chiếm / không có quyền."""
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: cấm 2 server dùng chung 1 cổng
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:                                       # Linux/macOS
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((host, port))
        server_sock.listen(LISTEN_BACKLOG)
        server_sock.settimeout(ACCEPT_TIMEOUT)  # để vòng accept kiểm tra được stop_event
    except OSError:
        server_sock.close()
        raise
    return server_sock


def serve_forever(server_sock, storage, stop_event):
    """Vòng accept: mỗi kết nối mới -> 1 thread handle_client. Dừng khi stop_event được set."""
    while not stop_event.is_set():
        try:
            conn, addr = server_sock.accept()
        except socket.timeout:
            continue
        except OSError as e:
            if stop_event.is_set():
                break
            log.error("Lỗi accept: %s", e)
            stop_event.wait(0.5)  # tránh vòng lặp lỗi quay hết CPU
            continue
        threading.Thread(target=handle_client, args=(conn, addr, storage),
                         daemon=True).start()


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(threadName)s: %(message)s")
    storage = load_storage()
    try:
        server_sock = create_server_socket()
    except OSError as e:
        log.error("Không thể lắng nghe tại %s:%s - %s", SERVER_HOST, SERVER_PORT, e)
        sys.exit(1)

    stop_event = threading.Event()
    # TODO: khởi động threading.Thread(target=monitor_loop, daemon=True).start()
    log.info("Server đang lắng nghe tại %s:%s (Ctrl+C để dừng)", SERVER_HOST, SERVER_PORT)
    try:
        serve_forever(server_sock, storage, stop_event)
    except KeyboardInterrupt:
        log.info("Nhận Ctrl+C, đang dừng Server...")
    finally:
        stop_event.set()
        server_sock.close()


if __name__ == "__main__":
    main()