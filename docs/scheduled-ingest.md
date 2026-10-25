---
description: "Run the HKEx scraper on a schedule, tell a live run from a hung one, and recover from a failed chunk without gaps in the corpus."
---

# Keep the corpus current

A one-off scrape answers a question about the past. Most deployments need the corpus to stay
current: a nightly run that picks up the filings published since yesterday, and an operator who
can tell a quiet run from a stopped one.

This page is the operational recipe. The flags themselves are in the [CLI reference](cli.md),
and the connection details are in [Configuration](configuration.md).

## Choose the window

With no date flags the run covers roughly the last two months, ending today:

```bash
hkex-scraper
```

That default is deliberately wider than a day. HKEx publishes late, and a filing dated
yesterday can first appear in a search result this morning, so a one-day window leaves holes
whenever a run is skipped.

Widen it explicitly after an outage rather than only scraping today:

```bash
hkex-scraper --from-date 01/09/2026 --to-date 25/10/2026
```

Every chunk that is fetched successfully gets a `scrape_coverage` row, so the report shows
which days actually came back. A chunk that failed has **no** row — the gap is the absence, not
an error entry:

```bash
hkex-scraper --coverage-report
```

## Schedule one run per host

```bash
# /etc/systemd/system/hkex-scraper.service
[Unit]
Description=HKEx filing ingest
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/hkex
EnvironmentFile=/opt/hkex/.env
ExecStart=/opt/hkex/.venv/bin/hkex-scraper
TimeoutStartSec=6h
```

```bash
# /etc/systemd/system/hkex-scraper.timer
[Unit]
Description=Nightly HKEx filing ingest

[Timer]
OnCalendar=*-*-* 03:30:00
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
```

`Persistent=true` runs the job after a host reboot if the scheduled time was missed, which is
what keeps a laptop-class deployment from silently skipping a night.

A `cron` entry works the same way if the host has no systemd:

```cron
30 3 * * * cd /opt/hkex && .venv/bin/hkex-scraper >> logs/cron.log 2>&1
```

Scheduling the same job on several hosts is safe but unnecessary — see the next section.

## Let the lock pick the producer

When PostgreSQL is one of the configured sinks, a run takes a session-level advisory lock
before its first chunk. On a host that already has a producer, the second run logs the holder
and exits `0` without doing work, because the corpus is idempotent and the holder ingests the
same filings. That makes the same timer safe to install on every node of a cluster:

- No duplicate document downloads, no doubled API traffic.
- No "failed" alert on the standby nodes — exit `0` is the correct outcome there.
- `SCRAPER_LOCK=0` exists for a manual run that must not wait for a holder.

If the lock cannot be taken because the driver or the database is missing, the run degrades to
unlocked rather than refusing to scrape. That keeps a SQLite-only deployment working, and it is
why the lock is a coordination aid rather than a correctness guarantee.

## Watch the heartbeat, not the exit code

A scheduled job that hangs produces no exit code at all, so monitoring only the process tells
you nothing. Every run writes its phase to two places:

| Signal | Where | What it is |
| --- | --- | --- |
| `scraper_heartbeat` row | PostgreSQL | `run_id`, `phase`, `detail`, `updated_at` — one row, upserted in place |
| Heartbeat file | `SCRAPER_HEARTBEAT_FILE`, default `/var/lib/hkex-scraper/heartbeat` | epoch seconds of the last phase change |

The row moves through `start` → `metadata` → `documents` → `complete`. A row stuck on
`metadata` with an `updated_at` older than the run's expected duration is a hung run, while an
absent row means nothing started at all.

The file carries epoch seconds on purpose: a watchdog built from `date +%s` and coreutils needs
no database driver and no Python. Set `SCRAPER_HEARTBEAT_FILE` to an empty string to disable it.

```bash
# Alert when the last heartbeat is older than 6 hours.
age=$(( $(date +%s) - $(cat /var/lib/hkex-scraper/heartbeat) ))
[ "$age" -lt 21600 ] || echo "hkex ingest stalled for ${age}s" >&2
```

## Bound each phase

Timeouts keep one slow chunk from turning into a hung run:

| Variable | Default | Effect |
| --- | --- | --- |
| `API_READ_TIMEOUT` | `30` | Read timeout for a single API page, in seconds. |
| `CHUNK_DEADLINE_SECONDS` | `300` | Hard ceiling per date chunk. An overrun is logged as skipped and the run continues. |
| `LINK_TIMEOUT_SECONDS` | `300` | Hard ceiling for the graph phase. The edges are rebuilt by the next `--link-only`. |

A chunk that overruns is logged with its range and skipped — the run continues, and the next
run retries that range. It leaves no `scrape_coverage` row, which is how the report shows it.
Already-ingested filings upsert in place, so re-running a range costs API calls and nothing
else.

To be deliberately gentle with the endpoint, set `REQUEST_DELAY_SECONDS` (for example `0.25`)
rather than lowering the deadlines.

## Recover from a failed run

Exit `0` means every configured sink accepted its writes. Exit `1` means a sink failed a write,
`--verify` found a difference, or the configuration is unusable.

1. Read the run log — `logs/hkex_filings_<YYYY-MM-DD_HH-MM>.log` in the working directory.
   Credentials are never written to it.
2. Re-run the same window. Writes are idempotent per sink, so nothing is duplicated.
3. If documents are the gap, run Phase 2 alone: `hkex-scraper --backfill-docs`.
4. If graph edges are the gap, rebuild them without touching filings: `hkex-scraper --link-only`.

`COVERAGE_RETENTION_DAYS` (default `180`) prunes old `scrape_coverage` rows after a successful
metadata phase. Raise it if you want a longer post-mortem window; at daily polling the table
grows by roughly 70 rows a day.

## Verify a multi-write deployment

When one run writes to several sinks, compare them by content rather than by row count:

```bash
hkex-scraper --database-target postgres,sqlite --parity-report
hkex-scraper --database-target postgres,sqlite --verify
```

`--parity-report` prints per-sink counts and their spread. `--verify` compares every filing id
and document hash and exits non-zero on any difference, which is the check worth putting in a
weekly timer.

## Next steps

- [CLI reference](cli.md) — every flag, exit code and log location.
- [Configuration](configuration.md) — DSNs, sink selection and precedence.
- [Troubleshooting](troubleshooting.md) — the errors this page assumes are already handled.
