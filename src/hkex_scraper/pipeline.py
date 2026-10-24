"""Pipeline orchestration: Phase 1 (metadata scrape) and Phase 2 (document backfill).

The pipeline builds canonical records once and hands them to every configured
sink. It never branches on a destination name.
"""

from __future__ import annotations

import hashlib
import io
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from . import http, sinks
from .api import REQUESTS_AVAILABLE, fetch_chunk_via_api, generate_monthly_chunks
from .config import MAX_DOWNLOAD_SIZE, MAX_DOWNLOAD_WORKERS
from .extractor import extract_content_with_tables
from .sinks import Sink
from .utils import (
    classify_filing,
    extract_issuer_name,
    extract_referenced_tickers,
    is_derivative_issuer_filing,
    log,
    squash_ws,
)

# ---------------------------------------------------------------------------
# Supported document extensions
# ---------------------------------------------------------------------------
SUPPORTED_EXTENSIONS = (".pdf", ".htm", ".html", ".xlsx", ".xls", ".doc", ".docx")


# ---------------------------------------------------------------------------
# Per-sink write accounting
# ---------------------------------------------------------------------------
SINK_STATS: Dict[str, Dict[str, int]] = {}
# Sink ids already warned about for being unavailable.
_UNAVAILABLE_WARNED: set[str] = set()


def reset_sink_stats() -> None:
    """Reset per-sink counters (called once at the start of a run)."""
    SINK_STATS.clear()
    _UNAVAILABLE_WARNED.clear()
    for sink in sinks.enabled_sinks():
        SINK_STATS.setdefault(sink.id, {"ok": 0, "failed": 0})


def record_sink(sink_id: str, ok: bool, count: int = 1) -> None:
    bucket = SINK_STATS.setdefault(sink_id, {"ok": 0, "failed": 0})
    if ok:
        bucket["ok"] += count
    else:
        bucket["failed"] += count


def warn_sink_unavailable_once(sink: Sink) -> None:
    """Log one actionable warning per unavailable sink."""
    if sink.id in _UNAVAILABLE_WARNED:
        return
    _UNAVAILABLE_WARNED.add(sink.id)
    log(f"  {sink.unavailable_reason()}; skipping {sink.id} writes")


def sink_exit_code() -> int:
    """Non-zero when any configured sink recorded at least one failure."""
    if any(stats["failed"] > 0 for stats in SINK_STATS.values()):
        return 1
    return 0


def log_sink_summary() -> None:
    """Log a per-sink success/failure summary for the completion banner."""
    parts = []
    for name, stats in SINK_STATS.items():
        if stats["ok"] or stats["failed"]:
            parts.append(f"{name}: {stats['ok']} ok / {stats['failed']} failed")
    if parts:
        log("Sink writes: " + "; ".join(parts))


def _configured_sinks() -> List[Sink]:
    return sinks.enabled_sinks()


def _parse_filing_date(date_str: str):
    """Parse a ``DD/MM/YYYY`` API date into a ``datetime`` (or ``None``)."""
    if not date_str:
        return None
    try:
        dd, mm, yyyy = date_str.split("/")
        return datetime(int(yyyy), int(mm), int(dd))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Document download helpers
# ---------------------------------------------------------------------------


def _download_document(url: str, filing_id: str) -> Tuple[bytes, int, str]:
    """Download a document if <= MAX_DOWNLOAD_SIZE.

    Returns ``(raw_bytes, size_bytes, skip_reason)``.
    """
    if not url:
        return b"", 0, "no_url"
    u = url.lower().split("?")[0].split("#")[0]
    if not any(u.endswith(ext) for ext in SUPPORTED_EXTENSIONS):
        return b"", 0, "unsupported_type"
    http.pace()
    try:
        with http.get_session().get(
            url,
            headers={
                "Accept": (
                    "application/pdf,text/html,"
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
                    "*/*;q=0.8"
                )
            },
            timeout=(10, 60),
            stream=True,
        ) as response:
            if response.status_code >= 400:
                log(f"  Download error for {filing_id}: HTTP {response.status_code}")
                return b"", 0, f"http_{response.status_code}"

            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_DOWNLOAD_SIZE:
                return b"", 0, "too_large"

            # Stream so a document that grows past the cap never fills memory.
            buffer = bytearray()
            for block in response.iter_content(256 * 1024):
                if not block:
                    continue
                buffer.extend(block)
                if len(buffer) > MAX_DOWNLOAD_SIZE:
                    return b"", 0, "too_large"
            content = bytes(buffer)
            return content, len(content), ""
    except Exception as e:
        log(f"  Download error for {filing_id}: {type(e).__name__}: {e}")
        return b"", 0, f"error:{type(e).__name__}"


def _download_worker(args: Tuple[str, str]) -> Tuple[str, str, bytes, int, str]:
    """Thread-safe wrapper for ``_download_document``."""
    fid, url = args
    raw_bytes, size_bytes, skip_reason = _download_document(url, fid)
    return fid, url, raw_bytes, size_bytes, skip_reason


# ---------------------------------------------------------------------------
# Canonical record construction
# ---------------------------------------------------------------------------


def filing_id_for(filing: Dict[str, Any]) -> str:
    """Return the stable filing identifier used as the upsert key everywhere."""
    return hashlib.md5(
        f"{filing['stockCode']}{filing['date']}{filing.get('title', '')}".encode(),
        usedforsecurity=False,
    ).hexdigest()[:16]


def _filing_record(f: Dict[str, Any]) -> Dict[str, Any]:
    """Build the canonical filing record shared by every sink."""
    fid = filing_id_for(f)
    title_str = f.get("title", "")
    filing_type, filing_subtype = classify_filing(title_str)
    raw_code = str(f["stockCode"]).lstrip("0") or "0"

    filing_category = "LISTED_COMPANY"
    if (not f["stockCode"].strip()) and is_derivative_issuer_filing(title_str):
        issuer_short = extract_issuer_name(title_str)
        ticker = f"{issuer_short}_DERIV.HK"
        filing_category = "DERIVATIVE_ISSUER"
    elif not f["stockCode"].strip():
        ticker = "UNKNOWN.HK"
        filing_category = "UNKNOWN"
    else:
        ticker = f"{raw_code.zfill(4)}.HK"

    return {
        "filing_id": fid,
        "company_ticker": ticker,
        "stock_code": f["stockCode"],
        "stock_name": squash_ws(f.get("stockName", "")) or None,
        "exchange": "HK",
        "filing_type": filing_type,
        "filing_subtype": filing_subtype or None,
        "filing_category": filing_category,
        "title": squash_ws(title_str) or None,
        "filing_date": _parse_filing_date(f.get("date", "")),
        "document_url": f.get("link", "") or None,
        "referenced_tickers": extract_referenced_tickers(title_str, str(f["stockCode"])),
        "source": "HKEx",
        "updated_at": datetime.now(timezone.utc),
    }


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------


def _save_filings_batch_metadata(filings: list, dry_run: bool = False) -> int:
    """Save filing metadata in batches to every configured sink. Phase 1 operation."""
    if not filings:
        return 0
    if dry_run:
        log(f" [DRY-RUN] Would save {len(filings)} filings (metadata)")
        return len(filings)

    records = [_filing_record(f) for f in filings]
    written_values: List[int] = []
    for sink in _configured_sinks():
        if not sink.available():
            warn_sink_unavailable_once(sink)
            continue
        try:
            written, err_code = sink.upsert_filings(records)
        except Exception as exc:  # noqa: BLE001 - one sink must not abort another
            record_sink(sink.id, False, len(records))
            log(f"  {sink.id} metadata batch raised: {type(exc).__name__}: {exc}")
            continue
        if err_code:
            record_sink(sink.id, False, len(records))
            log(f"  {sink.id} metadata batch failed ({len(records)} records): {err_code[:200]}")
        else:
            record_sink(sink.id, True, written)
        written_values.append(written)

    saved_count = max(written_values) if written_values else 0
    if written_values and saved_count < len(records):
        log(f"  Saved {saved_count} / {len(records)} filings on the leading sink")
    return saved_count


def _document_type(doc_url: str) -> str:
    ext = doc_url.lower().split("?")[0].split("#")[0]
    if ext.endswith(".pdf"):
        return "pdf"
    if ext.endswith(".htm") or ext.endswith(".html"):
        return "html"
    if ext.endswith(".xlsx") or ext.endswith(".xls"):
        return "xlsx"
    if ext.endswith(".doc") or ext.endswith(".docx"):
        return "docx"
    return "unknown"


def document_payload(
    fid: str,
    raw_bytes: bytes,
    size_bytes: int,
    doc_url: str,
    extracted_text: str = "",
    tables_json: list | None = None,
) -> Dict[str, Any]:
    """Build the canonical document payload every sink receives.

    ``document_hash`` (MD5) is an identity/dedup convenience; ``document_sha256`` is the
    integrity hash and is what a cross-sink check compares.
    """
    # Sanitise tables: strip None values so option<T> fields are omitted, not null.
    tables_list = [{k: v for k, v in tbl.items() if v is not None} for tbl in (tables_json or [])]
    return {
        "document_size": size_bytes,
        "document_type": _document_type(doc_url),
        "document_hash": (
            hashlib.md5(raw_bytes, usedforsecurity=False).hexdigest() if raw_bytes else ""
        ),
        "document_sha256": hashlib.sha256(raw_bytes).hexdigest() if raw_bytes else "",
        "document_text": extracted_text,
        "document_text_len": len(extracted_text),
        "document_tables": tables_list,
        "document_table_cnt": len(tables_list),
        "document_status": "processed",
        "document_status_reason": "",
    }


def _save_document_to_filing(
    fid: str,
    raw_bytes: bytes,
    size_bytes: int,
    doc_url: str,
    extracted_text: str = "",
    tables_json: list | None = None,
) -> Tuple[bool, str]:
    """Save extracted text + metadata to an existing filing on every configured sink.

    The canonical payload carries the full text and tables; each sink applies its
    own declared limit (SurrealDB truncates for its RPC body size, relational
    sinks store the text column).
    """
    payload = document_payload(fid, raw_bytes, size_bytes, doc_url, extracted_text, tables_json)

    ok = True
    first_error = ""
    for sink in _configured_sinks():
        if not sink.available():
            warn_sink_unavailable_once(sink)
            continue
        try:
            sink_ok, err_code = sink.upsert_document(fid, payload)
        except Exception as exc:  # noqa: BLE001
            record_sink(sink.id, False)
            log(f"  {sink.id} document save raised for {fid}: {type(exc).__name__}: {exc}")
            ok = False
            first_error = first_error or f"{sink.id}_exception"
            continue
        if err_code:
            record_sink(sink.id, False)
            log(f"  {sink.id} document save failed for {fid}: {err_code[:200]}")
            first_error = first_error or err_code
        else:
            record_sink(sink.id, True)
        ok = ok and sink_ok
    return ok, first_error


def _mark_filing_status(fid: str, status: str, reason: str = "") -> bool:
    """Mark a filing with a ``documentStatus`` (e.g. ``skipped``, ``failed``)."""
    ok = True
    for sink in _configured_sinks():
        if not sink.available():
            warn_sink_unavailable_once(sink)
            continue
        try:
            sink_ok, err_code = sink.mark_status(fid, status, reason[:200])
        except Exception as exc:  # noqa: BLE001
            record_sink(sink.id, False)
            log(f"  {sink.id} status update raised for {fid}: {type(exc).__name__}: {exc}")
            ok = False
            continue
        if err_code:
            record_sink(sink.id, False)
            log(f"  {sink.id} status update failed for {fid}: {err_code[:200]}")
        else:
            record_sink(sink.id, True)
        ok = ok and sink_ok
    return ok


# ---------------------------------------------------------------------------
# Phase 1: Metadata scrape
# ---------------------------------------------------------------------------


def run_phase1(
    max_filings: int = 0,
    dry_run: bool = False,
    date_from: str = "",
    date_to: str = "",
    full_history: bool = False,
) -> Tuple[int, set]:
    """Scrape HKEx filings using the JSON API (no browser needed)."""
    if not REQUESTS_AVAILABLE:
        log("ERROR: 'requests' library not installed. Run: pip install requests")
        return 0, set()

    today = datetime.now()
    if full_history:
        dt_from = datetime(1999, 4, 1)
        dt_to = today
    elif date_from and date_to:
        dt_from = datetime.strptime(date_from, "%d/%m/%Y")
        dt_to = datetime.strptime(date_to, "%d/%m/%Y")
    else:
        dt_to = today
        dt_from = datetime(today.year, today.month, today.day).replace(day=1)
        if dt_from.month == 1:
            dt_from = dt_from.replace(year=dt_from.year - 1, month=12)
        else:
            dt_from = dt_from.replace(month=dt_from.month - 1)

    chunks = generate_monthly_chunks(dt_from, dt_to)

    log("=" * 60)
    log("PHASE 1: METADATA SCRAPE")
    log("=" * 60)
    log(f"Date range: {dt_from.strftime('%Y-%m-%d')} to {dt_to.strftime('%Y-%m-%d')}")
    log(f"Monthly chunks: {len(chunks)}")
    log(f"Max filings: {'unlimited' if max_filings <= 0 else max_filings}")
    log("")

    session = http.get_session()

    saved_ids: set[str] = set()
    ingested_tickers: set[str] = set()
    total_api_count: int = 0
    total_unique_count: int = 0
    total_saved: int = 0
    run_id = datetime.now().strftime("%Y-%m-%d_%H-%M")
    save_batch = 50

    def _save_now(pending: list) -> int:
        """Write *pending* to the sinks and forget it.

        A full-history range is ~1M filings; holding them all in memory until the
        last chunk arrives is pure risk, and a crash late in the run would lose the
        lot. Each chunk is persisted as soon as it is deduplicated instead.
        """
        saved = 0
        for start in range(0, len(pending), save_batch):
            saved += _save_filings_batch_metadata(pending[start : start + save_batch], dry_run)
        return saved

    for chunk_idx, (chunk_from, chunk_to) in enumerate(chunks, 1):
        if max_filings > 0 and len(saved_ids) >= max_filings:
            log(f"Reached --limit {max_filings}, stopping.")
            break

        from_str = chunk_from.strftime("%Y%m%d")
        to_str = chunk_to.strftime("%Y%m%d")
        log(
            f"--- CHUNK {chunk_idx}/{len(chunks)}: "
            f"{chunk_from.strftime('%Y-%m-%d')} to {chunk_to.strftime('%Y-%m-%d')} ---"
        )

        remaining = 0
        if max_filings > 0:
            remaining = max_filings - len(saved_ids)

        try:
            chunk_filings, api_total = fetch_chunk_via_api(
                session, from_str, to_str, max_records=remaining
            )
        except Exception as e:
            log(f"  ERROR: {e}")
            log("  Skipping chunk, will retry on next run.")
            continue

        chunk_new = 0
        chunk_filings_new: list = []
        for f in chunk_filings:
            fid = filing_id_for(f)
            if fid not in saved_ids:
                saved_ids.add(fid)
                chunk_filings_new.append(f)
                chunk_new += 1
                raw_code = str(f["stockCode"]).lstrip("0") or "0"
                ingested_tickers.add(f"{raw_code.zfill(4)}.HK")

        if chunk_filings_new:
            chunk_saved = _save_now(chunk_filings_new)
            total_saved += chunk_saved
            log(f"  Saved {chunk_saved} filings ({total_saved} this run, {len(saved_ids)} unique)")

        if api_total is not None and api_total > 0:
            pct = (chunk_new / api_total) * 100
            total_api_count += api_total
            total_unique_count += chunk_new
            log(
                f"  Coverage: {chunk_new}/{api_total} = {pct:.1f}% "
                f"({len(chunk_filings)} raw records)"
            )
            _persist_coverage(
                {
                    "chunk_from": chunk_from,
                    "chunk_to": chunk_to,
                    "api_count": api_total,
                    "ingested_count": len(chunk_filings),
                    "unique_count": chunk_new,
                    "run_id": run_id,
                    "timestamp": datetime.now(timezone.utc),
                }
            )
        elif api_total is not None and api_total == 0:
            log("  Coverage: 0 filings (empty range)")
        else:
            total_unique_count += chunk_new
            log(f"  Coverage: {chunk_new} filings (API total unavailable)")

        log(
            f"  Fetched {len(chunk_filings)} records, {chunk_new} new "
            f"(total unique: {len(saved_ids)})"
        )

    log("")
    log(f"Total unique filings fetched: {len(saved_ids)}")
    if total_api_count > 0:
        overall_pct = (total_unique_count / total_api_count) * 100
        log(f"OVERALL COVERAGE: {total_unique_count}/{total_api_count} = {overall_pct:.1f}%")
        if overall_pct < 100:
            log(
                f"  WARNING: {total_api_count - total_unique_count} filings not captured "
                f"({100 - overall_pct:.1f}% gap)"
            )
        log(f"  Coverage stats saved to scrape_coverage table (run: {run_id})")
    else:
        log(f"OVERALL COVERAGE: {total_unique_count} filings (API totals unavailable)")

    if not saved_ids:
        log("No filings to save.")
        return 0, ingested_tickers

    log("")
    log(f"Complete: {total_saved} filings saved to database ({len(saved_ids)} unique IDs)")
    return total_saved, ingested_tickers


def _persist_coverage(chunk: Dict[str, Any]) -> None:
    """Write one coverage row to every configured sink."""
    for sink in _configured_sinks():
        if not sink.available():
            warn_sink_unavailable_once(sink)
            continue
        try:
            ok, err_code = sink.insert_coverage(chunk)
        except Exception as exc:  # noqa: BLE001
            record_sink(sink.id, False)
            log(f"  WARNING: coverage persist raised on {sink.id}: {type(exc).__name__}: {exc}")
            continue
        if err_code:
            record_sink(sink.id, False)
            log(f"  WARNING: Failed to persist coverage to {sink.id}: {err_code[:200]}")
        else:
            record_sink(sink.id, True)


# ---------------------------------------------------------------------------
# Phase 2: Document backfill
# ---------------------------------------------------------------------------


def run_phase2(
    batch_size: int = 50,
    max_workers: int = MAX_DOWNLOAD_WORKERS,
    limit: int = 0,
) -> dict:
    """Download and process documents for filings that have metadata but no document content."""
    stats = {
        "total_missing": 0,
        "total_processed": 0,
        "docs_downloaded": 0,
        "texts_extracted": 0,
        "tables_total": 0,
        "skipped": 0,
        "errors": 0,
    }

    log("=" * 60)
    log("PHASE 2: DOCUMENT BACKFILL")
    log("=" * 60)
    log(f"Workers: {max_workers}, Batch size: {batch_size}, Limit: {limit or 'unlimited'}")
    log("")

    reader = sinks.read_sink()
    if reader is None:
        log("ERROR: no configured sink supports reads; cannot select pending filings.")
        return stats

    total_missing, count_code = reader.count_pending_filings()
    if count_code:
        log(f"  ERROR: Could not count pending filings via {reader.id}: {count_code[:200]}")
        total_missing = 0
    stats["total_missing"] = total_missing
    log(f"Filings needing processing: {total_missing}")

    if total_missing == 0:
        log("All filings already processed. Nothing to backfill.")
        return stats

    effective_limit = limit if limit > 0 else total_missing
    batch_num = 0
    consecutive_stalls = 0
    MAX_STALLS = 3

    while stats["total_processed"] < effective_limit:
        batch_num += 1
        remaining = effective_limit - stats["total_processed"]
        this_batch = min(batch_size, remaining)

        rows, fetch_code = reader.fetch_pending_filings(this_batch)
        if fetch_code:
            log(f"  ERROR: Could not fetch pending filings via {reader.id}: {fetch_code[:200]}")
            break
        if not rows:
            log(f"No more filings to process (batch {batch_num}).")
            break

        log(f"Batch {batch_num}: Processing {len(rows)} filings...")

        download_tasks: list = []
        for row in rows:
            fid = row.get("filing_id", "")
            doc_url = row.get("document_url", "")
            if fid and doc_url:
                download_tasks.append((fid, doc_url))
            elif fid:
                _mark_filing_status(fid, "skipped", "no_document_url")
                stats["skipped"] += 1

        if not download_tasks:
            stats["total_processed"] += len(rows)
            continue

        # Download in parallel
        downloaded_docs: list = []
        batch_skipped = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_download_worker, t): t for t in download_tasks}
            for future in as_completed(futures):
                try:
                    fid, doc_url, raw_bytes, size_bytes, skip_reason = future.result()
                    if raw_bytes:
                        downloaded_docs.append((fid, doc_url, raw_bytes, size_bytes))
                    else:
                        _mark_filing_status(fid, "skipped", skip_reason or "download_failed")
                        batch_skipped += 1
                except Exception as e:
                    stats["errors"] += 1
                    log(f"  Download error: {e}")

        stats["skipped"] += batch_skipped
        log(f"  Downloaded: {len(downloaded_docs)}, Skipped: {batch_skipped}")

        # Extract text/tables sequentially
        batch_downloaded = 0
        batch_texts = 0
        batch_tables = 0
        for fid, doc_url, raw_bytes, size_bytes in downloaded_docs:
            extracted_text = ""
            tables_json: list = []
            try:
                old_stderr = sys.stderr
                sys.stderr = io.StringIO()
                try:
                    extracted_text, tables_json = extract_content_with_tables(raw_bytes, doc_url)
                finally:
                    sys.stderr = old_stderr
            except Exception as e:
                log(f"  Text extraction error for {fid}: {e}")

            success, error_code = _save_document_to_filing(
                fid, raw_bytes, size_bytes, doc_url, extracted_text, tables_json
            )
            if success:
                batch_downloaded += 1
                if extracted_text:
                    batch_texts += 1
                if tables_json:
                    batch_tables += len(tables_json)
            else:
                _mark_filing_status(fid, "failed", error_code or "save_error")
                stats["errors"] += 1

        stats["docs_downloaded"] += batch_downloaded
        stats["texts_extracted"] += batch_texts
        stats["tables_total"] += batch_tables
        stats["total_processed"] += len(rows)

        log(
            f"  Batch {batch_num}: {batch_downloaded} docs saved, "
            f"{batch_texts} with text, {batch_tables} tables"
        )

        # Stall detection
        batch_status_updates = batch_downloaded + batch_skipped
        if batch_status_updates == 0:
            consecutive_stalls += 1
            log(
                f"  WARNING: No filings updated in this batch "
                f"(stall {consecutive_stalls}/{MAX_STALLS})"
            )
            if consecutive_stalls >= MAX_STALLS:
                log(f"  BREAKING: {MAX_STALLS} consecutive stalls detected. Stopping.")
                break
        else:
            consecutive_stalls = 0

        pct = min(100, (stats["total_processed"] / total_missing) * 100)
        log(f"  Progress: {stats['total_processed']}/{total_missing} ({pct:.1f}%)")

    log("")
    log("=" * 60)
    log("BACKFILL COMPLETE")
    log("=" * 60)
    log(f"Total processed:  {stats['total_processed']}")
    log(f"Docs downloaded:  {stats['docs_downloaded']}")
    log(f"Texts extracted:  {stats['texts_extracted']}")
    log(f"Tables extracted: {stats['tables_total']}")
    log(f"Skipped:          {stats['skipped']}")
    log(f"Errors:           {stats['errors']}")
    return stats


__all__ = [
    "SINK_STATS",
    "reset_sink_stats",
    "record_sink",
    "sink_exit_code",
    "log_sink_summary",
    "warn_sink_unavailable_once",
    "filing_id_for",
    "run_phase1",
    "run_phase2",
    "SUPPORTED_EXTENSIONS",
]
