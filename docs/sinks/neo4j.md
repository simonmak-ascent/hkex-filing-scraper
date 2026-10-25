---
description: Write HKEx filings to Neo4j as nodes and relationships, with MERGE for idempotency. Connection variables, node model and constraints.
---

# Neo4j sink

Persist filings and graph edges to Neo4j as nodes and relationships.

- License: **GPLv3** (Community Edition) — OSI-approved open source. The `neo4j` Python driver
  is Apache-2.0, so connecting to a server does not affect this project's MIT license.
- Driver: [`neo4j`](https://pypi.org/project/neo4j/) (Bolt).
- Extra: `neo4j`.

## Install

```bash
pip install ".[neo4j]"
```

## Configure

```bash
DATABASE_TARGET=neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=secret
# NEO4J_DATABASE=neo4j          # optional; omit for the default database
```

Run it:

```bash
hkex-scraper --database-target neo4j --limit 100
```

Constraints and indexes are created automatically on startup (Neo4j 5 `IF NOT EXISTS` syntax).

## Graph model

```text
(:Company {id})-[:HAS_FILING]->(:Filing {filingId})
(:Filing {filingId})-[:REFERENCES_FILING]->(:Company {id})
```

`(:Filing)` carries the filing metadata as properties (`companyTicker`, `stockCode`, `title`,
`filingDate`, …) plus document properties (`documentText`, `documentTables` as a JSON string,
`documentTableCnt`, `documentStatus`, …). `referencedTickers` is a string-array property.
`(:Company {id})` uses the ticker-derived key (`451_HK`), matching the other sinks.

Every write uses `MERGE`, so re-running is idempotent.

```cypher
// filings for one company
MATCH (c:Company {id: '451_HK'})-[:HAS_FILING]->(f:Filing)
RETURN f.title, f.filingDate ORDER BY f.filingDate DESC LIMIT 10;

// filings that mention another company
MATCH (f:Filing)-[:REFERENCES_FILING]->(c:Company {id: '700_HK'})
RETURN f.filingId, f.title;

// documents that still need processing
MATCH (f:Filing)
WHERE f.documentStatus IS NULL AND f.documentUrl IS NOT NULL
RETURN count(f);

// most-referenced companies
MATCH (:Filing)-[r:REFERENCES_FILING]->(c:Company)
RETURN c.id, count(r) AS mentions ORDER BY mentions DESC LIMIT 10;
```

## Notes and limitations

- **Nested data**: Neo4j properties cannot hold nested maps, so `documentTables` is stored as a
  JSON string. Parse it client-side (`apoc.convert.fromJsonList` or in your application).
- **Company nodes are created by `MERGE`** for every referenced/owning ticker; the SurrealDB
  sink, by contrast, only links tickers that already exist in the configured company table.
- **Traversal** is where Neo4j shines; for tabular analytics prefer a relational sink.
- **Credentials** are never logged.

## Troubleshooting

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `Neo4j sink requires the neo4j driver` | Extra not installed | `pip install ".[neo4j]"` |
| `Neo4j sink requires NEO4J_URI` | URI not set | Set `NEO4J_URI` |
| `Neo4j sink requires NEO4J_USER and NEO4J_PASSWORD` | Credentials missing | Set both |
| `ServiceUnavailable` | Server unreachable | Check `NEO4J_URI` and the Bolt port (7687) |
| `An equivalent constraint already exists` | Older Neo4j without `IF NOT EXISTS` | Upgrade to Neo4j 5, or create the constraints manually |
