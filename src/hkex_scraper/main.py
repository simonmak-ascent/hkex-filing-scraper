"""CLI entry point and argument parsing."""

from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple

from . import __version__, config, sinks
from .config import LINK_TIMEOUT_SECONDS, LOG_DIR, MAX_DOWNLOAD_WORKERS
from .db_postgres import (
    prune_coverage,
    release_scrape_lock,
    try_acquire_scrape_lock,
    write_heartbeat,
)
from .extractor import check_dependencies
from .graph import cross_reference_filings, link_filings_to_companies
from .pipeline import (
    log_sink_summary,
    reset_sink_stats,
    run_phase1,
    run_phase2,
    sink_exit_code,
)
from .utils import close_log_file, log, set_log_file


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_env() -> None:
    """Ensure DATABASE_TARGET is explicit, known, and every sink is usable."""
    ids = config.sink_ids()
    if not ids:
        log(
            "ERROR: DATABASE_TARGET is not set. "
            "Set it to one or more sink ids, ordered, comma-separated "
            "(e.g. DATABASE_TARGET=postgres,sqlite). "
            f"Valid sinks: {', '.join(sinks.known_ids())}"
        )
        sys.exit(1)

    try:
        for sink_id in ids:
            sinks.spec(sink_id)
    except sinks.UnknownSinkError as exc:
        log(f"ERROR: {exc}")
        sys.exit(1)

    unusable = [sink for sink in sinks.enabled_sinks() if not sink.available()]
    if unusable:
        for sink in unusable:
            log(f"ERROR: {sink.unavailable_reason()}")
        log("ERROR: refusing to start with an unusable configured sink.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------


def _format_parity_report(counts: Dict[str, int]) -> str:
    """Format an N-way per-sink filing-count parity report."""
    lines = ["PARITY REPORT", "=" * 60]
    for sink_id, count in counts.items():
        lines.append(f"{sinks.spec(sink_id).label} filings: {count}")
    if len(counts) < 2:
        lines.append("Parity: N/A (configure two or more sinks to compare)")
        return "\n".join(lines)
    spread = max(counts.values()) - min(counts.values())
    lines.append(f"Spread:            {spread}")
    if spread == 0:
        lines.append("Parity: OK (0 spread)")
    else:
        lines.append(f"WARNING: sinks differ by {spread} record(s)")
    return "\n".join(lines)


def _print_coverage(records: list) -> None:
    """Print a coverage table from canonical coverage rows."""
    if not records:
        log("No coverage data found. Run the scraper first to generate coverage stats.")
        return
    print(
        f"{'From':<12} {'To':<12} {'API':>6} {'Raw':>6} {'Unique':>6} "
        f"{'%':>6} {'Run ID':<18} {'Timestamp'}"
    )
    print("-" * 90)
    for r in records:
        cf, ct = r.get("chunk_from"), r.get("chunk_to")
        api = r.get("api_count", 0) or 0
        ingested = r.get("ingested_count", 0) or 0
        unique = r.get("unique_count", 0) or 0
        rid = r.get("run_id", "-") or "-"
        ts = r.get("timestamp", "-") or "-"
        cf_s = str(cf)[:10] if cf else "-"
        ct_s = str(ct)[:10] if ct else "-"
        pct = f"{(unique / api * 100):.1f}" if api > 0 else "N/A"
        print(
            f"{cf_s:<12} {ct_s:<12} {api:>6} {ingested:>6} {unique:>6} {pct:>5}% {str(rid):<18} {ts}"
        )
    print("-" * 90)
    print(f"{len(records)} coverage records")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hkex-scraper",
        description=(
            "Scrape and ingest HKEx (Hong Kong Stock Exchange) regulatory filings "
            "into one or more database sinks. Full pipeline runs by default: "
            "metadata scrape, document download, text extraction, and graph linking."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--full-history",
        action="store_true",
        help="Scrape all filings from April 1999 to today",
    )
    parser.add_argument(
        "--from-date",
        type=str,
        default="",
        metavar="DD/MM/YYYY",
        help="Start date for scraping (default: ~2 months ago)",
    )
    parser.add_argument(
        "--to-date",
        type=str,
        default="",
        metavar="DD/MM/YYYY",
        help="End date for scraping (default: today)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Limit the number of filings to process (0 = unlimited)",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Phase 1 only: scrape metadata without downloading documents",
    )
    parser.add_argument(
        "--backfill-docs",
        action="store_true",
        help="Phase 2 only: download documents for existing filings in the database",
    )
    parser.add_argument(
        "--link-only",
        action="store_true",
        help="Only create/refresh graph edges (no scraping or downloading)",
    )
    parser.add_argument(
        "--no-link",
        action="store_true",
        help="Skip the post-ingest graph phase (company/filing edges)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test mode: fetch data but do not write to the database",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help=(
            "Compare the configured sinks semantically (filing ids and document hashes), "
            "then exit non-zero on any difference"
        ),
    )
    parser.add_argument(
        "--coverage-report",
        action="store_true",
        help="Print historical coverage data from the read source and exit",
    )
    parser.add_argument(
        "--database-target",
        type=str,
        default="",
        metavar="SINKS",
        help=(
            "Override DATABASE_TARGET for this run as a comma-separated list "
            f"(valid: {', '.join(sinks.known_ids())}). Order sets read precedence."
        ),
    )
    parser.add_argument(
        "--parity-report",
        action="store_true",
        help="Print per-sink filing counts and the spread, then exit",
    )
    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _init_schemas() -> None:
    for sink in sinks.enabled_sinks():
        ok, err_code = sink.ensure_schema()
        if ok:
            log(f"  {sink.id} schema initialized successfully")
        else:
            log(f"ERROR: {sink.id} schema initialization failed: {err_code[:300]}")
            sys.exit(1)


def _parity_report() -> None:
    log("PARITY REPORT")
    if len(sinks.enabled_sinks()) < 2:
        log("ERROR: --parity-report requires two or more configured sinks.")
        sys.exit(1)
    counts: Dict[str, int] = {}
    for sink in sinks.enabled_sinks():
        count, err_code = sink.count_filings()
        if err_code:
            log(f"ERROR: could not count filings on {sink.id}: {err_code[:200]}")
            sys.exit(1)
        counts[sink.id] = count
    print(_format_parity_report(counts))
    sys.exit(0)


def _format_verify_report(
    digests: Dict[str, List[Dict[str, Any]]],
    unsupported: Dict[str, str],
) -> Tuple[str, bool]:
    """Compare per-sink (filing_id, document_sha256) sets.

    Returns the report text and whether every comparable sink agrees.
    """
    lines = ["VERIFY REPORT", "=" * 60]
    for sink_id, rows in digests.items():
        lines.append(f"{sinks.spec(sink_id).label} filings: {len(rows)}")
    for sink_id, reason in unsupported.items():
        lines.append(f"{sinks.spec(sink_id).label}: not comparable ({reason})")

    if len(digests) < 2:
        lines.append("Verify: N/A (two or more comparable sinks are required)")
        return "\n".join(lines), True

    reference_id = next(iter(digests))
    reference = {row["filing_id"]: row["document_sha256"] for row in digests[reference_id]}
    problems: List[str] = []

    for sink_id, rows in digests.items():
        if sink_id == reference_id:
            continue
        current = {row["filing_id"]: row["document_sha256"] for row in rows}
        missing = sorted(set(reference) - set(current))
        extra = sorted(set(current) - set(reference))
        mismatched = sorted(
            filing_id
            for filing_id in set(reference) & set(current)
            if reference[filing_id]
            and current[filing_id]
            and reference[filing_id] != current[filing_id]
        )
        label = sinks.spec(sink_id).label
        if missing:
            problems.append(f"{label} is missing {len(missing)} id(s), e.g. {missing[:3]}")
        if extra:
            problems.append(f"{label} has {len(extra)} extra id(s), e.g. {extra[:3]}")
        if mismatched:
            problems.append(
                f"{label} has {len(mismatched)} hash mismatch(es), e.g. {mismatched[:3]}"
            )
        if not (missing or extra or mismatched):
            lines.append(f"{label}: identical to {sinks.spec(reference_id).label}")

    if problems:
        lines.append("")
        for problem in problems:
            lines.append(f"WARNING: {problem}")
        lines.append("Verify: MISMATCH")
        return "\n".join(lines), False

    lines.append("Verify: OK (ids and hashes agree)")
    return "\n".join(lines), True


def _verify_report() -> None:
    log("VERIFY REPORT")
    configured = sinks.enabled_sinks()
    if len(configured) < 2:
        log("ERROR: --verify requires two or more configured sinks.")
        sys.exit(1)

    digests: Dict[str, List[Dict[str, Any]]] = {}
    unsupported: Dict[str, str] = {}
    for sink in configured:
        if not sink.available():
            log(f"ERROR: {sink.unavailable_reason()}")
            sys.exit(1)
        rows, err_code = sink.read_filing_digests()
        if err_code:
            if err_code.endswith("UNSUPPORTED"):
                unsupported[sink.id] = "cannot enumerate filings"
                continue
            log(f"ERROR: could not read filings from {sink.id}: {err_code[:200]}")
            sys.exit(1)
        digests[sink.id] = rows

    report, ok = _format_verify_report(digests, unsupported)
    print(report)
    sys.exit(0 if ok else 1)


def _coverage_report() -> None:
    log("COVERAGE REPORT")
    log("=" * 60)
    reader = sinks.read_sink()
    if reader is None:
        log("ERROR: no configured sink supports reads for the coverage report.")
        sys.exit(1)
    records, err_code = reader.fetch_coverage()
    if err_code:
        log(f"ERROR: could not read coverage from {reader.id}: {err_code[:200]}")
        sys.exit(1)
    _print_coverage(records)
    sys.exit(0)


def _link_phase(ticker_set: set) -> None:
    """Run graph linking under a hard ceiling.

    The phase used to run unbounded after "Complete: N filings saved", which made a
    finished ingest look like a hung process. It is bounded now, and a timeout is
    logged as a skip rather than a failure: the corpus is already written.
    """
    log("")
    log(f"Linking filings to companies (timeout {LINK_TIMEOUT_SECONDS}s)...")
    started = time.monotonic()

    def _alarm(_signum, _frame):
        raise TimeoutError(f"graph phase exceeded {LINK_TIMEOUT_SECONDS}s")

    previous = signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(LINK_TIMEOUT_SECONDS)
    try:
        count = link_filings_to_companies(ticker_set=ticker_set)
        log(f"Created {count} edges")
        xref_count = cross_reference_filings(ticker_set=ticker_set)
        log(f"Created {xref_count} cross-reference edges")
    except TimeoutError as exc:
        log(f"  WARNING: {exc}; edges will be rebuilt on the next --link-only run")
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
        log(f"Graph phase finished in {time.monotonic() - started:.1f}s")


def main() -> None:
    """Entry point for the ``hkex-scraper`` CLI."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.database_target:
        config.apply_database_target(args.database_target)

    check_dependencies()
    _validate_env()

    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"hkex_filings_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.log"
    set_log_file(open(log_path, "w", encoding="utf-8"))

    try:
        reset_sink_stats()
        log(f"Database target: {config.DATABASE_TARGET} (sinks: {', '.join(config.sink_ids())})")

        _init_schemas()

        # One producer at a time. The corpus is idempotent, so a second node simply
        # stands down; running both would double-load HKEx and duplicate coverage rows.
        run_id = datetime.now().strftime("%Y-%m-%d_%H-%M")
        if config.SCRAPE_LOCK_ENABLED and not args.dry_run and "postgres" in config.sink_ids():
            held, detail = try_acquire_scrape_lock()
            log(f"Scrape lock: {detail}")
            if not held:
                log("Another producer is scraping this corpus; exiting without doing work.")
                sys.exit(0)
            write_heartbeat(run_id, "start")

        if args.parity_report:
            _parity_report()

        if args.verify:
            _verify_report()

        if args.coverage_report:
            _coverage_report()

        if args.backfill_docs:
            # Phase 2 only
            run_phase2(
                batch_size=50,
                max_workers=MAX_DOWNLOAD_WORKERS,
                limit=args.limit,
            )
        elif args.link_only:
            log("=" * 60)
            log("LINKING FILINGS TO COMPANIES")
            log("=" * 60)
            count = link_filings_to_companies()
            log(f"Created {count} edges")
            log("")
            xref_count = cross_reference_filings()
            log(f"Created {xref_count} cross-reference edges")
        else:
            # Phase 1: scrape metadata
            total, ingested_tickers = run_phase1(
                max_filings=args.limit,
                dry_run=args.dry_run,
                date_from=args.from_date,
                date_to=args.to_date,
                full_history=args.full_history,
            )
            if not args.dry_run:
                write_heartbeat(run_id, "metadata", f"{total} filings saved")
                pruned_ok, pruned = prune_coverage()
                if pruned_ok and pruned:
                    log(
                        f"Pruned {pruned} coverage rows older than {config.COVERAGE_RETENTION_DAYS} days"
                    )

            if not args.dry_run and total > 0:
                if args.no_link:
                    log("")
                    log("Skipping graph linking (--no-link)")
                else:
                    _link_phase(ingested_tickers)

                # Phase 2: download documents (default unless --metadata-only)
                if not args.metadata_only:
                    write_heartbeat(run_id, "documents")
                    log("Proceeding to Phase 2: Document download & text extraction...")
                    log("")
                    run_phase2(
                        batch_size=50,
                        max_workers=MAX_DOWNLOAD_WORKERS,
                        limit=args.limit,
                    )
                else:
                    log("Skipping document downloads (--metadata-only)")
                    log("Run again with --backfill-docs to download documents later.")
                    log("")

        if not args.dry_run:
            write_heartbeat(run_id, "complete")
        log_sink_summary()
        log("=" * 60)
        log("COMPLETE")
        log("=" * 60)
        log(f"Log: {log_path}")
        exit_code = sink_exit_code()
        if exit_code:
            log("ERROR: one or more sink writes failed (see sink summary above)")
        sys.exit(exit_code)
    finally:
        release_scrape_lock()
        close_log_file()
        sinks.close_all()


if __name__ == "__main__":
    main()
