import json
import select
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
import time

import pytest

from common.constants import MsgType, Status
from common.protocol import encode, make_heartbeat, make_metrics, make_register
from database.init_db import init_db
from database.operations import (
    get_agent_status,
    get_metrics,
    handle_server_message,
    list_agents,
    register_agent,
)
from server import server as srv


class FakeClock:
    def __init__(self, value=100):
        self._value = value
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            return self._value

    def set(self, value):
        with self._lock:
            self._value = value


class BlockingClock(FakeClock):
    """Clock that can pause one call after capturing its current value."""

    def __init__(self, value=100):
        super().__init__(value)
        self.entered = threading.Event()
        self.release = threading.Event()
        self._block_next = False

    def block_next(self):
        with self._lock:
            self._block_next = True
        self.entered.clear()
        self.release.clear()

    def __call__(self):
        with self._lock:
            value = self._value
            should_block = self._block_next
            self._block_next = False
        if should_block:
            self.entered.set()
            assert self.release.wait(timeout=3)
        return value


class Client:
    def __init__(self, address):
        self.sock = socket.create_connection(address, timeout=3)
        self.sock.settimeout(3)
        self.reader = self.sock.makefile("rb")

    def send_raw(self, data):
        self.sock.sendall(data)

    def send(self, message):
        self.send_raw(encode(message))

    def receive(self):
        line = self.reader.readline()
        if not line:
            raise ConnectionError("Server closed before sending a response")
        return json.loads(line)

    def assert_no_response(self, timeout=0.1):
        readable, _, _ = select.select([self.sock], [], [], timeout)
        assert not readable, "Agent received a response where protocol expects none"

    def assert_closed(self, timeout=2):
        readable, _, _ = select.select([self.sock], [], [], timeout)
        assert readable, "Server left the stale socket open"
        with pytest.raises((ConnectionError, OSError)):
            self.receive()

    def close(self):
        try:
            self.reader.close()
        finally:
            self.sock.close()


class RunningServer:
    def __init__(self, db_path, clock, message_handler=None):
        self.db_path = db_path
        self.clock = clock
        self.listener = srv.create_server_socket("127.0.0.1", 0)
        self.address = self.listener.getsockname()
        self.state = srv.ServerState(
            db_path=db_path,
            message_handler=message_handler,
            clock=clock,
        )
        self.thread = threading.Thread(
            target=srv.serve_forever,
            args=(self.listener, self.state),
            name="test-tcp-server",
            daemon=True,
        )
        self.thread.start()
        self._closed = False

    def connect(self):
        return Client(self.address)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.state.stop_event.set()
        try:
            self.listener.close()
        except OSError:
            pass
        self.thread.join(timeout=4)
        assert not self.thread.is_alive(), "TCP accept thread did not stop"
        assert self.state.monitor_thread is not None
        assert not self.state.monitor_thread.is_alive(), "monitor thread did not stop"
        with self.state.client_lock:
            assert not self.state.client_threads, "client thread remained after shutdown"


@pytest.fixture
def running_server(tmp_path, monkeypatch):
    # Keep monitor scans deterministic; tests invoke the same scan helper with
    # explicit times instead of waiting for the five-second production period.
    monkeypatch.setattr(srv, "MONITOR_SCAN_INTERVAL", 60)
    db_path = tmp_path / "monitor.db"
    init_db(db_path)
    clock = FakeClock()
    server = RunningServer(db_path, clock)
    try:
        yield server
    finally:
        server.close()


def register(client, agent_id):
    client.send(make_register(agent_id, f"host-{agent_id}", "10.0.0.1", "Linux"))
    assert client.receive() == {"type": MsgType.ACK, "status": "ok"}


def test_real_tcp_register_heartbeat_metrics_persist_before_ack(running_server):
    client = running_server.connect()
    try:
        running_server.clock.set(100)
        register(client, "PC-01")

        agent = list_agents(db_path=running_server.db_path)[0]
        assert agent["status"] == Status.ONLINE
        assert agent["last_seen"] is None

        running_server.clock.set(105)
        client.send(make_heartbeat("PC-01"))
        client.assert_no_response()
        agent = list_agents(db_path=running_server.db_path)[0]
        assert agent["last_seen"] == 105
        assert agent["status"] == Status.ONLINE

        running_server.clock.set(106)
        client.send({
            "type": MsgType.METRICS,
            "agent_id": "PC-01",
            "cpu": 35.2,
            "ram": 61,
            "disk": 70.5,
            "timestamp": 104,
        })
        client.assert_no_response()
        assert get_metrics("PC-01", db_path=running_server.db_path) == [
            {"cpu": 35.2, "ram": 61.0, "disk": 70.5, "timestamp": 104}
        ]
        agent = list_agents(db_path=running_server.db_path)[0]
        assert agent["last_seen"] == 105
    finally:
        client.close()


def test_monitor_timeout_boundaries_and_restart_null_last_seen(running_server):
    client = running_server.connect()
    try:
        running_server.clock.set(100)
        register(client, "PC-01")

        cases = [
            (110, Status.ONLINE),
            (111, Status.WARNING),
            (115, Status.WARNING),
            (116, Status.OFFLINE),
        ]
        for timestamp, expected in cases:
            srv.monitor_once(running_server.state, timestamp)
            assert get_agent_status("PC-01", db_path=running_server.db_path) == expected

        # A new ServerState has no current registration marker. A persisted row
        # with NULL last_seen is therefore offline until it registers again.
        register_agent(
            "PC-02", "host-PC-02", "10.0.0.2", "Linux", db_path=running_server.db_path
        )
        restarted_state = srv.ServerState(
            db_path=running_server.db_path,
            clock=running_server.clock,
        )
        srv.monitor_once(restarted_state, 200)
        assert get_agent_status("PC-02", db_path=running_server.db_path) == Status.OFFLINE
    finally:
        client.close()


def test_reconnect_resets_registration_age_and_preserves_created_at(running_server):
    first_client = running_server.connect()
    register(first_client, "PC-01")
    running_server.clock.set(102)
    first_client.send(make_heartbeat("PC-01"))
    first_client.assert_no_response()
    before = list_agents(db_path=running_server.db_path)[0]

    running_server.clock.set(200)
    second_client = running_server.connect()
    try:
        register(second_client, "PC-01")
        second_generation = running_server.state.active_sessions["PC-01"]
        after = list_agents(db_path=running_server.db_path)[0]
        assert after["created_at"] == before["created_at"]
        assert after["last_seen"] == before["last_seen"] == 102
        assert after["status"] == Status.ONLINE
        assert running_server.state.registration_times["PC-01"] == 200

        srv.monitor_once(running_server.state, 211)
        assert get_agent_status("PC-01", db_path=running_server.db_path) == Status.WARNING
        srv.monitor_once(running_server.state, 216)
        assert get_agent_status("PC-01", db_path=running_server.db_path) == Status.OFFLINE
        assert list_agents(db_path=running_server.db_path)[0]["last_seen"] == 102

        # The old socket is still connected, but no longer owns this agent ID.
        running_server.clock.set(216)
        first_client.send(make_heartbeat("PC-01"))
        assert first_client.receive()["type"] == MsgType.ERROR
        assert list_agents(db_path=running_server.db_path)[0]["last_seen"] == 102
        assert get_agent_status("PC-01", db_path=running_server.db_path) == Status.OFFLINE
        assert running_server.state.active_sessions["PC-01"] == second_generation
        assert running_server.state.registration_times["PC-01"] == 200

        running_server.clock.set(217)
        second_client.send(make_heartbeat("PC-01"))
        second_client.assert_no_response()
        agent = list_agents(db_path=running_server.db_path)[0]
        assert agent["last_seen"] == 217
        assert agent["status"] == Status.ONLINE
        assert "PC-01" not in running_server.state.registration_times

        # Closing A must not retire B's generation; B remains authorized.
        first_client.close()
        assert running_server.state.active_sessions["PC-01"] == second_generation
        running_server.clock.set(218)
        second_client.send(make_heartbeat("PC-01"))
        second_client.assert_no_response()
        assert list_agents(db_path=running_server.db_path)[0]["last_seen"] == 218
    finally:
        first_client.close()
        second_client.close()
    _wait_for_condition(
        lambda: "PC-01" not in running_server.state.active_sessions,
        message="closing the current session did not retire its generation",
    )


def test_superseded_socket_cannot_register_again_or_change_database(running_server):
    old_client = running_server.connect()
    new_client = running_server.connect()
    try:
        register(old_client, "PC-01")
        running_server.clock.set(200)
        new_client.send(make_register("PC-01", "host-B", "10.0.0.2", "Linux"))
        assert new_client.receive() == {"type": MsgType.ACK, "status": "ok"}

        before = list_agents(db_path=running_server.db_path)[0]
        new_generation = running_server.state.active_sessions["PC-01"]
        new_registration_time = running_server.state.registration_times["PC-01"]

        old_client.send(make_register("PC-01", "stale-host", "10.0.0.9", "Other"))
        assert old_client.receive()["type"] == MsgType.ERROR
        old_client.assert_closed()

        assert list_agents(db_path=running_server.db_path)[0] == before
        assert running_server.state.active_sessions["PC-01"] == new_generation
        assert running_server.state.registration_times["PC-01"] == new_registration_time

        _wait_for_condition(
            lambda: len(running_server.state.client_threads) == 1,
            message="superseded REGISTER socket thread did not stop",
        )
        running_server.clock.set(201)
        new_client.send(make_heartbeat("PC-01"))
        new_client.assert_no_response()
        updated = list_agents(db_path=running_server.db_path)[0]
        assert updated["last_seen"] == 201
        assert updated["status"] == Status.ONLINE
        assert running_server.state.active_sessions["PC-01"] == new_generation
    finally:
        old_client.close()
        new_client.close()


def test_stale_heartbeat_gets_one_error_then_socket_closes(running_server):
    old_client = running_server.connect()
    new_client = running_server.connect()
    try:
        register(old_client, "PC-01")
        running_server.clock.set(101)
        old_client.send(make_heartbeat("PC-01"))
        old_client.assert_no_response()

        running_server.clock.set(200)
        new_client.send(make_register("PC-01", "host-B", "10.0.0.2", "Linux"))
        assert new_client.receive() == {"type": MsgType.ACK, "status": "ok"}
        before = list_agents(db_path=running_server.db_path)[0]
        new_generation = running_server.state.active_sessions["PC-01"]
        new_registration_time = running_server.state.registration_times["PC-01"]

        old_heartbeats = (
            encode(make_heartbeat("PC-01"))
            + encode(make_heartbeat("PC-01"))
            + encode(make_heartbeat("PC-01"))
        )
        old_client.send_raw(old_heartbeats)
        assert old_client.receive()["type"] == MsgType.ERROR
        old_client.assert_closed()

        assert list_agents(db_path=running_server.db_path)[0] == before
        assert running_server.state.active_sessions["PC-01"] == new_generation
        assert running_server.state.registration_times["PC-01"] == new_registration_time
        _wait_for_condition(
            lambda: len(running_server.state.client_threads) == 1,
            message="stale Heartbeat socket thread did not stop",
        )

        running_server.clock.set(201)
        new_client.send(make_heartbeat("PC-01"))
        new_client.assert_no_response()
        updated = list_agents(db_path=running_server.db_path)[0]
        assert updated["last_seen"] == 201
        assert updated["status"] == Status.ONLINE
        assert running_server.state.active_sessions["PC-01"] == new_generation
    finally:
        old_client.close()
        new_client.close()


def test_stale_empty_frames_close_only_stale_session(running_server):
    old_client = running_server.connect()
    new_client = running_server.connect()
    try:
        register(old_client, "PC-01")
        running_server.clock.set(200)
        new_client.send(make_register("PC-01", "host-B", "10.0.0.2", "Linux"))
        assert new_client.receive() == {"type": MsgType.ACK, "status": "ok"}

        before = list_agents(db_path=running_server.db_path)[0]
        new_generation = running_server.state.active_sessions["PC-01"]
        new_registration_time = running_server.state.registration_times["PC-01"]

        # Empty and whitespace-only lines are ignored for a current session,
        # while a stale session gets one ERROR and is closed.
        old_client.send_raw(b"\n \t\n\n")
        assert old_client.receive()["type"] == MsgType.ERROR
        old_client.assert_closed()

        assert list_agents(db_path=running_server.db_path)[0] == before
        assert running_server.state.active_sessions["PC-01"] == new_generation
        assert running_server.state.registration_times["PC-01"] == new_registration_time
        _wait_for_condition(
            lambda: len(running_server.state.client_threads) == 1,
            message="stale empty-frame client thread did not stop",
        )

        new_client.send_raw(b"\n \t\n\n")
        new_client.assert_no_response()
        running_server.clock.set(201)
        new_client.send(make_heartbeat("PC-01"))
        _wait_for_condition(
            lambda: list_agents(db_path=running_server.db_path)[0]["last_seen"] == 201,
            message="current session Heartbeat was not persisted",
        )
        new_client.assert_no_response()

        updated = list_agents(db_path=running_server.db_path)[0]
        assert updated["last_seen"] == 201
        assert updated["status"] == Status.ONLINE
        assert running_server.state.active_sessions["PC-01"] == new_generation
    finally:
        old_client.close()
        new_client.close()


@pytest.mark.parametrize(
    "bad_frame",
    [
        pytest.param(b'{"type":]\n', id="malformed-json"),
        pytest.param(
            b'{"type":"HEARTBEAT","agent_id":"PC-01","timestamp":"bad"}\n',
            id="invalid-heartbeat-timestamp",
        ),
        pytest.param(
            b'{"type":"METRICS","agent_id":"PC-01","cpu":101,"ram":1,"disk":1,"timestamp":1}\n',
            id="metrics-out-of-range",
        ),
    ],
)
def test_stale_invalid_frame_closes_once_if_replaced_during_decode(
    running_server, monkeypatch, bad_frame
):
    old_client = running_server.connect()
    new_client = running_server.connect()
    decode_entered = threading.Event()
    release_decode = threading.Event()
    frame_line = bad_frame.rstrip(b"\n")
    original_decode = srv.decode

    def delayed_decode(line):
        if line == frame_line:
            decode_entered.set()
            assert release_decode.wait(timeout=3)
        return original_decode(line)

    monkeypatch.setattr(srv, "decode", delayed_decode)
    try:
        register(old_client, "PC-01")
        old_client.send_raw(bad_frame * 3)
        assert decode_entered.wait(timeout=2)

        # Pause A after its initial generation check, let B replace it, then
        # resume decode/validation. The error path must re-check generation.
        running_server.clock.set(200)
        new_client.send(make_register("PC-01", "host-B", "10.0.0.2", "Linux"))
        assert new_client.receive() == {"type": MsgType.ACK, "status": "ok"}
        before = list_agents(db_path=running_server.db_path)[0]
        new_generation = running_server.state.active_sessions["PC-01"]
        new_registration_time = running_server.state.registration_times["PC-01"]

        release_decode.set()
        assert old_client.receive()["type"] == MsgType.ERROR
        old_client.assert_closed()

        assert list_agents(db_path=running_server.db_path)[0] == before
        assert running_server.state.active_sessions["PC-01"] == new_generation
        assert running_server.state.registration_times["PC-01"] == new_registration_time
        _wait_for_condition(
            lambda: len(running_server.state.client_threads) == 1,
            message="stale invalid-frame client thread did not stop",
        )

        running_server.clock.set(201)
        new_client.send(make_heartbeat("PC-01"))
        new_client.assert_no_response()
        updated = list_agents(db_path=running_server.db_path)[0]
        assert updated["last_seen"] == 201
        assert updated["status"] == Status.ONLINE
        assert running_server.state.active_sessions["PC-01"] == new_generation
    finally:
        release_decode.set()
        old_client.close()
        new_client.close()


def test_multiple_agents_are_saved_concurrently(running_server):
    clients = [running_server.connect() for _ in range(5)]
    try:
        for index, client in enumerate(clients):
            register(client, f"PC-{index}")

        def send_metrics(item):
            index, client = item
            client.send(make_metrics(f"PC-{index}", index, index + 1, index + 2))
            client.assert_no_response()

        with ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(send_metrics, enumerate(clients)))
        assert {agent["agent_id"] for agent in list_agents(db_path=running_server.db_path)} == {
            f"PC-{index}" for index in range(5)
        }
        assert all(get_metrics(f"PC-{index}", db_path=running_server.db_path) for index in range(5))
    finally:
        for client in clients:
            client.close()


def test_heartbeat_restores_online_and_metrics_do_not_touch_last_seen(running_server):
    client = running_server.connect()
    try:
        running_server.clock.set(100)
        register(client, "PC-01")
        running_server.clock.set(105)
        client.send(make_heartbeat("PC-01"))
        client.assert_no_response()

        srv.monitor_once(running_server.state, 116)
        assert get_agent_status("PC-01", db_path=running_server.db_path) == Status.WARNING

        running_server.clock.set(117)
        client.send(make_metrics("PC-01", 5, 6, 7))
        client.assert_no_response()
        agent = list_agents(db_path=running_server.db_path)[0]
        assert agent["last_seen"] == 105
        assert agent["status"] == Status.WARNING

        running_server.clock.set(118)
        client.send(make_heartbeat("PC-01"))
        client.assert_no_response()
        agent = list_agents(db_path=running_server.db_path)[0]
        assert agent["last_seen"] == 118
        assert agent["status"] == Status.ONLINE
    finally:
        client.close()


def test_invalid_frame_returns_error_and_next_valid_frame_is_processed(running_server):
    client = running_server.connect()
    try:
        register(client, "PC-01")
        client.send_raw(
            b'{"type":"METRICS","agent_id":"PC-01","cpu":101,"ram":1,"disk":1,"timestamp":1}\n'
            + b'{"type":]\n'
            + encode(make_heartbeat("PC-01"))
        )
        responses = [client.receive(), client.receive()]
        assert [response["type"] for response in responses] == [
            MsgType.ERROR,
            MsgType.ERROR,
        ]
        _wait_for_condition(
            lambda: list_agents(db_path=running_server.db_path)[0]["last_seen"]
            is not None,
            message="valid Heartbeat after protocol errors was not persisted",
        )
        client.assert_no_response()
        assert get_metrics("PC-01", db_path=running_server.db_path) == []
    finally:
        client.close()


def test_sqlite_failure_returns_error_without_success_ack(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "MONITOR_SCAN_INTERVAL", 60)
    db_path = tmp_path / "valid.db"
    init_db(db_path)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("file", encoding="utf-8")
    invalid_db_path = blocker / "monitor.db"
    clock = FakeClock(100)
    handler = partial(handle_server_message, db_path=invalid_db_path)
    server = RunningServer(db_path, clock, message_handler=handler)
    client = server.connect()
    try:
        client.send(make_register("PC-01", "host", "10.0.0.1", "Linux"))
        response = client.receive()
        assert response["type"] == MsgType.ERROR
        assert response.get("status") != "ok"
        assert list_agents(db_path=db_path) == []
    finally:
        client.close()
        server.close()


def test_shutdown_stops_monitor_accept_and_active_client_threads(running_server):
    client = running_server.connect()
    register(client, "PC-01")
    monitor_thread = running_server.state.monitor_thread
    running_server.close()
    assert not running_server.thread.is_alive()
    assert monitor_thread is not None and not monitor_thread.is_alive()
    with running_server.state.client_lock:
        assert not running_server.state.client_threads
    client.close()


def _wait_for_condition(predicate, timeout=2, message="condition was not reached in time"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), message


def test_delayed_clock_callback_does_not_commit_heartbeat_timestamp_out_of_order(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(srv, "MONITOR_SCAN_INTERVAL", 60)
    db_path = tmp_path / "monitor.db"
    init_db(db_path)
    clock = BlockingClock(100)
    server = RunningServer(db_path, clock)
    old_client = server.connect()
    new_client = None
    try:
        register(old_client, "PC-01")
        clock.set(110)
        clock.block_next()
        old_client.send(make_heartbeat("PC-01"))
        assert clock.entered.wait(timeout=2)

        # A new REGISTER must wait behind the in-flight clock/callback. It
        # cannot commit a later heartbeat before the older timestamp.
        new_client = server.connect()
        clock.set(120)
        new_client.send(make_register("PC-01", "new-host", "10.0.0.2", "Linux"))
        new_client.assert_no_response(timeout=0.2)

        clock.release.set()
        assert new_client.receive() == {"type": MsgType.ACK, "status": "ok"}
        assert list_agents(db_path=db_path)[0]["last_seen"] == 110

        clock.set(121)
        new_client.send(make_heartbeat("PC-01"))
        new_client.assert_no_response()
        assert list_agents(db_path=db_path)[0]["last_seen"] == 121
    finally:
        clock.release.set()
        old_client.close()
        if new_client is not None:
            new_client.close()
        server.close()


def test_tcp_fragmented_and_concatenated_frames_and_ack_contract(running_server):
    client = running_server.connect()
    try:
        register(client, "PC-01")

        heartbeat = encode(make_heartbeat("PC-01"))
        split_at = len(heartbeat) // 2
        client.send_raw(heartbeat[:split_at])
        assert list_agents(db_path=running_server.db_path)[0]["last_seen"] is None

        # Complete the fragmented heartbeat, then send a metrics frame and a
        # malformed frame in the same write. Only the malformed frame responds.
        running_server.clock.set(105)
        client.send_raw(
            heartbeat[split_at:]
            + encode({
                "type": MsgType.METRICS,
                "agent_id": "PC-01",
                "cpu": 1,
                "ram": 2,
                "disk": 3,
                "timestamp": 100,
            })
            + b'{"type":]\n'
        )
        assert client.receive()["type"] == MsgType.ERROR
        client.assert_no_response()

        agent = list_agents(db_path=running_server.db_path)[0]
        assert agent["last_seen"] == 105
        assert get_metrics("PC-01", db_path=running_server.db_path) == [
            {"cpu": 1, "ram": 2, "disk": 3, "timestamp": 100}
        ]
    finally:
        client.close()


def test_disconnect_before_first_heartbeat_cleans_only_current_registration(
    running_server,
):
    old_client = running_server.connect()
    register(old_client, "PC-01")
    new_client = running_server.connect()
    try:
        running_server.clock.set(200)
        register(new_client, "PC-01")
        new_generation = running_server.state.active_sessions["PC-01"]

        old_client.close()
        _wait_for_condition(
            lambda: len(running_server.state.client_threads) == 1,
            message="old client cleanup did not run",
        )
        assert running_server.state.active_sessions["PC-01"] == new_generation
        assert running_server.state.registration_times["PC-01"] == 200

        new_client.close()
        _wait_for_condition(
            lambda: "PC-01" not in running_server.state.active_sessions,
            message="current client cleanup did not retire its generation",
        )
        assert "PC-01" not in running_server.state.registration_times
        srv.monitor_once(running_server.state, 201)
        assert get_agent_status("PC-01", db_path=running_server.db_path) == Status.OFFLINE
    finally:
        old_client.close()
        new_client.close()
