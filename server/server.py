"""TCP Server that validates Agent messages and persists them to SQLite."""
from functools import partial
import logging
import socket
import threading

from common.constants import (
    BUFFER_SIZE,
    DB_PATH,
    MESSAGE_DELIMITER,
    MONITOR_SCAN_INTERVAL,
    MsgType,
    OFFLINE_AFTER,
    SERVER_HOST,
    SERVER_PORT,
    Status,
    WARNING_AFTER,
)
from common.protocol import (
    ProtocolError,
    decode,
    encode,
    make_ack,
    make_error,
    now,
)
from database.init_db import init_db
from database.operations import handle_server_message, list_agents, update_agent


logger = logging.getLogger("server")
LISTEN_BACKLOG = 5
ACCEPT_TIMEOUT = 0.5
CLIENT_TIMEOUT = 30
MAX_MESSAGE_SIZE = 65536
AGENT_MESSAGES = (MsgType.REGISTER, MsgType.HEARTBEAT, MsgType.METRICS)


class ServerState:
    """Shared state for the monitor and the per-client handler threads."""

    def __init__(self, db_path=DB_PATH, message_handler=None, clock=now):
        self.db_path = db_path
        self.message_handler = (
            message_handler
            if message_handler is not None
            else partial(handle_server_message, db_path=db_path)
        )
        self.clock = clock
        # Present only until this registration receives its first valid heartbeat.
        self.registration_times = {}
        # A generation identifies the currently registered TCP session for an
        # Agent. Old sockets cannot update data after a reconnect takes over.
        self.active_sessions = {}
        self.next_generation = 0
        # Serialize writes and monitor scans so a stale scan cannot overwrite a
        # heartbeat that has just been committed.
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.client_lock = threading.Lock()
        self.client_sockets = set()
        self.client_threads = set()
        self.monitor_thread = None


class Session:
    """Registration state for one TCP connection."""

    def __init__(self, address):
        self.address = address
        self.agent_id = None
        self.generation = None


class CloseSessionError(ProtocolError):
    """Protocol violation that should receive one ERROR before socket close."""


def _require_non_empty_string(message, field):
    value = message.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"Trường '{field}' phải là chuỗi không rỗng")
    return value


def _require_timestamp(message):
    value = message.get("timestamp")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProtocolError("Trường 'timestamp' phải là số Unix nguyên không âm")


def validate_message(message, session):
    """Validate an Agent message and enforce registration per connection."""
    message_type = message["type"]  # decode() checks required fields and type.
    if message_type not in AGENT_MESSAGES:
        raise ProtocolError(f"Server không nhận bản tin {message_type} từ Agent")

    agent_id = _require_non_empty_string(message, "agent_id")
    if message_type == MsgType.REGISTER:
        if session.agent_id is not None:
            raise CloseSessionError("Mỗi kết nối chỉ được REGISTER một lần")
        for field in ("hostname", "ip", "os"):
            _require_non_empty_string(message, field)
        return agent_id

    if session.agent_id is None:
        raise ProtocolError("Agent phải gửi REGISTER trước")
    if agent_id != session.agent_id:
        raise ProtocolError("agent_id không khớp với REGISTER trên kết nối này")

    _require_timestamp(message)
    if message_type == MsgType.METRICS:
        for field in ("cpu", "ram", "disk"):
            value = message.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ProtocolError(f"Trường '{field}' phải là số từ 0 đến 100")
            if not 0 <= value <= 100:
                raise ProtocolError(f"Trường '{field}' phải nằm trong khoảng 0–100")
    return agent_id


def process_message(message, session, state):
    """Persist one valid message before returning a REGISTER ACK, if required."""
    agent_id = validate_message(message, session)

    # The DB write and the corresponding in-memory session change are atomic
    # with respect to monitor scans and reconnects. Capture received_at only
    # after taking the lock so a delayed callback cannot commit an older time
    # after a newer registration or heartbeat.
    with state.lock:
        if message["type"] != MsgType.REGISTER:
            if (
                session.generation is None
                or state.active_sessions.get(agent_id) != session.generation
            ):
                raise CloseSessionError(
                    "Kết nối không còn là session hiện hành của Agent"
                )

        received_at = state.clock()
        state.message_handler(dict(message), session.address, received_at)
        if message["type"] == MsgType.REGISTER:
            state.next_generation += 1
            session.agent_id = agent_id
            session.generation = state.next_generation
            state.active_sessions[agent_id] = session.generation
            state.registration_times[agent_id] = received_at
        elif message["type"] == MsgType.HEARTBEAT:
            state.registration_times.pop(agent_id, None)

    # This protocol uses ACK for REGISTER only. HEARTBEAT and METRICS are
    # persisted successfully but intentionally have no success response.
    return make_ack() if message["type"] == MsgType.REGISTER else None


def _session_is_current(session, state):
    """Check a registered connection using its stored identity, not frame data."""
    if session.generation is None:
        return True
    with state.lock:
        return (
            session.agent_id is not None
            and state.active_sessions.get(session.agent_id) == session.generation
        )


def _ordinary_error_response(message, session, state):
    """Keep current clients open for ordinary errors; close stale clients."""
    stale = session.generation is not None and not _session_is_current(session, state)
    return make_error(message), stale


def handle_line(line, session, state):
    """Return (response, close_connection) for one newline-delimited frame."""
    # A stale registered socket is rejected before parsing, so malformed JSON
    # cannot bypass the generation check and produce repeated ERROR replies.
    if session.generation is not None and not _session_is_current(session, state):
        return make_error("Kết nối không còn là session hiện hành của Agent"), True

    try:
        message = decode(line)
        return process_message(message, session, state), False
    except CloseSessionError as exc:
        logger.info("Đóng session không còn hợp lệ từ %s: %s", session.address, exc)
        return make_error(str(exc)), True
    except (ProtocolError, TypeError, ValueError) as exc:
        logger.info("Từ chối bản tin từ %s: %s", session.address, exc)
        return _ordinary_error_response(str(exc), session, state)
    except Exception:
        # Includes DatabaseError: no successful ACK is sent if SQLite fails.
        logger.exception("Không thể xử lý bản tin từ %s", session.address)
        return _ordinary_error_response(
            "Server không thể lưu hoặc xử lý bản tin", session, state
        )


def handle_client(conn, addr, state):
    """Serve one Agent connection, preserving valid frames around bad frames."""
    session = Session(addr)
    pending = bytearray()
    try:
        conn.settimeout(CLIENT_TIMEOUT)
        while not state.stop_event.is_set():
            try:
                data = conn.recv(BUFFER_SIZE)
            except socket.timeout:
                logger.info("Agent %s hết thời gian chờ", session.agent_id)
                break
            except InterruptedError:
                continue
            except OSError:
                if not state.stop_event.is_set():
                    logger.info("Socket lỗi từ %s", addr, exc_info=True)
                break

            if not data:
                break
            pending.extend(data)

            while True:
                delimiter = pending.find(MESSAGE_DELIMITER)
                if delimiter < 0:
                    break
                line = bytes(pending[:delimiter])
                del pending[:delimiter + len(MESSAGE_DELIMITER)]
                if not line.strip():
                    if (
                        session.generation is not None
                        and not _session_is_current(session, state)
                    ):
                        try:
                            conn.sendall(encode(make_error(
                                "Kết nối không còn là session hiện hành của Agent"
                            )))
                        except OSError:
                            pass
                        return
                    continue
                try:
                    response, close_connection = handle_line(line, session, state)
                    if response is not None:
                        conn.sendall(encode(response))
                    if close_connection:
                        return
                except OSError:
                    return

            if len(pending) > MAX_MESSAGE_SIZE:
                try:
                    conn.sendall(encode(make_error("Tin nhắn quá dài")))
                except OSError:
                    pass
                break
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
        logger.info("Agent %s ngắt kết nối đột ngột", session.agent_id)
    except OSError:
        logger.info("Lỗi socket với %s", addr, exc_info=True)
    finally:
        retire_session(session, state)
        try:
            conn.close()
        except OSError:
            pass
        with state.client_lock:
            state.client_sockets.discard(conn)


def retire_session(session, state):
    """Remove only this session's active marker, preserving newer sessions."""
    if session.agent_id is None or session.generation is None:
        return
    with state.lock:
        if state.active_sessions.get(session.agent_id) == session.generation:
            state.active_sessions.pop(session.agent_id, None)
            state.registration_times.pop(session.agent_id, None)


def status_for_age(age_seconds):
    """Map elapsed whole seconds to the configured connection status."""
    if age_seconds <= WARNING_AFTER:
        return Status.ONLINE
    if age_seconds <= OFFLINE_AFTER:
        return Status.WARNING
    return Status.OFFLINE


def monitor_once(state, current_time=None):
    """Recompute persisted statuses once; accepts time explicitly for tests."""
    current_time = state.clock() if current_time is None else current_time
    with state.lock:
        agents = list_agents(db_path=state.db_path)
        for agent in agents:
            agent_id = agent["agent_id"]
            registered_at = state.registration_times.get(agent_id)
            if registered_at is not None:
                reference_time = registered_at
            elif agent["last_seen"] is not None:
                reference_time = agent["last_seen"]
            else:
                # A NULL last_seen with no live registration is a record from
                # before this Server process (or otherwise not currently seen).
                desired_status = Status.OFFLINE
                reference_time = None

            if reference_time is not None:
                desired_status = status_for_age(current_time - reference_time)
            if agent["status"] != desired_status:
                update_agent(
                    agent_id,
                    status=desired_status,
                    db_path=state.db_path,
                )


def monitor_loop(state):
    """Run heartbeat monitoring until the Server stop event is set."""
    while not state.stop_event.is_set():
        try:
            monitor_once(state)
        except Exception:
            logger.exception("Heartbeat monitor không cập nhật được trạng thái")
        if state.stop_event.wait(MONITOR_SCAN_INTERVAL):
            break


def create_server_socket(host=SERVER_HOST, port=SERVER_PORT):
    """Bind a listening socket; port 0 allows isolated loopback tests."""
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((host, port))
        server_sock.listen(LISTEN_BACKLOG)
        server_sock.settimeout(ACCEPT_TIMEOUT)
    except OSError:
        server_sock.close()
        raise
    return server_sock


def _client_worker(conn, addr, state):
    try:
        handle_client(conn, addr, state)
    finally:
        with state.client_lock:
            state.client_threads.discard(threading.current_thread())


def _shutdown_clients(state):
    with state.client_lock:
        connections = list(state.client_sockets)
        threads = list(state.client_threads)

    for conn in connections:
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            conn.close()
        except OSError:
            pass
    for thread in threads:
        if thread is not threading.current_thread():
            thread.join()


def serve_forever(server_sock, state):
    """Accept clients and own monitor/client thread shutdown lifecycle."""
    state.monitor_thread = threading.Thread(
        target=monitor_loop,
        args=(state,),
        name="heartbeat-monitor",
        daemon=True,
    )
    state.monitor_thread.start()
    try:
        while not state.stop_event.is_set():
            try:
                conn, addr = server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if state.stop_event.is_set():
                    break
                logger.exception("Lỗi accept TCP")
                state.stop_event.wait(0.5)
                continue

            if state.stop_event.is_set():
                conn.close()
                break
            worker = threading.Thread(
                target=_client_worker,
                args=(conn, addr, state),
                name=f"agent-{addr[0]}:{addr[1]}",
                daemon=True,
            )
            with state.client_lock:
                state.client_sockets.add(conn)
                state.client_threads.add(worker)
            try:
                worker.start()
            except Exception:
                with state.client_lock:
                    state.client_sockets.discard(conn)
                    state.client_threads.discard(worker)
                conn.close()
                raise
    finally:
        state.stop_event.set()
        try:
            server_sock.close()
        except OSError:
            pass
        _shutdown_clients(state)
        if state.monitor_thread is not threading.current_thread():
            state.monitor_thread.join()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
    )
    try:
        init_db()
        server_sock = create_server_socket()
    except Exception:
        logger.exception("Không thể khởi tạo DB hoặc lắng nghe TCP")
        raise SystemExit(1)

    state = ServerState()
    logger.info("Server đang lắng nghe tại %s:%s", *server_sock.getsockname())
    try:
        serve_forever(server_sock, state)
    except KeyboardInterrupt:
        logger.info("Nhận Ctrl+C, đang dừng Server")
        state.stop_event.set()


if __name__ == "__main__":
    main()
