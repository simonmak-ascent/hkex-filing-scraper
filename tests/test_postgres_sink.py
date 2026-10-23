"""Unit tests for the PostgreSQL sink module (``db_postgres``).

Pure unit tests — no database or network required. The generated DDL and SQL
are asserted directly, and the write control flow is exercised with the driver
availability and connection helpers monkeypatched.
"""

from __future__ import annotations

from hkex_scraper import db_postgres, pipeline
from hkex_scraper.sinks.base import Sink


# ---------------------------------------------------------------------------
# AC-2: idempotent schema DDL
# ---------------------------------------------------------------------------


class TestSchemaDdl:
    def test_schema_ddl_idempotent(self):
        statements = db_postgres._build_postgres_schema_sql()
        assert statements
        for stmt in statements:
            assert "IF NOT EXISTS" in stmt, stmt[:120]
        joined = "\n".join(statements)
        for table in (
            "exchange_filing",
            "scrape_coverage",
            "has_filing",
            "references_filing",
        ):
            assert f"TABLE IF NOT EXISTS {table}" in joined
        for column in ("filing_id", "document_text", "document_tables", "company_ticker"):
            assert column in joined
        for index in (
            "idx_pg_ef_ticker",
            "idx_pg_ef_docstatus",
            "idx_pg_ef_doc_tables",
            "idx_pg_cov_run",
        ):
            assert index in joined
        # exchange_filing must be created before the FK-bearing edge tables.
        assert joined.index("TABLE IF NOT EXISTS exchange_filing") < joined.index(
            "REFERENCES exchange_filing"
        )


class TestSearchIndexes:
    def test_index_sql_is_idempotent_and_trigram(self):
        statements = db_postgres._build_search_index_sql("public")
        joined = "\n".join(statements)
        assert "public.gin_trgm_ops" in joined
        assert "lower(document_text)" in joined
        assert "lower(title)" in joined
        for statement in statements:
            assert "IF NOT EXISTS" in statement
        assert db_postgres._SEARCH_EXTENSION_SQL == "CREATE EXTENSION IF NOT EXISTS pg_trgm"

    def test_index_sql_qualifies_a_custom_extension_schema(self):
        statements = db_postgres._build_search_index_sql("hkex")
        assert all("hkex.gin_trgm_ops" in statement for statement in statements)

    def test_ensure_search_indexes_can_be_disabled(self, monkeypatch):
        monkeypatch.setattr(db_postgres, "POSTGRES_FTS_INDEX", False)
        assert db_postgres.ensure_search_indexes() == (True, "")

    def test_ensure_search_indexes_degrades_gracefully(self, monkeypatch):
        monkeypatch.setattr(db_postgres, "POSTGRES_FTS_INDEX", True)
        monkeypatch.setattr(db_postgres, "_PSYCOPG_AVAILABLE", True)
        monkeypatch.setattr(db_postgres, "postgres_conninfo", lambda: "postgresql://x")
        monkeypatch.setattr(
            db_postgres, "_run", lambda *a, **k: (False, "insufficient_privilege", 0)
        )
        ok, code = db_postgres.ensure_search_indexes()
        assert ok is False
        assert "insufficient_privilege" in code

    def test_schema_init_is_unaffected_by_index_failure(self, monkeypatch):
        from hkex_scraper.sinks.postgres import PostgresSink

        monkeypatch.setattr(db_postgres, "initialize_postgres_schema", lambda: (True, ""))
        monkeypatch.setattr(db_postgres, "ensure_search_indexes", lambda: (False, "PERM"))
        assert PostgresSink().ensure_schema() == (True, "")


# ---------------------------------------------------------------------------
# Filing metadata upsert
# ---------------------------------------------------------------------------


class TestUpsertFilings:
    def test_upsert_filings_sql_and_binds(self):
        sql = db_postgres._build_upsert_filings_sql()
        assert "INSERT INTO exchange_filing" in sql
        assert "ON CONFLICT (filing_id) DO UPDATE" in sql
        # Metadata upsert must NOT touch document payload columns.
        assert "document_text" not in sql
        assert "document_tables" not in sql
        assert sql.count("%s") == len(db_postgres._FILING_COLUMNS)

    def test_filing_row_shape(self):
        row = db_postgres._filing_row(
            {
                "filing_id": "abc123",
                "company_ticker": "0451.HK",
                "stock_code": "0451",
                "filing_type": "Annual Report",
            }
        )
        assert len(row) == len(db_postgres._FILING_COLUMNS)
        assert row[0] == "abc123"
        assert row[1] == "0451.HK"
        assert row[4] == "HK"
        assert row[12] == "HKEx"


# ---------------------------------------------------------------------------
# Document payload upsert
# ---------------------------------------------------------------------------


class TestUpsertDocument:
    def test_upsert_document_sql_and_truncation(self, monkeypatch):
        sql = db_postgres._build_upsert_document_sql()
        for column in db_postgres._DOCUMENT_COLUMNS:
            assert f"{column} = %s" in sql
        assert "WHERE filing_id = %s" in sql

        captured = {}

        def fake_run(statement, params=None, many=False):
            captured["sql"] = statement
            captured["params"] = params
            return True, db_postgres.ERR_NONE, 1

        monkeypatch.setattr(db_postgres, "_PSYCOPG_AVAILABLE", True)
        monkeypatch.setattr(db_postgres, "postgres_conninfo", lambda: "host=x")
        monkeypatch.setattr(db_postgres, "_run", fake_run)

        truncated = "head-of-text"
        ok, code = db_postgres.upsert_document(
            "fid1",
            {
                "document_size": 10,
                "document_type": "pdf",
                "document_hash": "h",
                "document_sha256": "s" * 64,
                "document_text": truncated,
                "document_text_len": len(truncated),
                "document_tables": [{"tableIndex": 0}],
                "document_table_cnt": 1,
                "document_status": "processed",
                "document_status_reason": "",
            },
        )
        assert ok is True
        assert code == db_postgres.ERR_NONE
        # The caller's payload is passed through unchanged; look the columns up by name so
        # adding a column does not silently shift the assertions.
        column = db_postgres._DOCUMENT_COLUMNS.index
        assert captured["params"][column("document_text")] == truncated
        assert captured["params"][column("document_text_len")] == len(truncated)
        assert captured["params"][column("document_sha256")] == "s" * 64

    def test_upsert_document_missing_row_is_failure(self, monkeypatch):
        monkeypatch.setattr(db_postgres, "_PSYCOPG_AVAILABLE", True)
        monkeypatch.setattr(db_postgres, "postgres_conninfo", lambda: "host=x")
        monkeypatch.setattr(db_postgres, "_run", lambda *a, **k: (True, db_postgres.ERR_NONE, 0))
        ok, code = db_postgres.upsert_document("missing", {"document_status": "processed"})
        assert ok is False
        assert code == db_postgres.ERR_WRITE_ERROR


# ---------------------------------------------------------------------------
# Coverage upsert
# ---------------------------------------------------------------------------


class TestInsertCoverage:
    def test_insert_coverage_conflict_key(self):
        sql = db_postgres._build_insert_coverage_sql()
        assert "INSERT INTO scrape_coverage" in sql
        assert "ON CONFLICT (chunk_from, chunk_to, run_id) DO UPDATE" in sql
        assert sql.count("%s") == len(db_postgres._COVERAGE_COLUMNS)

    def test_coverage_row_shape(self):
        row = db_postgres._coverage_row(
            {
                "chunk_from": 1,
                "chunk_to": 2,
                "api_count": 3,
                "ingested_count": 4,
                "unique_count": 5,
                "run_id": "run",
            }
        )
        assert row[:6] == (1, 2, 3, 4, 5, "run")


# ---------------------------------------------------------------------------
# Graph edges
# ---------------------------------------------------------------------------


class TestUpsertEdges:
    def test_upsert_edges_conflict_noop(self):
        has_filing_sql = db_postgres._build_upsert_edges_sql("has_filing")
        assert "INSERT INTO has_filing" in has_filing_sql
        assert "ON CONFLICT (company_id, filing_id) DO NOTHING" in has_filing_sql

        ref_sql = db_postgres._build_upsert_edges_sql("references_filing")
        assert "INSERT INTO references_filing" in ref_sql
        assert "ON CONFLICT (filing_id, company_id) DO NOTHING" in ref_sql

    def test_unknown_edge_kind_is_payload_error(self, monkeypatch):
        monkeypatch.setattr(db_postgres, "_PSYCOPG_AVAILABLE", True)
        monkeypatch.setattr(db_postgres, "postgres_conninfo", lambda: "host=x")
        created, code = db_postgres.upsert_edges(
            [{"company_id": "1_HK", "filing_id": "f"}], "bogus"
        )
        assert created == 0
        assert code == db_postgres.ERR_PAYLOAD_ERROR

    def test_count_edges_unknown_kind_is_payload_error(self):
        # Validated before any database access, so no driver is needed.
        count, code = db_postgres.count_edges("bogus")
        assert count == 0
        assert code == db_postgres.ERR_PAYLOAD_ERROR


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    def test_graceful_degradation_driver_missing(self, monkeypatch):
        monkeypatch.setattr(db_postgres, "_PSYCOPG_AVAILABLE", False)
        assert db_postgres.postgres_available() is False
        assert db_postgres.upsert_filings([{"filing_id": "x"}]) == (
            0,
            db_postgres.ERR_DRIVER_MISSING,
        )
        ok, code = db_postgres.initialize_postgres_schema()
        assert ok is False
        assert code == db_postgres.ERR_DRIVER_MISSING
        ok, code = db_postgres.upsert_document("x", {})
        assert ok is False
        assert code == db_postgres.ERR_DRIVER_MISSING
        # No exception raised anywhere above.

    def test_existing_schema_tolerates_ddl_that_needs_ownership(self, monkeypatch):
        # RDS restore left exchange_filing owned by svc_admin; the producer has
        # DML only, so CREATE INDEX fails even with IF NOT EXISTS. The schema is
        # already there, so the scrape must proceed.
        monkeypatch.setattr(db_postgres, "_PSYCOPG_AVAILABLE", True)
        monkeypatch.setattr(db_postgres, "postgres_conninfo", lambda: "postgresql://x")

        def fake_run(sql, params=None, many=False):
            if sql.strip().startswith("SELECT 1 FROM exchange_filing"):
                return True, "", 0
            return False, "must be owner of table exchange_filing", 0

        monkeypatch.setattr(db_postgres, "_run", fake_run)
        ok, code = db_postgres.initialize_postgres_schema()
        assert (ok, code) == (True, db_postgres.ERR_NONE)

    def test_missing_schema_still_fails_on_ddl_error(self, monkeypatch):
        monkeypatch.setattr(db_postgres, "_PSYCOPG_AVAILABLE", True)
        monkeypatch.setattr(db_postgres, "postgres_conninfo", lambda: "postgresql://x")

        def fake_run(sql, params=None, many=False):
            if sql.strip().startswith("SELECT 1 FROM exchange_filing"):
                return False, "relation exchange_filing does not exist", 0
            return False, "must be owner of table exchange_filing", 0

        monkeypatch.setattr(db_postgres, "_run", fake_run)
        ok, code = db_postgres.initialize_postgres_schema()
        assert ok is False
        assert code == "must be owner of table exchange_filing"

    def test_dsn_missing_degrades(self, monkeypatch):
        monkeypatch.setattr(db_postgres, "_PSYCOPG_AVAILABLE", True)
        monkeypatch.setattr(db_postgres, "postgres_conninfo", lambda: "")
        assert db_postgres.postgres_available() is False
        assert db_postgres.upsert_filings([{"filing_id": "x"}]) == (
            0,
            db_postgres.ERR_DSN_MISSING,
        )
        ok, code = db_postgres.initialize_postgres_schema()
        assert ok is False
        assert code == db_postgres.ERR_DSN_MISSING


# ---------------------------------------------------------------------------
# Credentials never logged
# ---------------------------------------------------------------------------


class TestCredentialRedaction:
    def test_dsn_never_logged(self):
        redacted = db_postgres._redact(
            "connection failed: host=db password=supersecret123 user=simon"
        )
        assert "supersecret123" not in redacted
        assert "***" in redacted

    def test_url_credentials_redacted(self):
        redacted = db_postgres._redact(
            "could not connect to postgresql://simon:topsecret@db.local:5432/hkex"
        )
        assert "topsecret" not in redacted
        assert "***" in redacted


# ---------------------------------------------------------------------------
# Sink adapter
# ---------------------------------------------------------------------------


class TestPostgresSinkAdapter:
    def test_satisfies_contract_and_delegates(self):
        from hkex_scraper.sinks.postgres import PostgresSink

        sink = PostgresSink()
        assert isinstance(sink, Sink)
        assert sink.id == "postgres"
        assert sink.capabilities.arrays is True
        assert sink.capabilities.reads is True

    def test_unavailable_reason_names_driver_or_settings(self, monkeypatch):
        from hkex_scraper.sinks.postgres import PostgresSink

        sink = PostgresSink()
        monkeypatch.setattr(db_postgres, "driver_installed", lambda: False)
        assert "psycopg" in sink.unavailable_reason()
        monkeypatch.setattr(db_postgres, "driver_installed", lambda: True)
        assert "POSTGRES_DSN" in sink.unavailable_reason()


def test_filing_record_is_canonical():
    record = pipeline._filing_record(
        {
            "stockCode": "0451",
            "date": "01/07/2024",
            "title": "Annual Report 2024",
            "stockName": "Example Holdings",
            "link": "https://www1.hkexnews.hk/example.pdf",
        }
    )
    assert record["filing_id"] == pipeline.filing_id_for(
        {
            "stockCode": "0451",
            "date": "01/07/2024",
            "title": "Annual Report 2024",
        }
    )
    assert record["company_ticker"] == "0451.HK"
    assert record["filing_type"] == "Annual Report"
    assert record["exchange"] == "HK"
