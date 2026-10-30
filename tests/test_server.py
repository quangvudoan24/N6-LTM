import json
import os
import socket
import struct
import threading
from contextlib import contextmanager

import pytest

from common.constants import SERVER_HOST, SERVER_PORT
from common.protocol import (
    MsgType,
    decode,
    encode,
    make_heartbeat,
    make_metrics,
    make_register,
)
from server.server import create_server


@contextmanager
def running_server(message_handler=None):
    server = create_server("127.0.0.1", 0, message_handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def connect(server):
    sock = socket.create_connection(server.server_address, timeout=2)
    sock.settimeout(2)
    reader = sock.makefile("rb")
    return sock, reader


def read_response(reader):
    line = reader.readline()
    assert line, "server closed before returning a response"
    return decode(line.rstrip(b"\n"))


def test_server_defaults_use_shared_host_and_port():
    assert create_server.__defaults__[:2] == (SERVER_HOST, SERVER_PORT)
    assert SERVER_PORT == 9000


def test_register_heartbeat_and_metrics_are_acked_and_handed_off():
    received = []
    with running_server(lambda message, peer, received_at: received.append(
        (message, peer, received_at)
    )) as server:
        sock, reader = connect(server)
        with sock, reader:
            sock.sendall(encode(make_register("PC-01", "host", "10.0.0.1", "Windows")))
            assert read_response(reader) == {"type": MsgType.ACK, "status": "ok"}

            sock.sendall(encode(make_heartbeat("PC-01")))
            assert read_response(reader) == {"type": MsgType.ACK, "status": "ok"}

            sock.sendall(encode(make_metrics("PC-01", 10.5, 20, 99.9)))
            assert read_response(reader) == {"type": MsgType.ACK, "status": "ok"}

        assert [row[0]["type"] for row in received] == [
            MsgType.REGISTER,
            MsgType.HEARTBEAT,
            MsgType.METRICS,
        ]
        assert all(row[2] > 0 for row in received)
        assert all(row[1][0] == "127.0.0.1" for row in received)


def test_fragmented_and_concatenated_frames_are_processed_in_order():
    with running_server() as server:
        sock, reader = connect(server)
        with sock, reader:
            register = encode(make_register("A", "host", "127.0.0.1", "Windows"))
            split_at = len(register) // 2
            sock.sendall(register[:split_at])
            sock.sendall(register[split_at:])
            assert read_response(reader)["type"] == MsgType.ACK

            # Multiple protocol frames sent in one TCP write.
            sock.sendall(encode(make_heartbeat("A")) + encode(make_metrics("A", 1, 2, 3)))
            assert read_response(reader)["type"] == MsgType.ACK
            assert read_response(reader)["type"] == MsgType.ACK


def test_invalid_json_and_missing_fields_return_errors_without_losing_next_frame():
    with running_server() as server:
        sock, reader = connect(server)
        with sock, reader:
            register = encode(make_register("A", "host", "127.0.0.1", "Windows"))
            sock.sendall(register + b'{"type":"HEARTBEAT","agent_id":"A"}\n')
            assert read_response(reader)["type"] == MsgType.ACK
            assert read_response(reader)["type"] == MsgType.ERROR

            # A malformed frame followed by a valid one in the same write.
            sock.sendall(b'{"type":]\n' + encode(make_heartbeat("A")))
            error = read_response(reader)
            assert error["type"] == MsgType.ERROR
            assert "JSON" in error["message"]
            assert read_response(reader)["type"] == MsgType.ACK


def test_json_with_non_string_type_returns_error_and_connection_recovers():
    with running_server() as server:
        sock, reader = connect(server)
        with sock, reader:
            sock.sendall(b'{"type":[]}\n' + encode(
                make_register("A", "host", "127.0.0.1", "Windows")
            ))
            error = read_response(reader)
            assert error["type"] == MsgType.ERROR
            assert "type" in error["message"]
            assert read_response(reader)["type"] == MsgType.ACK


@pytest.mark.parametrize(
    "message",
    [
        {"type": MsgType.METRICS, "agent_id": "A", "cpu": 101, "ram": 1, "disk": 1, "timestamp": 1},
        {"type": MsgType.METRICS, "agent_id": "A", "cpu": True, "ram": 1, "disk": 1, "timestamp": 1},
        {"type": MsgType.HEARTBEAT, "agent_id": "A", "timestamp": -1},
        {"type": MsgType.HEARTBEAT, "agent_id": "B", "timestamp": 1},
    ],
)
def test_invalid_values_or_wrong_client_id_return_error(message):
    with running_server() as server:
        sock, reader = connect(server)
        with sock, reader:
            sock.sendall(encode(make_register("A", "host", "127.0.0.1", "Windows")))
            assert read_response(reader)["type"] == MsgType.ACK
            sock.sendall((json.dumps(message) + "\n").encode("utf-8"))
            assert read_response(reader)["type"] == MsgType.ERROR


def test_agent_must_register_before_heartbeat_but_can_recover():
    with running_server() as server:
        sock, reader = connect(server)
        with sock, reader:
            sock.sendall(encode(make_heartbeat("A")))
            assert read_response(reader)["type"] == MsgType.ERROR
            sock.sendall(encode(make_register("A", "host", "127.0.0.1", "Windows")))
            assert read_response(reader)["type"] == MsgType.ACK


def test_callback_failure_returns_error_and_does_not_ack():
    def fail_handler(message, peer, received_at):
        raise RuntimeError("database unavailable")

    with running_server(fail_handler) as server:
        sock, reader = connect(server)
        with sock, reader:
            sock.sendall(encode(make_register("A", "host", "127.0.0.1", "Windows")))
            assert read_response(reader)["type"] == MsgType.ERROR


def test_multiple_agents_are_served_concurrently():
    rendezvous = threading.Barrier(3)

    def wait_for_all_agents(message, peer, received_at):
        if message["type"] == MsgType.REGISTER:
            rendezvous.wait(timeout=3)

    with running_server(wait_for_all_agents) as server:
        errors = []

        def client(agent_id):
            try:
                sock, reader = connect(server)
                with sock, reader:
                    sock.sendall(encode(make_register(
                        agent_id, "host", "127.0.0.1", "Windows"
                    )))
                    assert read_response(reader)["type"] == MsgType.ACK
            except Exception as exc:  # surfaced in the test thread below
                errors.append(exc)

        clients = [threading.Thread(target=client, args=(f"A-{i}",)) for i in range(3)]
        for thread in clients:
            thread.start()
        for thread in clients:
            thread.join(timeout=5)

        assert not any(thread.is_alive() for thread in clients)
        assert errors == []


def test_disconnect_does_not_prevent_server_from_serving_another_client():
    with running_server() as server:
        disconnected, reader = connect(server)
        reader.close()
        disconnected.close()

        sock, reader = connect(server)
        with sock, reader:
            sock.sendall(encode(make_register("B", "host", "127.0.0.1", "Windows")))
            assert read_response(reader)["type"] == MsgType.ACK


def test_socket_reset_does_not_prevent_server_from_serving_another_client():
    with running_server() as server:
        reset_sock, reader = connect(server)
        reset_sock.sendall(encode(make_register("A", "host", "127.0.0.1", "Windows")))
        assert read_response(reader)["type"] == MsgType.ACK

        # SO_LINGER with a zero timeout closes with RST rather than FIN.
        linger = struct.pack("hh", 1, 0) if os.name == "nt" else struct.pack("ii", 1, 0)
        reset_sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
        reader.close()
        reset_sock.close()

        sock, reader = connect(server)
        with sock, reader:
            sock.sendall(encode(make_register("B", "host", "127.0.0.1", "Windows")))
            assert read_response(reader)["type"] == MsgType.ACK
