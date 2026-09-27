# MongoDB sink

Persist filings, document payloads, coverage rows, and graph edges to MongoDB.

- License: **SSPL** — **not OSI-approved** (source-available). Accepted as a labeled exception
  per [ADR 0003](../adr/0003-sink-support-policy.md).
- Driver: [`pymongo`](https://pypi.org/project/pymongo/) (Apache-2.0).
- Extra: `mongodb`.

## Install

```bash
pip install ".[mongodb]"
```

## Configure

```bash
DATABASE_TARGET=mongodb
MONGODB_URI=mongodb://localhost:27017
MONGODB_DATABASE=hkex
```

With authentication:

```bash
MONGODB_URI=mongodb://user:password@localhost:27017/?authSource=admin
```

Run it:

```bash
hkex-scraper --database-target mongodb --limit 100
```

Collections and indexes are created automatically on startup.

## Schema

| Collection | `_id` | Contents |
| ---------- | ----- | -------- |
| `exchange_filing` | `filing_id` | Filing metadata + document payload (same field names as the relational sinks). |
| `scrape_coverage` | `"<from>\|<to>\|<run_id>"` | One document per chunk. |
| `has_filing` | `"<company_id>\|<filing_id>"` | Company → filing edges. |
| `references_filing` | `"<filing_id>\|<company_id>"` | Filing → referenced-company edges. |

`document_tables` and `referenced_tickers` are stored as native BSON arrays. A metadata
upsert uses `$set` on metadata fields only, so it never touches `document_*`; a document write
uses `$set` on `document_*` only.

```javascript
// filings for one ticker
db.exchange_filing.find({ company_ticker: "0451.HK" }, { title: 1, filing_date: 1 })

// filings that reference another company
db.exchange_filing.find({ referenced_tickers: "0700.HK" })

// documents with extracted tables
db.exchange_filing.countDocuments({ document_table_cnt: { $gt: 0 } })

// graph edges
db.has_filing.aggregate([
  { $group: { _id: "$company_id", filings: { $sum: 1 } } },
  { $sort: { filings: -1 } },
])
```

## Notes and limitations

- **Idempotent**: every write upserts on `_id`, so re-running updates rather than duplicating.
- **Reads** are served through the sink contract (`find`/`count_documents`), so MongoDB can be
  first in `DATABASE_TARGET` to act as the read source.
- **Credentials** are never logged; driver errors are redacted.

## Troubleshooting

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `MongoDB sink requires pymongo` | Extra not installed | `pip install ".[mongodb]"` |
| `MongoDB sink requires MONGODB_URI` | URI not set | Set `MONGODB_URI` |
| `MongoDB sink requires MONGODB_DATABASE` | Database not set | Set `MONGODB_DATABASE` |
| `ServerSelectionTimeoutError` | Server unreachable | Check the URI, host, and network |
