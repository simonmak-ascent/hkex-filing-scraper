"""Single-producer lock and heartbeat.

The corpus is idempotent, so the lock is not a correctness guard — it stops two nodes
double-loading HKEx and duplicating coverage rows. These tests pin the three behaviours
that matter: a follower stands down, the holder keeps its session connection, and a
broken lock never blocks a scrape.
"""

from __future__ import annotations

import pytest

from hkex_scraper import db_postgres


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None


class FakeConnection:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.closed = False
        self.cursors = []

    def cursor(self):
        cursor = FakeCursor(self.results)
        self.cursors.append(cursor)
        return cursor

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def reset_lock_state(monkeypatch):
    monkeypatch.setattr(db_postgres, "_lock_conn", None)
    monkeypatch.setattr(db_postgres, "postgres_available", lambda: True)
    yield
    monkeypatch.setattr(db_postgres, "_lock_conn", None)


def test_acquires_and_holds_the_connection(monkeypatch):
    conn = FakeConnection(results=[(True,)])
    monkeypatch.setattr(db_postgres.psycopg, "connect", lambda dsn, autocommit=False: conn)

    held, detail = db_postgres.try_acquire_scrape_lock()

    assert held is True
    assert detail == "locked"
    assert db_postgres._lock_conn is conn
    assert conn.cursors[0].executed[0][1] == (db_postgres.SCRAPE_LOCK_KEY,)


def test_a_second_caller_is_told_it_already_holds_it(monkeypatch):
    conn = FakeConnection(results=[(True,)])
    monkeypatch.setattr(db_postgres.psycopg, "connect", lambda dsn, autocommit=False: conn)

    db_postgres.try_acquire_scrape_lock()
    held, detail = db_postgres.try_acquire_scrape_lock()

    assert held is True
    assert "already held" in detail


def test_follower_stands_down_and_closes_its_connection(monkeypatch):
    conn = FakeConnection(results=[(False,)])
    monkeypatch.setattr(db_postgres.psycopg, "connect", lambda dsn, autocommit=False: conn)

    held, detail = db_postgres.try_acquire_scrape_lock()

    assert held is False
    assert "another producer" in detail
    assert conn.closed is True
    assert db_postgres._lock_conn is None


def test_a_broken_lock_never_blocks_a_scrape(monkeypatch):
    def boom(dsn, autocommit=False):
        raise OSError("database is on fire")

    monkeypatch.setattr(db_postgres.psycopg, "connect", boom)

    held, detail = db_postgres.try_acquire_scrape_lock()

    assert held is True
    assert "unavailable" in detail


def test_release_unlocks_and_forgets_the_connection(monkeypatch):
    conn = FakeConnection(results=[(True,), (True,)])
    monkeypatch.setattr(db_postgres.psycopg, "connect", lambda dsn, autocommit=False: conn)
    db_postgres.try_acquire_scrape_lock()

    db_postgres.release_scrape_lock()

    assert db_postgres._lock_conn is None
    assert conn.closed is True
    assert any("pg_advisory_unlock" in sql for sql, _ in conn.cursors[-1].executed)


def test_release_is_idempotent():
    db_postgres.release_scrape_lock()
    db_postgres.release_scrape_lock()


def test_heartbeat_reports_a_missing_driver(monkeypatch):
    monkeypatch.setattr(db_postgres, "postgres_available", lambda: False)

    ok, code = db_postgres.write_heartbeat("run-1", "start")

    assert ok is False
    assert code == db_postgres.ERR_DRIVER_MISSING
