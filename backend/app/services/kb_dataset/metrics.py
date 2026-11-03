"""The five numbers the rollout is judged on, as structured log records.

This deployment has no metrics client: nothing in the codebase creates a
counter or a histogram, and the observability it does have is its logs. So
"instrumented" here means one record per event, with a stable `event` name and
machine-readable fields, which is what a log pipeline can count and bucket. The
names are the plan's (`kb_dataset.import.duration` and the rest) rather than
something invented, so a dashboard written against the design reads the same
numbers here.

Why these five, and not more: the plan asks for the ones that answer questions
somebody will actually ask during a rollout - is an import slow, is it failing,
is a query slow, is the model being refused, is history growing. Anything else
is a number nobody has decided a threshold for.

A record is emitted at the end of the work, not per row: a 500k-row import
writes one line, because a per-row metric is a log flood and a lock.
"""

import logging
from typing import Any

LOGGER = logging.getLogger("kb_dataset.metrics")

IMPORT_DURATION = "kb_dataset.import.duration"
IMPORT_FAILURE = "kb_dataset.import.failures"
QUERY_LATENCY = "kb_dataset.query.latency"
QUERY_REJECTED = "kb_dataset.query.rejected"
VERSION_ROWS = "kb_dataset.version.rows"


def _emit(event: str, **fields: Any) -> None:
    """One record, with the event name as `extra` rather than in the message.

    The message stays empty: log aggregation groups by the event, and a message
    that repeats it is a second copy that can disagree with the first.
    """

    LOGGER.info(event, extra={"event": event, **fields})


def import_finished(
    *, sheets: int, rows: int, duration_ms: int, failed_stages: dict[str, int] | None = None
) -> None:
    """An import commit, whether it landed or stopped part way.

    `failed_stages` is what makes the failure counter useful: parse, validate and
    write are three different problems with three different fixes, and a single
    "imports failed" count cannot tell them apart. It is a count per stage rather
    than one stage because a batch can fail two different ways.
    """

    _emit(
        IMPORT_DURATION,
        sheets=sheets,
        rows=rows,
        duration_ms=duration_ms,
        outcome="failed" if failed_stages else "ok",
    )
    for stage, count in (failed_stages or {}).items():
        _emit(IMPORT_FAILURE, stage=stage, count=count)


def query_finished(*, rows: int, joins: int, duration_ms: int) -> None:
    """One answered question: what it cost and how wide it was."""

    _emit(QUERY_LATENCY, rows=rows, joins=joins, duration_ms=duration_ms)


def query_rejected(*, reason: str) -> None:
    """One refused question, by reason - a prompt-quality signal, not an error rate."""

    _emit(QUERY_REJECTED, reason=reason)


def version_published(*, rows: int, version_no: int) -> None:
    """A new version and how many rows it carries: the growth curve retention reads."""

    _emit(VERSION_ROWS, rows=rows, version_no=version_no)
