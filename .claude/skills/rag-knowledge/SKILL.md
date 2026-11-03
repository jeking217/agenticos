---
name: rag-knowledge
description: Work with the knowledge base — ingest documents, run semantic search, manage collections, add a sync connector (Google Drive, S3), change a parser or an ingestion setting, or import and maintain an Excel dataset base (spreadsheet import, version history, as-of reads, record-level diffs). Use when populating or debugging retrieval, when a document upload fails or dies silently in a worker, when "the agent cannot find something that is definitely in the collection", or when a dataset import is slow, refuses a workbook, or reads a version that looks wrong. pgvector + per-organization embedding keys; datasets live in their own `kb_data` schema.
---

# Knowledge base (pgvector)

**Read `docs/file-processing.md`** for the pipeline and
`docs/howto/add-sync-connector.md` / `docs/howto/configure-sync-sources.md` for
sources. Code: `app/services/rag/` — ingestion, vectorstore, embeddings, connectors.

The model reaches it through the **`knowledge` capability**, whose tool is
`search_documents`. (`search_knowledge_base` is an internal function in
`capabilities/knowledge/_search.py`, not the tool the model sees.) A collection is
searched, never browsed: the model chooses *what* to look for and can never widen
*where* it looks — collections are resolved from the spec before the run.

## CLI

```bash
uv run agenticos cmd rag-ingest ./docs/ --collection docs --recursive
uv run agenticos cmd rag-search "your question" --collection docs
uv run agenticos cmd rag-collections
uv run agenticos cmd rag-stats
uv run agenticos cmd rag-drop <collection> --yes
uv run agenticos cmd rag-sources
uv run agenticos cmd rag-source-add
uv run agenticos cmd rag-source-sync --all
uv run agenticos cmd rag-source-remove <id>
```

Ingestion is parse → chunk → embed → upsert. It runs as an `ingest-document` job
in a worker's child process, under a memory watchdog, never inline in a request -
see the `background-task` skill. An upload queues the job in its own transaction;
a document whose parse kills the child is retried once, then dead with why.

## The four traps

**1. The database must be `pgvector/pgvector:pg16`.** The store issues
`CREATE EXTENSION IF NOT EXISTS vector` on first write; stock Postgres answers
`extension "vector" is not available` — a 500 before any row is committed. If ingestion
500s on a fresh environment, **check the image first**. Every compose file and both CI
jobs pin it; they used to pin `postgres:16-alpine`, which is why no ingestion path had
ever been exercised locally or in CI.

**2. A format list can lie, and the upload is accepted anyway.**
`GET /rag/supported-formats` and the upload validator answer from `PARSER_FORMATS`;
`DocumentProcessor.process_file` is what actually routes. When the two disagree the
upload **succeeds** — file stored, document row committed, task dispatched — and dies
in a worker, so the document sits in the listing with no explanation.
`tests/test_supported_formats.py` pins each parser's set against what the pipeline can
route, **in both directions**. Widening a format set is what that test exists for.

**3. Narrowing an `IngestionConfig` rule breaks existing rows.** It lives in a JSONB
column, so a tighter rule does not only reject new input — it makes stored rows
unreadable, and a Pydantic model refusing one field of one row takes the whole listing
endpoint down with a 500. Adding a field is safe (missing keys take defaults);
narrowing needs a data migration **in the same change**. the OCR language codes are the
worked example. See the `alembic-migration` skill.

**4. A document that parses to nothing.** Silently indexing an empty result is
indistinguishable afterwards from a document that ingested fine and never matches.
Markdown reconstruction returns an **empty fenced block** rather than whitespace for an
unreadable scan, so `.strip()` is not the check.

## Credentials

Embedding keys, connector credentials and a LlamaParse key are organization secrets in
the vault. **`CHANNEL_ENCRYPTION_KEY` is gone** — see the `vault-secrets` skill.
`app/services/embedding_resolution.py` decides which key a collection embeds with, and
it *is* in the gated platform layer.

## Adding a connector

Implement it in `app/services/rag/connectors/` following the Google Drive / S3
connectors, register it so the sync service discovers it, and expose its config fields.
`docs/howto/add-sync-connector.md` is the walkthrough; `docs/patterns.md` has the
registration shape.

## Debugging bad results

1. Is the collection populated? `rag-stats`.
2. Is it bound to *this* agent's spec (`collection_ids`)? An unbound collection is
   invisible, and `knowledge` bound with no collections contributes **nothing at all**.
3. Does the org have `knowledge:read`?
4. Same embedding model throughout? **Do not mix embeddings within a collection** —
   re-ingest if it changed.
5. Did the document actually parse? See trap 4.

## Excel datasets (`kb_type = 'dataset'`)

A *dataset base* is the same `knowledge_bases` row with `kb_type='dataset'` and no
vector collection behind it: it holds spreadsheets, not documents. The rows live in
the `kb_data` schema — `dataset_rows` is **partitioned**, SCD-2 style (`valid_from`
/ `valid_to`), so a version is a range of row versions rather than a snapshot.
Read `docs/design/knowledge-datasets.md` before changing anything here; code is
`app/services/kb_dataset/` (one module per concern: `excel` parse, `versions`
publish, `query` compiler, `imports` wizard, `inspect` operator checks).

```bash
uv run agenticos cmd check-kb-datasets              # release gate: schema, partitions, grants
uv run agenticos cmd verify-kb-datasets-backup dump.sql
uv run agenticos cmd verify-kb-datasets-restore     # run against the restore target
uv run agenticos cmd init-kb-datasets-roles         # local only: app_ro / app_rw
bash scripts/kb_datasets_golive.sh --migrate        # the three above, in order
bash scripts/kb_datasets_restore_drill.sh dump.sql  # dump → empty db → as-of read
```

**The four traps, and they are all silent:**

**1. A row missing from a new workbook is not a deleted row.** The interval stays
open and the record keeps reading. Only maintenance writes a tombstone. Anything
that "cleans up" absent keys on import destroys the difference between "the export
stopped listing it" and "we removed it", and only a person can say which happened.

**2. `kb_data` is not in most backup configurations.** It is a second schema, and
`pg_dump` without `-n kb_data` / a `--schema` allow-list produces a backup that
restores a database listing datasets whose rows are gone. `verify-kb-datasets-backup`
compares the dump's object list against `pg_class` — twelve objects, not ten,
because the two pool partitions are tables too.

**3. `app_ro` only exists if somebody made it.** The migration grants to it *if it
already exists*, so a database that migrated first has a read-only role that reads
nothing, and the grant check reports a warning rather than a failure. `init-kb-datasets-roles`
is the fix.

**4. The sheet cap counts Excel's row index, header included.** `MAX_ROWS_PER_SHEET`
is 500 000, so a full sheet carries 499 999 data rows and is refused at 500 000
("split the workbook"). A "million-row" workbook is therefore three sheets, and the
import is a background job (`import-dataset`) for exactly that reason.

Measured, not budgeted — a million rows through the real path, on a laptop-class
machine (`loadtest/results/*-dataset-capacity.md`, reproducible with
`tests/integration/test_kb_dataset_capacity.py`): the workbook is parsed twice per
import (once to propose the model at upload, once to publish), and the re-import
pays for the interval compare. If an import feels slow, that is where to look
before sharding anything.

## Coverage

`app/services/rag/*` is template-inherited and **outside** the coverage gate — which is
exactly why the invariants above are pinned by explicit named tests rather than left to
a percentage. Three format lists disagreed there for months without anything failing.
