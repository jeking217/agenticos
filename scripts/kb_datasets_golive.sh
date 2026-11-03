#!/usr/bin/env bash
#
# The go-live check for an Excel-dataset deployment (plan T48, risk K12).
#
# `check-kb-datasets` answers five questions about the database the backend is
# pointed at: the schema exists, all ten tables are there, both pool partitions
# are attached to a *partitioned* rows table, the read-only role may read and may
# not write, and the partition count has not reached the planner's ceiling. Each
# of those has a failure mode that is silent in production - the worst being a
# `dataset_rows` created from the models rather than by 0248's DDL, which accepts
# every insert and answers zero rows through an as-of read.
#
# This script is that check plus the two things a release also needs: the
# migrations applied first, and the backup verified against the live catalog.
#
#   scripts/kb_datasets_golive.sh                    # check only
#   scripts/kb_datasets_golive.sh --migrate          # alembic upgrade head, then check
#   scripts/kb_datasets_golive.sh --backup dump.sql  # also compare a plain dump
#   scripts/kb_datasets_golive.sh --json             # one JSON object on stdout
#
# Exit codes: 0 everything held, 1 a check failed, 2 the script could not run.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"

migrate=0
json=0
backup=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --migrate) migrate=1; shift ;;
        --json) json=1; shift ;;
        --backup)
            [[ $# -ge 2 ]] || { echo "--backup needs a path" >&2; exit 2; }
            backup="$2"; shift 2 ;;
        -h|--help) sed -n '3,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

# The CLI runs through `uv run --directory backend`, which is how every other
# Makefile target invokes it and therefore the only invocation this repository
# tests. Refusing early beats a pipeline that fails forty lines later.
command -v uv >/dev/null 2>&1 || { echo "uv is not on PATH" >&2; exit 2; }

# `db` is a built-in group; the dataset checks are custom commands, which this
# CLI exposes under `cmd`. Two helpers because the namespaces really are two.
run_db() {
    ( cd "$ROOT" && uv run --directory backend agenticos db "$@" )
}

run_cmd() {
    ( cd "$ROOT" && uv run --directory backend agenticos cmd "$@" )
}

if [[ "$migrate" -eq 1 ]]; then
    echo "==> alembic upgrade head"
    run_db upgrade || { echo "migrations failed; nothing else is worth checking" >&2; exit 1; }
fi

status=0

echo "==> schema, partitions and grants"
if [[ "$json" -eq 1 ]]; then
    # Only one JSON object may reach stdout, so the check is captured and its
    # exit code kept - a release gate reads the document, not the exit code alone.
    check_json="$(run_cmd check-kb-datasets --json)" || status=1
    echo "$check_json"
else
    run_cmd check-kb-datasets || status=1
fi

if [[ -n "$backup" ]]; then
    echo "==> backup covers the live catalog: $backup"
    if [[ "$json" -eq 1 ]]; then
        backup_json="$(run_cmd verify-kb-datasets-backup "$backup" --json)" || status=1
        echo "$backup_json"
    else
        run_cmd verify-kb-datasets-backup "$backup" || status=1
    fi
fi

if [[ "$status" -eq 0 ]]; then
    echo "==> go-live check passed"
else
    echo "==> go-live check FAILED: fix the first failing line above and re-run" >&2
fi
exit "$status"
