"""TCP server for the N6-LTM Agent protocol."""
import logging
import math
import socket
import socketserver
from typing import Callable, Optional, Tuple

from common.constants import (
    BUFFER_SIZE,
    MESSAGE_DELIMITER,
    SERVER_HOST,
    SERVER_PORT,
    MsgType,
)
from common.protocol import (
    LineBuffer,
    ProtocolError,
    encode,
    make_ack,
    make_error,
    now,
)


logger = logging.getLogger(__name__)

# SQLite integration point:
# callback(message, peer_address, received_at) -> None
# It is called once for each valid message. The SQLite module can use message
# type to register an Agent, update last_seen, or insert a metrics sample.
MessageHandler = Callable[[dict, Tuple[str, int], int], None]


def _require_non_empty_string(message: dict, field: str) -> str:
    value = message.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"Trường '{field}' phải là chuỗi không rỗng")
    return value


def _require_timestamp(message: dict) -> None:
    timestamp = message.get("timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
        raise ProtocolError("Trường 'timestamp' phải là số Unix nguyên không âm")


def _validate_message(message: dict, registered_agent_id: Optional[str]) -> str:
    """Validate protocol fields and this connection's registration state."""
    message_type = message["type"]  # decode() already checks required fields/type.
    agent_id = _require_non_empty_string(message, "agent_id")

    if message_type == MsgType.REGISTER:
        for field in ("hostname", "ip", "os"):
            _require_non_empty_string(message, field)
        if registered_agent_id is not None and agent_id != registered_agent_id:
            raise ProtocolError("Kết nối đã đăng ký với agent_id khác")
        return agent_id

    if registered_agent_id is None:
        raise ProtocolError("Agent phải gửi REGISTER trước")
    if agent_id != registered_agent_id:
        raise ProtocolError("agent_id không khớp với REGISTER trên kết nối này")

    if message_type == MsgType.HEARTBEAT:
        _require_timestamp(message)
    elif message_type == MsgType.METRICS:
        _require_timestamp(message)
        for field in ("cpu", "ram", "disk"):
            value = message.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ProtocolError(f"Trường '{field}' phải là số từ 0 đến 100")
            if not 0 <= value <= 100 or (
                isinstance(value, float) and not math.isfinite(value)
            ):
                raise ProtocolError(f"Trường '{field}' phải nằm trong khoảng 0–100")
    else:
        # decode() rejects unsupported types, but keep this guard at the
        # semantic boundary if the shared protocol evolves later.
        raise ProtocolError(f"Loại tin nhắn không hỗ trợ: {message_type}")

    return agent_id


def _send_error(conn: socket.socket, message: str) -> bool:
    try:
        conn.sendall(encode(make_error(message)))
        return True
    except OSError:
        logger.debug("Socket đóng khi gửi ERROR", exc_info=True)
        return False


def _process_message(
    conn: socket.socket,
    addr: Tuple[str, int],
    message: dict,
    registered_agent_id: Optional[str],
    message_handler: Optional[MessageHandler],
) -> Tuple[bool, Optional[str]]:
    """Process one decoded message; return (connection_alive, agent_id)."""
    try:
        agent_id = _validate_message(message, registered_agent_id)
    except ProtocolError as exc:
        logger.info("Từ chối bản tin từ %s: %s", addr, exc)
        return _send_error(conn, str(exc)), registered_agent_id

    received_at = now()
    if message_handler is not None:
        try:
            message_handler(dict(message), addr, received_at)
        except Exception:
            logger.exception("Callback xử lý bản tin thất bại từ %s", addr)
            return (
                _send_error(conn, "Server không thể xử lý bản tin"),
                registered_agent_id,
            )

    try:
        # Week 2 requires an ACK for each accepted message. Keep the existing
        # protocol shape (ACK with status) from common.protocol.
        conn.sendall(encode(make_ack()))
    except OSError:
        logger.debug("Socket đóng khi gửi ACK", exc_info=True)
        return False, registered_agent_id

    if message["type"] == MsgType.REGISTER:
        registered_agent_id = agent_id
    return True, registered_agent_id


def handle_client(
    conn: socket.socket,
    addr: Tuple[str, int],
    message_handler: Optional[MessageHandler] = None,
) -> None:
    """Receive newline-delimited messages from one Agent connection.

    Complete frames are fed separately to the shared LineBuffer so a malformed
    line cannot prevent later lines in the same recv from being processed.
    """
    line_buffer = LineBuffer()
    pending = bytearray()
    registered_agent_id = None

    logger.info("Agent kết nối: %s", addr)
    try:
        while True:
            try:
                data = conn.recv(BUFFER_SIZE)
            except InterruptedError:
                continue
            except (ConnectionResetError, OSError):
                logger.info("Socket lỗi hoặc bị đóng từ %s", addr, exc_info=True)
                break

            if not data:
                break

            pending.extend(data)
            while True:
                delimiter_index = pending.find(MESSAGE_DELIMITER)
                if delimiter_index < 0:
                    break

                frame_end = delimiter_index + len(MESSAGE_DELIMITER)
                frame = bytes(pending[:frame_end])
                del pending[:frame_end]
                try:
                    messages = line_buffer.feed(frame)
                except ProtocolError as exc:
                    logger.info("JSON/protocol lỗi từ %s: %s", addr, exc)
                    if not _send_error(conn, str(exc)):
                        return
                    continue

                for message in messages:
                    alive, registered_agent_id = _process_message(
                        conn,
                        addr,
                        message,
                        registered_agent_id,
                        message_handler,
                    )
                    if not alive:
                        return
    finally:
        logger.info("Agent ngắt kết nối: %s", addr)


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class _RequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = self.server
        handle_client(
            self.request,
            self.client_address,
            getattr(server, "message_handler", None),
        )


def create_server(
    host: str = SERVER_HOST,
    port: int = SERVER_PORT,
    message_handler: Optional[MessageHandler] = None,
) -> _ThreadedTCPServer:
    """Create a concurrent TCP server; ``port=0`` is useful for local tests."""
    server = _ThreadedTCPServer((host, port), _RequestHandler)
    server.message_handler = message_handler
    return server


def monitor_loop() -> None:
    """Heartbeat status monitoring remains assigned to its owning module/task."""
    # TODO: integrate the agreed heartbeat monitor and event writer.
    return None


def main() -> None:
    server = create_server()
    logger.info("TCP Server đang lắng nghe tại %s:%s", *server.server_address)
    print(f"TCP Server đang lắng nghe tại {SERVER_HOST}:{SERVER_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Đang dừng TCP Server")
    finally:
        server.server_close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
