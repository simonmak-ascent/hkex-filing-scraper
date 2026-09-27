# ADR 0003 — Sink support policy (open source first)

- Status: Accepted
- Date: 2026-09-16
- Deciders: maintainers

## Context

The project accepts more database sinks over time. Each sink brings a driver
dependency, a CI service, a schema to mirror, and a support burden. Without a policy, the
supported set grows by accident and the licensing story becomes unclear — several popular
databases are *source-available* rather than OSI-approved open source.

## Decision

- **Default to OSI-approved open-source engines.** A sink qualifies on its community
  license, regardless of whether an enterprise edition exists alongside it.
- Every sink declares its license and OSI status in the registry (`SinkSpec.license`,
  `SinkSpec.source_available`), and the support matrix documents it. Source-available
  engines are only accepted as clearly labeled exceptions.
- Keep **one driver per engine, optional**. No driver may be promoted to a base dependency;
  the core stays `requests` + `beautifulsoup4`.
- The MySQL/MariaDB (`PyMySQL`) and SQLite (stdlib) sinks landed first; later releases added
  **DuckDB** (`duckdb`), **MongoDB** (`pymongo`), **ClickHouse** (`clickhouse-connect`), and
  **Neo4j** (`neo4j`).

## Supported set

| Sink | License | OSI open source | Extra |
| ---- | ------- | --------------- | ----- |
| `postgres` | PostgreSQL License | Yes | `postgres` |
| `mysql` | GPLv2 (Community) | Yes | `mysql` |
| `mariadb` | GPLv2 | Yes | `mysql` |
| `sqlite` | Public domain | Yes | — |
| `duckdb` | MIT | Yes | `duckdb` |
| `clickhouse` | Apache-2.0 | Yes | `clickhouse` |
| `neo4j` | GPLv3 (Community) | Yes | `neo4j` |
| `mongodb` | SSPL | No — source-available (exception) | `mongodb` |
| `surrealdb` | BSL 1.1 | No — source-available (exception) | — |

Explicitly out of scope: proprietary engines (SQL Server, Oracle, BigQuery, Snowflake,
Redshift) and the remaining search/key-value/wide-column engines (OpenSearch, Cassandra,
Valkey, TiDB).

## Alternatives considered

| Alternative | Why not chosen |
| ----------- | -------------- |
| OSI-only with no exceptions | Would drop SurrealDB (the original destination) and MongoDB, the most popular NoSQL engine. |
| No policy (accept anything) | Makes the license surface unclear and the CI matrix unbounded. |
| Support every engine in the DB-Engines top 20 | Most are proprietary or a poor fit for a filing store. |

## Consequences

- The registry and the support matrix are the authoritative record of what is supported and
  under which license.
- Source-available sinks are visible as exceptions rather than silently included.
- Each new sink costs a driver extra, a CI service (where testable), and a dialect or
  adapter; the capability model keeps those costs bounded.
