import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest

from common.constants import MsgType, Status
from database.init_db import init_db
from database.operations import (
    DatabaseError,
    get_agent_status,
    get_metrics,
    handle_server_message,
    list_agents,
    register_agent,
    save_metrics,
    update_agent,
)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "monitor.db"
    init_db(path)
    return path


def test_init_db_is_idempotent_and_creates_tables_and_constraints(db_path):
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as connection, connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {"agents", "metrics", "events"}.issubset(tables)

        agent_columns = {
            row[1]: row for row in connection.execute("PRAGMA table_info(agents)")
        }
        assert agent_columns["agent_id"][5] == 1
        assert agent_columns["created_at"][3] == 1
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint"):
            connection.execute(
                """
                INSERT INTO agents (
                    agent_id, hostname, status, created_at
                ) VALUES ('bad-status', 'host', 'UNKNOWN', 1)
                """
            )

        metric_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(metrics)"
        ).fetchall()
        assert any(
            row[2] == "agents" and row[3] == "agent_id"
            for row in metric_foreign_keys
        )


def test_register_agent_leaves_last_seen_null_and_duplicate_preserves_heartbeat(db_path):
    register_agent("PC-01", "first-host", "10.0.0.1", "Windows", db_path=db_path)
    first = list_agents(db_path=db_path)[0]
    assert first["last_seen"] is None

    update_agent("PC-01", last_seen=100, db_path=db_path)

    register_agent(
        "PC-01", "updated-host", "10.0.0.2", "Linux",
        status=Status.WARNING, db_path=db_path,
    )
    agents = list_agents(db_path=db_path)

    assert len(agents) == 1
    assert agents[0]["hostname"] == "updated-host"
    assert agents[0]["ip_address"] == "10.0.0.2"
    assert agents[0]["os"] == "Linux"
    assert agents[0]["status"] == Status.WARNING
    assert agents[0]["last_seen"] == 100
    assert agents[0]["created_at"] == first["created_at"]


def test_update_agent_changes_only_provided_fields_and_reports_unknown_agent(db_path):
    register_agent("PC-01", "host", "10.0.0.1", "Windows", db_path=db_path)

    update_agent("PC-01", hostname="new-host", status=Status.OFFLINE,
                 last_seen=100, db_path=db_path)
    agent = list_agents(db_path=db_path)[0]
    assert agent["hostname"] == "new-host"
    assert agent["ip_address"] == "10.0.0.1"
    assert agent["status"] == Status.OFFLINE
    assert agent["last_seen"] == 100

    with pytest.raises(LookupError):
        update_agent("missing", hostname="host", db_path=db_path)
    with pytest.raises(ValueError):
        update_agent("PC-01", db_path=db_path)


def test_list_and_status_queries_filter_devices(db_path):
    register_agent("A", "alpha", "10.0.0.1", "Windows", db_path=db_path)
    register_agent("B", "beta", "10.0.0.2", "Linux", status=Status.WARNING,
                   db_path=db_path)

    assert [agent["agent_id"] for agent in list_agents(db_path=db_path)] == ["A", "B"]
    assert [agent["agent_id"] for agent in list_agents(Status.WARNING, db_path=db_path)] == ["B"]
    assert get_agent_status("A", db_path=db_path) == Status.ONLINE
    assert get_agent_status("missing", db_path=db_path) is None


def test_save_metrics_and_query_history_newest_first_with_limit(db_path):
    register_agent("A", "host", "10.0.0.1", "Windows", db_path=db_path)
    save_metrics("A", 10, 20.5, 30, 100, db_path=db_path)
    save_metrics("A", 11, 21.5, 31, 101, db_path=db_path)
    save_metrics("A", 12, 22.5, 32, 102, db_path=db_path)

    history = get_metrics("A", limit=2, db_path=db_path)
    assert history == [
        {"cpu": 12, "ram": 22.5, "disk": 32, "timestamp": 102},
        {"cpu": 11, "ram": 21.5, "disk": 31, "timestamp": 101},
    ]


@pytest.mark.parametrize("value", [-1, 100.1, True, float("nan"), float("inf"), "50"])
def test_save_metrics_rejects_invalid_resource_values(db_path, value):
    with pytest.raises(ValueError):
        save_metrics("A", value, 50, 50, 100, db_path=db_path)


def test_operations_reject_invalid_status_timestamp_and_identifiers(db_path):
    with pytest.raises(ValueError):
        register_agent(" ", "host", "10.0.0.1", "Windows", db_path=db_path)
    with pytest.raises(ValueError):
        register_agent("A", "host", "10.0.0.1", "Windows", status="UNKNOWN",
                       db_path=db_path)
    with pytest.raises(ValueError):
        save_metrics("A", 1, 2, 3, True, db_path=db_path)
    with pytest.raises(ValueError):
        get_metrics("A", limit=0, db_path=db_path)


def test_timestamps_outside_sqlite_integer_range_are_rejected(db_path):
    too_large = 1 << 63

    with pytest.raises(ValueError):
        save_metrics("A", 1, 2, 3, too_large, db_path=db_path)
    with pytest.raises(ValueError):
        update_agent("A", last_seen=too_large, db_path=db_path)
    with pytest.raises(ValueError):
        handle_server_message(
            {"type": MsgType.REGISTER, "agent_id": "A", "hostname": "host",
             "ip": "10.0.0.1", "os": "Windows"},
            ("10.0.0.1", 5000), too_large, db_path=db_path,
        )
    with pytest.raises(ValueError):
        handle_server_message(
            {"type": MsgType.METRICS, "agent_id": "A", "cpu": 1, "ram": 2,
             "disk": 3, "timestamp": too_large},
            ("10.0.0.1", 5000), 100, db_path=db_path,
        )


def test_maximum_sqlite_integer_timestamp_is_accepted(db_path):
    max_timestamp = (1 << 63) - 1
    register_agent("A", "host", "10.0.0.1", "Windows", db_path=db_path)

    update_agent("A", last_seen=max_timestamp, db_path=db_path)
    save_metrics("A", 1, 2, 3, max_timestamp, db_path=db_path)

    assert list_agents(db_path=db_path)[0]["last_seen"] == max_timestamp
    assert get_metrics("A", db_path=db_path)[0]["timestamp"] == max_timestamp


def test_foreign_key_errors_are_reported_for_metrics_without_agent(db_path):
    with pytest.raises(DatabaseError, match="FOREIGN KEY"):
        save_metrics("missing", 1, 2, 3, 100, db_path=db_path)


def test_server_callback_stores_register_metrics_and_heartbeat_time(db_path):
    handle_server_message(
        {"type": MsgType.REGISTER, "agent_id": "A", "hostname": "host",
         "ip": "10.0.0.1", "os": "Windows"},
        ("10.0.0.1", 5000), 100, db_path=db_path,
    )
    assert list_agents(db_path=db_path)[0]["last_seen"] is None

    handle_server_message(
        {"type": MsgType.HEARTBEAT, "agent_id": "A", "timestamp": 101},
        ("10.0.0.1", 5000), 102, db_path=db_path,
    )
    assert list_agents(db_path=db_path)[0]["last_seen"] == 102

    handle_server_message(
        {"type": MsgType.METRICS, "agent_id": "A", "cpu": 1, "ram": 2,
         "disk": 3, "timestamp": 103},
        ("10.0.0.1", 5000), 104, db_path=db_path,
    )

    assert list_agents(db_path=db_path)[0]["last_seen"] == 102
    assert get_agent_status("A", db_path=db_path) == Status.ONLINE
    assert get_metrics("A", db_path=db_path) == [
        {"cpu": 1, "ram": 2, "disk": 3, "timestamp": 103}
    ]


def test_server_callback_rejects_unknown_message_type(db_path):
    with pytest.raises(ValueError, match="không hỗ trợ"):
        handle_server_message({"type": "OTHER"}, ("127.0.0.1", 1), 100,
                              db_path=db_path)


def test_records_persist_after_database_reinitialization(db_path):
    register_agent("A", "host", "10.0.0.1", "Windows", db_path=db_path)
    save_metrics("A", 10, 20, 30, 100, db_path=db_path)

    init_db(db_path)

    assert list_agents(db_path=db_path)[0]["agent_id"] == "A"
    assert get_metrics("A", db_path=db_path)[0]["timestamp"] == 100


def test_database_errors_are_wrapped_and_later_operations_still_work(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("file", encoding="utf-8")
    invalid_db_path = blocker / "monitor.db"

    with pytest.raises(DatabaseError):
        list_agents(db_path=invalid_db_path)
    with pytest.raises(DatabaseError):
        init_db(invalid_db_path)

    valid_db_path = tmp_path / "valid.db"
    init_db(valid_db_path)
    assert list_agents(db_path=valid_db_path) == []


def test_independent_connections_allow_concurrent_agent_writes(db_path):
    def write_agent(index):
        agent_id = f"A-{index}"
        register_agent(agent_id, f"host-{index}", "10.0.0.1", "Windows",
                       db_path=db_path)
        save_metrics(agent_id, index, index, index, index, db_path=db_path)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(write_agent, range(8)))

    assert len(list_agents(db_path=db_path)) == 8
    assert sum(len(get_metrics(f"A-{i}", db_path=db_path)) for i in range(8)) == 8
