"""Single-producer lock and heartbeat.

The corpus is idempotent, so the lock is not a correctness guard — it stops two nodes
double-loading HKEx and duplicating coverage rows. These tests pin the three behaviours
that matter: a follower stands down, the holder keeps its session connection, and a
broken lock never blocks a scrape.
"""

from __future__ import annotations

import types
from datetime import datetime, timezone

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
def fake_driver(monkeypatch):
    """Pretend the optional driver is installed.

    CI runs the unit suite without the ``postgres`` extra, so ``db_postgres.psycopg``
    is ``None`` there; the tests replace it with a stand-in module.
    """
    monkeypatch.setattr(db_postgres, "psycopg", types.SimpleNamespace(connect=None))
    monkeypatch.setattr(db_postgres, "postgres_available", lambda: True)
    monkeypatch.setattr(db_postgres, "_lock_conn", None)
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


def test_heartbeat_writes_a_timestamp_file_for_the_watchdog(monkeypatch, tmp_path):
    """The watchdog has no database client, so the phase must also reach a file."""
    target = tmp_path / "hb" / "heartbeat"
    monkeypatch.setattr(db_postgres, "SCRAPER_HEARTBEAT_FILE", str(target))
    monkeypatch.setattr(db_postgres, "postgres_available", lambda: False)

    ok, _ = db_postgres.write_heartbeat("run-1", "metadata", "7423 filings")

    assert ok is False  # no driver, but the file is still written
    body = target.read_text(encoding="utf-8").strip()
    # Epoch seconds, because existing heartbeat readers subtract it from `date +%s`.
    assert body.isdigit()
    assert abs(int(body) - int(datetime.now(timezone.utc).timestamp())) < 60


def test_heartbeat_file_is_optional(monkeypatch):
    monkeypatch.setattr(db_postgres, "SCRAPER_HEARTBEAT_FILE", "")
    monkeypatch.setattr(db_postgres, "postgres_available", lambda: False)

    ok, code = db_postgres.write_heartbeat("run-1", "start")

    assert ok is False
    assert code == db_postgres.ERR_DRIVER_MISSING


def test_prune_coverage_is_a_no_op_without_a_driver(monkeypatch):
    monkeypatch.setattr(db_postgres, "postgres_available", lambda: False)

    ok, deleted = db_postgres.prune_coverage(180)

    assert ok is False
    assert deleted == 0


def test_prune_coverage_reports_the_deleted_rows(monkeypatch):
    class _Cursor:
        rowcount = 12

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            self.sql = sql
            self.params = params

    class _Conn:
        def connection(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cursor(self):
            return _Cursor()

    monkeypatch.setattr(db_postgres, "_get_pool", lambda: _Conn())

    ok, deleted = db_postgres.prune_coverage(180)

    assert ok is True
    assert deleted == 12


def test_search_indexes_present_counts_both(monkeypatch):
    monkeypatch.setattr(db_postgres, "_fetch_scalar", lambda sql, params=None: (2, ""))
    assert db_postgres.search_indexes_present() is True

    monkeypatch.setattr(db_postgres, "_fetch_scalar", lambda sql, params=None: (1, ""))
    assert db_postgres.search_indexes_present() is False

    monkeypatch.setattr(db_postgres, "_fetch_scalar", lambda sql, params=None: (None, "42P01"))
    assert db_postgres.search_indexes_present() is False


def test_search_index_creation_is_skipped_when_already_indexed(monkeypatch):
    monkeypatch.setattr(db_postgres, "search_indexes_present", lambda: True)
    called = []
    monkeypatch.setattr(
        db_postgres, "_run", lambda sql: called.append(sql) or (True, "", 0)
    )

    ok, code = db_postgres.ensure_search_indexes()

    assert ok is True
    assert called == []  # no DDL, so no "must be owner" warning either
