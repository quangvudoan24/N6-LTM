"""Test Server bằng socket thật trên cổng ngẫu nhiên (không đụng cổng 9000)."""
import json
import socket
import threading

import pytest

from common.constants import MsgType
from common.protocol import encode, make_heartbeat, make_metrics, make_register
from server import server as srv


class FakeStorage:
    def __init__(self):
        self.agents, self.metrics, self.touches = [], [], []
        self.fail = False

    def _check(self):
        if self.fail:
            raise RuntimeError("DB hỏng")

    def save_agent(self, *args):
        self._check()
        self.agents.append(args)

    def save_metrics(self, *args):
        self._check()
        self.metrics.append(args)

    def touch_agent(self, *args):
        self._check()
        self.touches.append(args)


@pytest.fixture
def env():
    storage = FakeStorage()
    stop = threading.Event()
    sock = srv.create_server_socket("127.0.0.1", 0)
    port = sock.getsockname()[1]
    t = threading.Thread(target=srv.serve_forever, args=(sock, storage, stop), daemon=True)
    t.start()
    yield port, storage
    stop.set()
    t.join(timeout=3)
    sock.close()


class Client:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=3)
        self.buf = b""

    def send_raw(self, data: bytes):
        self.sock.sendall(data)

    def send(self, msg):
        self.send_raw(encode(msg))

    def recv(self):
        while b"\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("Server đã đóng kết nối")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)

    def close(self):
        self.sock.close()


def registered_client(port, agent_id="PC-01"):
    c = Client(port)
    c.send(make_register(agent_id, "host", "10.0.0.1", "Linux"))
    assert c.recv() == {"type": MsgType.ACK, "status": "ok"}
    return c


def test_register_gets_ack_and_is_saved(env):
    port, storage = env
    c = registered_client(port)
    assert storage.agents[0][:4] == ("PC-01", "host", "10.0.0.1", "Linux")
    c.close()


def test_heartbeat_and_metrics_get_ack_and_are_saved(env):
    port, storage = env
    c = registered_client(port)
    c.send(make_heartbeat("PC-01"))
    assert c.recv()["type"] == MsgType.ACK
    c.send(make_metrics("PC-01", 35.2, 61, 70.5))
    assert c.recv()["type"] == MsgType.ACK
    assert storage.metrics[0][:4] == ("PC-01", 35.2, 61, 70.5)
    assert len(storage.touches) == 2
    c.close()


def test_invalid_json_returns_error_and_keeps_connection(env):
    port, _ = env
    c = registered_client(port)
    c.send_raw(b"day khong phai json\n")
    assert c.recv()["type"] == MsgType.ERROR
    c.send(make_heartbeat("PC-01"))  # kết nối vẫn dùng được
    assert c.recv()["type"] == MsgType.ACK
    c.close()


@pytest.mark.parametrize("raw", [
    b'{"type": "HEARTBEAT", "agent_id": "PC-01"}\n',        # thiếu timestamp
    b'{"type": "XYZ"}\n',                                    # loại lạ
    b'[1, 2, 3]\n',                                          # không phải object
    b'{"type": []}\n',                                       # type không hash được
    b'{"type": "ACK", "status": "ok"}\n',                    # sai chiều
    b'{"type": "METRICS", "agent_id": "PC-01", "cpu": 150, "ram": 1, "disk": 1, "timestamp": 1}\n',
    b'{"type": "METRICS", "agent_id": "PC-01", "cpu": "x", "ram": 1, "disk": 1, "timestamp": 1}\n',
    b'{"type": "METRICS", "agent_id": "PC-01", "cpu": NaN, "ram": 1, "disk": 1, "timestamp": 1}\n',
    b'{"type": "HEARTBEAT", "agent_id": "PC-01", "timestamp": "abc"}\n',
    b'{"type": "HEARTBEAT", "agent_id": "PC-99", "timestamp": 1}\n',  # agent_id không khớp
])
def test_bad_messages_return_error(env, raw):
    port, storage = env
    c = registered_client(port)
    c.send_raw(raw)
    assert c.recv()["type"] == MsgType.ERROR
    assert storage.metrics == []
    c.close()


def test_data_before_register_is_rejected(env):
    port, storage = env
    c = Client(port)
    c.send(make_metrics("PC-01", 1, 2, 3))
    assert c.recv()["type"] == MsgType.ERROR
    assert storage.metrics == []
    c.close()


def test_two_messages_in_one_packet_and_split_packet(env):
    port, _ = env
    c = registered_client(port)
    c.send_raw(encode(make_heartbeat("PC-01")) + encode(make_metrics("PC-01", 1, 2, 3)))
    assert c.recv()["type"] == MsgType.ACK
    assert c.recv()["type"] == MsgType.ACK
    raw = encode(make_heartbeat("PC-01"))
    c.send_raw(raw[:10])
    c.send_raw(raw[10:])
    assert c.recv()["type"] == MsgType.ACK
    c.close()


def test_bad_line_does_not_drop_good_lines_in_same_packet(env):
    port, storage = env
    c = registered_client(port)
    c.send_raw(encode(make_metrics("PC-01", 1, 2, 3)) + b"rac\n" + encode(make_heartbeat("PC-01")))
    assert [c.recv()["type"] for _ in range(3)] == [MsgType.ACK, MsgType.ERROR, MsgType.ACK]
    assert len(storage.metrics) == 1
    c.close()


def test_abrupt_disconnect_does_not_kill_server(env):
    port, _ = env
    c = registered_client(port)
    c.send_raw(b'{"type": "HEART')  # nửa tin nhắn rồi cắt
    c.close()
    c2 = registered_client(port, "PC-02")  # Server vẫn nhận Agent khác
    c2.close()


def test_multiple_agents_concurrently(env):
    port, storage = env
    clients = [registered_client(port, f"PC-{i}") for i in range(5)]
    for i, c in enumerate(clients):
        c.send(make_metrics(f"PC-{i}", i, i, i))
    for c in clients:
        assert c.recv()["type"] == MsgType.ACK
        c.close()
    assert len(storage.metrics) == 5


def test_storage_failure_returns_error_and_keeps_connection(env):
    port, storage = env
    c = registered_client(port)
    storage.fail = True
    c.send(make_metrics("PC-01", 1, 2, 3))
    assert c.recv()["type"] == MsgType.ERROR
    storage.fail = False
    c.send(make_metrics("PC-01", 1, 2, 3))
    assert c.recv()["type"] == MsgType.ACK
    c.close()


def test_oversized_message_without_newline_closes_connection(env):
    port, _ = env
    c = registered_client(port)
    try:
        c.send_raw(b"x" * (srv.MAX_MESSAGE_SIZE + 5000))
        # Linux: nhận được ERROR. Windows: có thể bị reset trước khi nhận được.
        assert c.recv()["type"] == MsgType.ERROR
    except (ConnectionError, OSError):
        pass
    with pytest.raises((ConnectionError, OSError)):
        c.recv()  # Kết nối đã bị Server đóng
    c.close()
    registered_client(port, "PC-02").close()  # Server vẫn phục vụ Agent khác


def test_port_in_use_raises_oserror():
    s1 = srv.create_server_socket("127.0.0.1", 0)
    port = s1.getsockname()[1]
    try:
        with pytest.raises(OSError):
            srv.create_server_socket("127.0.0.1", port)
    finally:
        s1.close()


def test_load_storage_falls_back_to_null_when_no_db_module():
    assert isinstance(srv.load_storage(), srv.NullStorage)