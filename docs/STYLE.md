# Documentation style

One rulebook for every human-facing doc in this repo: the README, the community files
(`CONTRIBUTING`, `SECURITY`, `SUPPORT`, …), GitHub templates, the wiki, and the docs site
under `docs/`. Consistency matters more than any individual rule below — when in doubt,
match the surrounding text.

## Language

- **US English.** `license`, `behavior`, `color`, `organize`, `analyze`.
- **Second person, active voice.** "Set `DATABASE_TARGET`", not "The operator must set…".
- Short sentences. One idea per sentence.
- No marketing adjectives in reference docs ("simply", "just", "easily", "powerful").

## Terms

One canonical word per concept — do not use synonyms for variety.

| Use | Do not use | Meaning |
| --- | ---------- | ------- |
| **sink** | sink, destination, target | A configured persistence destination (`postgres`, `mysql`, `sqlite`, …). |
| **engine** | database, product, DBMS | The database product itself (PostgreSQL, MongoDB, …). |
| **filing** | record, document (for metadata) | One HKEx regulatory disclosure. |
| **document** | attachment, file | The source file attached to a filing. |
| **pipeline** | job, run, process | A single execution of Phase 1 and/or Phase 2. |

Format identifiers, paths, variables, and commands as `` `code` ``. Format sink ids as
`` `postgres` ``. Write `DATABASE_TARGET`, not "the database target variable".

## Structure

- **Page title (H1)** is Title Case: `# Configuration Reference`.
- **Section headings (H2+)** are sentence case: `## Database sink selection`.
- Do not skip heading levels (no H3 under H1).
- Each page ends with a short **See also** or **Next steps** list of relative links, unless it
  is a terminal reference (ADR, changelog entry).
- Per-sink guides follow one template so pages are diffable:
  `Overview → Install → Configure → Schema → Behavior and limits → Example queries → Troubleshooting`.

## Formatting

- Bullets: `- **Label** — description.` Bold label, em dash, sentence, full stop.
- Ordered lists only for genuine sequences (steps, precedence).
- Tables for variable/option/flag references; prose for everything else.
- Fence every code block with a language (`bash`, `ini`, `sql`, `cypher`, `python`, `yaml`,
  `json`, `text`). Use `text` for plain output.
- Use relative links for anything inside the repo (`docs/sinks/mysql.md`,
  `../postgresql.md`). Absolute GitHub URLs are only for files outside `docs/` when writing
  from the docs site, or for external resources.
- One trailing newline per file; no trailing whitespace (Markdown files excepted for hard
  breaks — prefer paragraphs).

## Tone

- Direct and neutral. Explain the *why* only when it is not obvious.
- Prefer concrete examples over abstract description.
- No emoji. Em dashes (—) for asides, not parentheses, when the aside is short.

## Enforcement

- `markdownlint-cli2` runs in CI (`.markdownlint-cli2.jsonc`). Rules are tuned to this guide:
  line length is disabled, and mermaid/front-matter blocks are allowed.
- `tests/test_docs_consistency.py` fails a PR when a sink is added, removed, or renamed
  without updating the docs, templates, or `.env.example`.
