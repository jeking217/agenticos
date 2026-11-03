# Excel datasets: storage, retention, capacity and the review that signed it off

A knowledge base can hold rows instead of passages: an uploaded workbook becomes a
queryable, versioned table an agent answers questions from. This note is the
engineering record — where the data lives, what happens to it as it ages, what the
deployment must be able to restore, and the limits somebody measured rather than
assumed. The user-facing behaviour is described in the module docstrings; this is
the part an operator and a reviewer need.

## Why a separate schema

Everything is in `kb_data`, next to `public` rather than inside it.

- **A backup can name it.** `pg_dump -n kb_data` is a runbook line, not a guess
  about which of a hundred tables were the dataset ones.
- **A rescue restore cannot take it silently.** Restoring `public` alone leaves
  the rows where they are; a schema-qualified dump is the only way to lose them,
  which is the failure mode you want to be deliberate.
- **Grants are per schema.** `app_ro`/`app_rw` are authorised on `kb_data`
  conditionally, with `ALTER DEFAULT PRIVILEGES` so a table added later starts with
  the same reach rather than being invisible until somebody notices.

The cost is that `kb_data` is easy to leave out of anything that assumes one
schema. That is the K12 risk, and it is answered in three places rather than one:
the migration creates the schema, the integration harness creates and truncates it
(`backend/tests/integration/conftest.py`), and the runbook below names it.

## Partitions

`dataset_rows` is `PARTITION BY LIST (partition_key)`.

- `dataset_rows_pool_a` / `dataset_rows_pool_b` hold every dataset below the size
  where a dedicated partition pays for itself; a dataset's partition is chosen by
  `dataset_id.int % 2`, so two large tenants land on different partitions.
- A dataset can be promoted to its own partition (`storage_mode = 'dedicated'`)
  or frozen into an archive one (`'archived'`). Promotion is a copy, so it is a
  maintenance operation and not something a publish does mid-flight.
- The primary key is `(partition_key, id)`, which the planner needs: every query
  carries `partition_key` in its predicate, and a query that forgot it would read
  every dataset's history.

## Version retention

A version is append-only, so nothing prunes itself. `sweep-dataset-versions` runs
daily and keeps:

- the **current version**, always;
- the last `KB_DATASET_VERSIONS_KEPT` versions (default 20, floor 2);
- every version **pinned** in `dataset_version_pins` — the table an answer's
  citation writes, so "this was computed from v7" survives the sweep.

Rows go with the last version that could still show them: a row closed at version
N is invisible from N onwards, so the cutoff is the **lowest surviving** version,
not the highest removed. That distinction is load-bearing — a pin keeps an older
version readable in the middle of the range, and judging rows by the highest
removed version would delete exactly what the pin exists to keep.

A pruned version is gone, not merged: `compare` across the boundary reports it as
absent, and the Versions tab lists what a dataset still holds. "v3 is not
retained" is an answer; a silent merge would read as a change of content.

`Apply retention` (the per-organization policy sweep, `app/services/retention.py`)
is a different mechanism and does not touch `kb_data`: dataset history is bounded
by version count, not by age, because a dataset refreshed weekly for two years has
a hundred useful versions and one that has not been touched since March has one.

## Audit and the operation log

Two records, answering different questions.

- `dataset_operations` (per dataset) holds every hand-made change with its before
  and after: what the number was, what it became, who said why.
- The audit trail (`record_audit`) holds `dataset.base_created`,
  `dataset.created`, `dataset.model_updated`, `dataset.row_inserted|updated|
  deleted|restored`, `dataset.relation_created|deleted`,
  `dataset.import_committed`, `dataset.archived`.

The audit entry names the dataset, the record key, the version and the reason the
maintainer typed — and deliberately **not** the values. Those are in the operation
log, which retention reaches and which is scoped to the dataset; an audit trail
that copied them would be a second full copy of the data, outside the retention
that removed the first.

## Capacity

Measured, not budgeted. The table below was a list of budgets until T31 ran a
million rows through the real path — `excel.scan` at the upload screen,
`imports.commit` in the worker, `DatasetService.list_rows` for the grid. The
numbers are from `tests/integration/test_kb_dataset_capacity.py` on one machine
(Docker Desktop, Postgres 16.14 in a container with no CPU or memory limit, Python
3.15.2, 20 visible cores of an Intel Core Ultra 7 265K); the workbook is three
sheets of eight columns, because a sheet is capped at 500 000 Excel rows including
its header.

| Step | Budget was | Measured, 1 000 000 rows | Verdict |
| --- | --- | --- | --- |
| Parse + propose | seconds, not minutes | 93.7 s (10 674 rows/s) | holds |
| Publish the first version | one transaction, batched inserts | 198.9 s (5 029 rows/s) | holds |
| Re-import, 5% of rows changed | "comparable to the first" | **1 259 s (794 rows/s)** | **does not hold: 6× the first** |
| Page 50 rows, current version | a few ms | 200 ms p95 | **does not hold at this size** |
| Page 50 rows, as of an older version | a few ms more | 213 ms p95 | does not hold at this size |
| Aggregate over the dataset | well under the 8 s ceiling | 0.31 s | holds |
| Page 50 rows at offset 500 000 | not claimed | 287 ms | — |

The re-import was 2 106 s before the fix described below and is 1 259 s after it.
Everything else in the table is within noise of the first run.

### Why the re-import is six times the publish

A re-import at a million rows pays for the whole file even though 5% of it changed,
and the design's premise — "the digest compare skips unchanged rows" — skips the
*insert*, not the *reading*. Where the time goes, inside the transaction:

| Step | First publish | Re-import |
| --- | --- | --- |
| load the current version (`current_rows … FOR UPDATE`) | 30.1 s | 79.7 s |
| close the intervals that moved (`close_rows`) | 0 s | **1 041.5 s** |
| insert the new intervals (`insert_rows`) | 38.6 s | 2.0 s |
| count the current rows (`count_current`) | 0.5 s | 0.6 s |
| parse, coerce, compare and digest (not timed above) | 129.7 s | 135.2 s |

Closing fifty thousand intervals is 83% of the re-import, and it was worse: as one
`UPDATE … WHERE id IN (50 000 bind parameters)` it took **1 890 s**. Chunking it into
thousand-id statements (`repo.CLOSE_BATCH`) brought that to 1 041 s, a 45% cut, and
the constant is where the measurement left it. The remaining 21 ms a row is index
maintenance: `dataset_rows` carries four indexes, and an update on a freshly
inserted page has no room to be a HOT update, so every closed interval rewrites all
four — including `dataset_rows_values_idx`, a `jsonb_path_ops` GIN index that **no
query in this codebase reads**. The compiler emits `values->>'field'`, never a
containment predicate, and the text search in `page_rows` is `values::text ILIKE`,
which a GIN cannot serve. Dropping that index is the next large win and is a
migration, so it is written down here rather than done quietly at the end of a
measurement.

`current_rows` is the second item and the one the design got wrong: 79.7 s to load
a million rows the compare then throws 95% of away. It is `select(DatasetRow)`, so
every row is hydrated into an ORM object with its JSONB decoded. Three columns and
no entities would cut it; a per-row digest column would remove it entirely, since
the compare would never decode `values`. The second is a migration.

### Why a page costs 200 ms

`repo.page_rows` runs `SELECT count(*)` over the same predicate as every page,
because the console's pager shows a total. At 20 000 rows that is 6 ms end to end;
at a million it is 200, and the count is nearly all of it. The row itself is cheap —
the deep page at offset 500 000 costs 287 ms, so a page is `count` plus an index
scan of fifty rows.

`datasets.row_count` is maintained on every publish and is exactly what that count
returns when the page is unfiltered and at the current version, so the fix is to
read it instead of counting. Until then the honest statement is: paging is
O(rows) in the number of rows in the dataset, not O(1) in the page size.

Ceilings that keep a bad query or a bad file from becoming an outage:
`statement_timeout` 8 s, `limit` capped at 1 000 rows, `MAX_ROWS_PER_SHEET`
500 000 — which counts Excel's row index, so a full sheet carries 499 999 data rows
and a workbook with 500 001 is refused with "split the workbook" — and joins
limited to relations somebody confirmed, so a model cannot write a cross join.

A dataset large enough to matter gets its own partition; the two pool partitions
exist so two heavy tenants do not queue behind each other's `DELETE`s.

Three things the measurement changed in the code rather than in this table:

- The commit path used to re-parse the whole workbook once **per sheet**, so a
  three-sheet import parsed the same bytes three times. `excel.sheets_of` parses
  once and the commit drops each sheet as it publishes it; the first publish went
  from 5.0 s to 3.2 s at 20 000 rows, and the test that pins it counts workbook
  opens rather than seconds.
- `close_rows` sends its ids in batches of `CLOSE_BATCH`: one statement with 50 000
  bind parameters took 1 890 s, thousand-id statements take 1 041 s, and
  `tests/integration/test_kb_dataset_publish_batches.py` forces the boundary down to
  seven so the loop is exercised across it.
- `MAX_ROWS_PER_SHEET` was verified the hard way: 500 000 data rows plus a header
  is refused, which is why the million-row workbook in the test has three sheets
  and not two.


## Security

The review that signed this off, in the order the risks were ranked:

1. **No SQL from the model.** The agent sends a described query — fields, filters,
   windows, orders — and the compiler emits SQL with every parameter bound. Field
   names are checked against the model's whitelist before they reach a statement,
   `values->>'key'` keys are escaped, and a name that fails the check is refused
   rather than quoted. The compiled SQL and its parameters are returned with the
   result, so a reviewer reads what ran.
2. **Reach is the knowledge base's reach.** A dataset has no ACL of its own; it
   resolves through `KnowledgeBaseService`, so an agent bound to a base can read
   its datasets and one that is not gets `NOT_FOUND`. The name-based agent tools
   resolve inside the bound set only.
3. **Tenant isolation is the partition predicate.** Every statement carries
   `partition_key`, and the repository layer is the only place that builds one.
4. **Import is the untrusted-input path.** A workbook is parsed with openpyxl/xlrd
   into a grid, never executed; the staged file is in `MEDIA_DIR`, keyed by a
   sha256 of its content; a cell that fails coercion is a per-row warning rather
   than a crash. `MAX_ROWS_PER_SHEET` bounds the parse, and the upload route
   inherits the body-size limit every other upload has.
5. **Nothing here is exempt from RBAC.** `collections:view` reads,
   `collections:manage` writes — the same gate the document bases use, because a
   dataset base *is* a knowledge base.
6. **The rollout switch is not a security control.** `KB_DATASETS_ENABLED=false`
   refuses new bases and new imports and removes the agent tools; it does not hide
   what already exists, and it is not claimed to.

### The review, as performed (T32)

Three attack surfaces were exercised against a real Postgres, and each has a test
file that fails if the answer ever changes:
`backend/tests/integration/test_kb_dataset_security.py`.

| Rank | Attack | What was tried | Outcome |
| --- | --- | --- | --- |
| 1 | Read another tenant's data | resolve a dataset by id, and by query, using a context from a second organization | `NOT_FOUND`, the same answer a missing row gives - the refusal cannot be used to probe for what exists |
| 2 | Read history sideways | read an old version, a comparison, a row snapshot, and pin a version, all with a foreign context; and read a colleague's *personal* base from inside the same organization | refused in every case: history resolves through the same `KnowledgeBaseService` gate as the current version, and a personal base is not shared by sharing an organization |
| 3 | Inject through the compiler | a field name carrying `"; DROP TABLE …`, a value carrying the same, and a join naming a relation that was never registered | the name is refused by the schema before compilation; the value is a bind parameter and appears in `params`, never in the SQL text; the join is refused because FROM/JOIN are read from the model |

Two defects were found and fixed during the review rather than after it:

- `DatasetService.pin` took a dataset id and wrote a pin with no access check at
  all. It had no route, so nothing could reach it, but a method shaped that way is
  one a route added later would forget to guard. It now resolves the dataset
  through the caller's own access.
- The field-name pattern ended in `$`, which in a regex also matches *before* a
  trailing newline. `金额\n` was therefore a field distinct from `金额` - two keys
  no query could tell apart. The pattern now ends in `\z`, and the schema's
  whitespace stripping is asserted so the pair cannot come back.

What this review does not claim: the compiler is not proved safe by these tests,
it is bounded by them. The invariants that carry the weight are structural -
`partition_key` in every statement, FROM and JOIN read from the model, values
always bound - and each is asserted where it is built, in
`tests/test_kb_dataset_query.py` and `tests/test_kb_dataset_subquery.py`.

## Rolling it out

1. Deploy with `KB_DATASETS_ENABLED=false`. The migration still runs; nothing is
   offered.
2. Flip it on for a test organization and import one workbook end to end: upload,
   propose, publish, query from the console, query from an agent, edit a row, read
   the operation log, read the audit trail.
3. Confirm `sweep-dataset-versions` appears in Admin → Background jobs with a next
   tick, and that a dataset at `KB_DATASET_VERSIONS_KEPT + 1` versions is pruned on
   its next pass — with the pinned version still listed afterwards.
4. Confirm the backup includes `kb_data`: `pg_dump -n kb_data` non-empty, and a
   restore into a scratch database shows `dataset_rows` with the same row count.

What a deployment must back up, and what a restore has to bring back: `public`
(knowledge base metadata, audit trail) **and** `kb_data` (datasets, versions, rows,
relations, pins). Restoring only `public` leaves bases that list datasets whose
rows are gone; the datasets read as empty rather than as missing, which is the
reason this is written down.

## Operating it

Four commands, two of which a release runs and two of which a person runs when
something is wrong. All of them are `agenticos cmd …` — this CLI exposes custom
commands under a `cmd` group, and `agenticos check-kb-datasets` is a "no such
command" rather than a diagnosis.

| Command | Question | Non-zero when |
| --- | --- | --- |
| `check-kb-datasets [--json]` | is this database shaped as the code assumes? | `kb_data` missing, a table missing, `dataset_rows` not partitioned, a pool partition detached, `app_ro` able to write, ≥ 2000 partitions |
| `verify-kb-datasets-backup DUMP` | would this dump restore what the database holds? | a table is missing from the dump (or extra in it), or a table with rows has no `COPY` block |
| `verify-kb-datasets-restore` | the restored copy can still answer | the shape is wrong, or an as-of read through a partition returns nothing |
| `init-kb-datasets-roles` | a local database has a read-only path at all | the grants did not take (usually: no tables to grant on yet) |

`check-kb-datasets` is deliberately fatal about the shape and a *warning* about
the roles: a local database has no `app_ro`, and refusing every laptop would make
the check something people stop running. That is also why the roles have their own
command — 0248 grants to `app_ro` only **if it already exists**, so a machine that
migrated first has a read-only role that reads nothing, which the check reports as
"neither app_ro nor app_rw exists" until somebody runs the initialisation.

Two scripts wrap them for a release:

- `scripts/kb_datasets_golive.sh [--migrate] [--backup dump.sql] [--json]` — the
  release gate. Every failure in it is silent in production; the worst is a
  `dataset_rows` created from the models rather than by 0248's DDL, which accepts
  every insert and answers zero rows through an as-of read.
- `scripts/kb_datasets_restore_drill.sh DUMP [scratch-db]` — creates an empty
  database, loads last night's plain-format dump into it, and asks that database a
  versioned question. It refuses a database whose name does not contain `drill` or
  `test`, and refuses the database it is checking: a restore into production is
  not a drill. `KB_DRILL_KEEP=1` keeps the scratch database for a look.

### What was run (T46, T48)

Against the development database, which is the one deployment available here:

```
$ agenticos cmd check-kb-datasets
schema kb_data exists: kb_data is present
10 tables: all 10 tables present
pool partitions attached: both pool partitions attached
read-only role cannot write: neither app_ro nor app_rw exists: the runtime
    connects as the owner, so there is no separate read path to check
partition count below 2000: 2 partitions
kb_data: 10 tables, 2 partitions                                       (exit 0)

$ agenticos cmd init-kb-datasets-roles
app_ro: SELECT
app_rw: INSERT, SELECT
roles ready: app_ro reads kb_data, app_rw reads and writes             (exit 0)

$ agenticos cmd check-kb-datasets
read-only role cannot write: app_ro reads only; app_rw reads and writes (exit 0)

$ pg_dump --format=plain → restored into an empty database →
$ agenticos cmd verify-kb-datasets-restore
as-of query runs for dataset 1111…1111: v2 reads 1 rows through partition 00000000
restored database answers a versioned read through the partitioned table  (exit 0)

$ agenticos cmd verify-kb-datasets-backup agenticos-full.sql
backup covers every table: every table is in the dump
backup carries the rows it should: every non-empty table has a data block
agenticos-full.sql: 12 tables match the live catalog                      (exit 0)
```

Twelve, not ten: partitions are tables in `pg_class`, and `verify-kb-datasets-backup`
compares `pg_class`, so a dump that carries the parent and forgets a pool partition
is caught by the same line that catches a missing table. That is the K12 control —
an independent schema quietly left out of the backup, or restored without its
partitions and answering zero rows.

`tests/test_kb_dataset_inspect_command.py` pins the exit codes and the `--json`
shape, because a check that prints a failure and exits zero is a check a pipeline
steps over. `tests/integration/test_kb_dataset_roles.py` proves what the read-only
role actually may do: `SET ROLE app_ro` reads `kb_data.datasets` and is refused an
`INSERT` by Postgres, not by a convention in the application.

## Tests

Twenty-eight files, 280 cases, split by what they can prove without a database:

| Suite | What it is |
| --- | --- |
| `tests/test_kb_dataset_*.py` | pure functions: the parser's header and type inference, `record_key`, the digest, the query compiler against a described model, retention arithmetic, the import-batch machinery, the operator decisions, the route shapes. No database. |
| `tests/integration/test_kb_dataset_*.py` | real Postgres: partitions, the version engine under a savepoint, the import worker and its resumability, relations, as-of reads, tenant isolation, the security review's three attack surfaces, the role grants, the capacity run, and the service facade every route and agent tool goes through. |

Three of those are worth naming because they are the ones that would fail first
if the premise broke: `test_kb_dataset_security.py` (T32 — a second tenant,
history read sideways, and the injection surface),
`test_kb_dataset_capacity.py` (T31 — the numbers in the section above, reproduce
them with `-s -k costs`), and `test_kb_dataset_service.py` (T29 — the layer
between the two, which is where four of the five defects below were found).

### What the gate found

Writing these tests turned up five ways the feature answered a 500 instead of
doing what it says. Each was fixed with the test that named it:

| Store | Symptom | Cause |
| --- | --- | --- |
| `PATCH /kb/datasets/{id}` | `MissingGreenlet` | the UPDATE carries `updated_at = now()`, so the attribute is expired and reading it to build the response was IO outside an await |
| `PUT /kb/datasets/{id}/model` | `MissingGreenlet` | the same, through `touch_dataset`'s statement |
| `PATCH`/`POST`/`DELETE .../rows` | `value too long for varchar(36)` | the audit row wrote `target_id = f"{dataset.id}:{record_key}"` into a column sized for one uuid |
| `GET`/`POST .../relations` | `AttributeError: created_at` | `DatasetRelation` had the columns in the migration but not `TimestampMixin` on the class |
| `GET .../imports/{id}` | a full progress bar before anything was published | `progress_of` read zero-of-zero as 100% and phase `done` for a `draft`/`preview` row |

`make test-cov-kb-datasets` is the feature's own gate. It is a separate rcfile
(`backend/.coveragerc-kb-datasets`) rather than an entry in the curated 100% list
in `pyproject.toml`: a package added to that list by the change that creates it
either ships untested branches or an unreviewable pile of mocks, and the list is
where a file goes when it is finished.

The threshold is 86, and the package stands at **86.9%** (2 762 statements and
768 branches, 2026-11-03). The integer is one below the measurement on purpose: a
refactor that merges two branches should not fail the build on rounding, and
raising it is a one-line change in the commit that earns it. What is left
uncovered is concentrated in two files — `query.py` (78%: the window and
subquery branches the compiler refuses rather than builds) and `excel.py` (77%:
the `.xls`/`.xlsm` readers and the malformed-header paths).


