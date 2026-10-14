import pytest
from common.protocol import (encode, decode, LineBuffer, ProtocolError,
                             make_heartbeat, make_metrics, make_register)


def test_roundtrip():
    msg = make_heartbeat("PC-01")
    assert decode(encode(msg).strip()) == msg


def test_missing_field():
    with pytest.raises(ProtocolError):
        encode({"type": "HEARTBEAT", "agent_id": "PC-01"})


def test_unknown_type():
    with pytest.raises(ProtocolError):
        decode(b'{"type": "XYZ"}')


def test_two_messages_stuck_together():
    data = encode(make_heartbeat("A")) + encode(make_metrics("A", 1.0, 2.0, 3.0))
    assert len(LineBuffer().feed(data)) == 2


def test_message_split_in_half():
    raw = encode(make_register("A", "h", "1.1.1.1", "Win"))
    buf = LineBuffer()
    assert buf.feed(raw[:10]) == []
    assert len(buf.feed(raw[10:])) == 1
