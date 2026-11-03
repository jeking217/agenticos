#!/usr/bin/env bash
#
# Restore last night's dump into an empty database and prove it can still answer
# (plan T48, risk K12).
#
# `check` proves a *schema* is shaped as the code assumes; it cannot prove a
# backup carried the data, because it reads the database that has it. The drill is
# the other direction: load the dump into a database nobody depends on, then ask
# that database a versioned question. A dump that lost `kb_data`, or that carried
# the partitioned parent without its pool partitions, passes a table count and
# fails here.
#
#   scripts/kb_datasets_restore_drill.sh backups/kb_data-2026-11-03.sql
#   scripts/kb_datasets_restore_drill.sh dump.sql kb_restore_drill
#
# The dump must be plain SQL (`pg_dump --format=plain`); a `-Fc` archive is
# explained rather than decoded, because reading it needs `pg_restore` and a
# format this script does not pretend to handle.
#
# Connection settings come from the environment, the same variables the backend
# reads: POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD. The
# scratch database is created next to them and dropped again on success.
#
# Exit codes: 0 the restore answered, 1 it did not, 2 the script could not run.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"

if [[ $# -lt 1 ]]; then
    sed -n '3,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 2
fi

dump="$1"
scratch="${2:-kb_datasets_restore_drill}"
keep="${KB_DRILL_KEEP:-0}"

[[ -f "$dump" ]] || { echo "no such dump: $dump" >&2; exit 2; }
# A custom-format archive starts with this magic; `pg_restore -l` lists it and
# `pg_dump --format=plain` produces something this drill can load with psql.
if head -c 5 "$dump" | grep -q PGDMP; then
    echo "$dump is a custom-format archive: dump with --format=plain for this drill," >&2
    echo "or restore it by hand with pg_restore and then run verify-kb-datasets-restore." >&2
    exit 2
fi

for tool in psql createdb dropdb uv; do
    command -v "$tool" >/dev/null 2>&1 || { echo "$tool is not on PATH" >&2; exit 2; }
done

export PGHOST="${POSTGRES_HOST:-localhost}"
export PGPORT="${POSTGRES_PORT:-5432}"
export PGUSER="${POSTGRES_USER:-postgres}"
export PGPASSWORD="${POSTGRES_PASSWORD:-postgres}"

# A drill that loads into the database it is checking proves nothing, and a
# restore into production is not a drill. The name is refused rather than
# disambiguated: getting this wrong costs a database.
if [[ "$scratch" == "${POSTGRES_DB:-}" ]] || [[ "$scratch" != *drill* && "$scratch" != *test* ]]; then
    echo "refusing to restore into ${scratch}: the target must be a scratch database" >&2
    echo "(its name has to contain 'drill' or 'test', and differ from POSTGRES_DB)" >&2
    exit 2
fi

# The verification is a custom command, which this CLI exposes under `cmd`.
run_cmd() {
    ( cd "$ROOT" && POSTGRES_DB="$scratch" uv run --directory backend agenticos cmd "$@" )
}

started=$(date +%s)

echo "==> dropping any leftover $scratch"
dropdb --if-exists "$scratch"

echo "==> creating an empty $scratch"
createdb "$scratch"

cleanup() {
    if [[ "$keep" == "1" ]]; then
        echo "==> keeping $scratch (KB_DRILL_KEEP=1)"
    else
        dropdb --if-exists "$scratch"
    fi
}

echo "==> loading $dump"
# ON_ERROR_STOP because a dump that fails halfway leaves a database that answers
# some questions, and a drill that shrugs at that is worse than no drill.
if ! psql -v ON_ERROR_STOP=1 --quiet --dbname "$scratch" --file "$dump" >/dev/null; then
    echo "the dump did not load: nothing to verify" >&2
    cleanup
    exit 1
fi

echo "==> asking the restored database a versioned question"
status=0
run_cmd verify-kb-datasets-restore || status=1

elapsed=$(( $(date +%s) - started ))
if [[ "$status" -eq 0 ]]; then
    echo "==> restore drill passed in ${elapsed}s: $(du -h "$dump" | cut -f1) restored into $scratch"
else
    echo "==> restore drill FAILED after ${elapsed}s" >&2
fi
cleanup
exit "$status"
