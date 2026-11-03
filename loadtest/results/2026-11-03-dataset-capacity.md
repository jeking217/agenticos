# Dataset capacity run — 2026-11-03, one million rows

The measurement the design's Capacity section used to call a set of budgets
(`docs/design/knowledge-datasets.md`). It runs a real workbook through the real
import path and pages a real dataset, against real Postgres.

- **Machine** — Windows 11 + Docker Desktop · Intel Core Ultra 7 265K (20 visible
  cores) · Python 3.15.2 · Postgres 16.14 (`pgvector/pgvector:pg16`) in a container
  with no CPU or memory limit · no other work on the machine beyond the run itself
- **Workload** — three sheets of 8 columns (a text key, a foreign key, a date, two
  integers, three low-cardinality texts), 1 000 000 rows; the re-import changes one
  row in twenty and re-sends the whole file, which is what a person does
- **Path** — `excel.scan` (the upload screen), `imports.commit` (the worker),
  `DatasetService.list_rows` (the console's grid). Nothing mocked
- **Reproduce** — `KB_CAPACITY_ROWS=1000000 KB_CAPACITY_SAMPLES=200 pytest
  tests/integration/test_kb_dataset_capacity.py -s -k costs`, or `make test` for the
  20 000-row version that guards the same thresholds

## The budget, and what it cost

| Step | Budget was | Measured | Verdict |
| --- | --- | --- | --- |
| Parse + propose 1 000 000 rows | seconds, not minutes | 93.7 s (10 674 rows/s) | holds |
| Publish the first version | one transaction, batched inserts | 198.9 s (5 029 rows/s) | holds |
| Re-import, 5% changed | comparable to the first | **1 259.0 s (794 rows/s)** | 6× the first publish |
| Page 50 rows, current version | a few ms | 200.4 ms p95 | not at this size |
| Page 50 rows, as of v3 | a few ms more | 212.5 ms p95 | not at this size |
| Aggregate over 1 000 000 rows | well under the 8 s ceiling | 0.31 s | holds |
| Page 50 rows at offset 500 000 | not claimed | 287 ms | — |

## Where the two publishes went

Inside the transaction, timed at the repository:

| Step | First publish | Re-import |
| --- | --- | --- |
| load the current version (`current_rows … FOR UPDATE`) | 30.1 s | 79.7 s |
| close the intervals that moved (`close_rows`) | 0 s | **1 041.5 s** |
| insert the new intervals (`insert_rows`) | 38.6 s | 2.0 s |
| count the current rows (`count_current`) | 0.5 s | 0.6 s |
| parse, coerce, compare and digest (outside the four) | 129.7 s | 135.2 s |

## What the run changed

1. **One `UPDATE` with 50 000 bound ids took 1 890 s.** Chunked into thousand-id
   statements (`repo.CLOSE_BATCH`) the whole re-import went from 2 106 s to 1 259 s.
   The constant is where the measurement left it, and
   `tests/integration/test_kb_dataset_publish_batches.py` forces the boundary down to
   seven so the loop is exercised across it.
2. **The commit parsed the workbook once per sheet.** `excel.sheets_of` parses once
   and the commit drops each sheet as it publishes it. At 20 000 rows the first
   publish went from 5.0 s to 3.2 s; at a million the saving is one whole parse
   (~30 s of the 198.9 s).
3. **`MAX_ROWS_PER_SHEET` counts Excel's row index.** 500 000 data rows plus a
   header is refused with "split the workbook", so a million-row workbook is three
   sheets and the first attempt of this run failed on exactly that.

## What is left, in order of size

- **Closing intervals costs 21 ms a row, and 83% of a re-import is closing them.**
  `dataset_rows` has four indexes and a freshly inserted page has no room for a HOT
  update, so each close rewrites all four — one of them
  `dataset_rows_values_idx`, a `jsonb_path_ops` GIN index that no query in this
  codebase reads (the compiler emits `values->>'field'`; the console's search is
  `values::text ILIKE`). Dropping it is the next large win, and it is a migration.
- **`current_rows` hydrates a million ORM objects** with their JSONB decoded, to
  compare values it then discards 95% of. Three columns and no entities would cut
  it; a stored per-row digest would remove it.
- **A page is `count(*)`.** 200 ms at a million rows, and `datasets.row_count`
  already holds that number for the unfiltered current-version case.
