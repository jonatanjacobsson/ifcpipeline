"""Tolerant accessors for RQ job fields.

The dashboard deliberately does not install the workers' task code, so any RQ
attribute that unpickles the job payload (`func_name`, `args`, `kwargs`) raises
``DeserializationError`` — ``ModuleNotFoundError: No module named 'shared'`` —
for jobs enqueued against worker-side functions. Left unguarded, a single such
job aborts a whole history sync or blanks a job detail page, so read through
these helpers instead of touching the attributes directly.
"""

from __future__ import annotations

from typing import Any

from rq.job import Job


def safe_func_name(job: Job) -> str | None:
    """`job.func_name`, or None when the payload cannot be deserialized."""
    try:
        return job.func_name
    except Exception:
        return None


def safe_description(job: Job) -> str:
    """`job.description` (stored on the job hash), or "" if it has to be derived and fails."""
    try:
        return job.description or ""
    except Exception:
        return ""


def safe_args(job: Job) -> dict | None:
    """First positional arg when it is the request-payload dict, else None."""
    try:
        args = job.args
        if args and len(args) > 0 and isinstance(args[0], dict):
            return args[0]
    except Exception:
        pass
    return None


def status_value(status: Any) -> Any:
    """RQ 2.x returns a `JobStatus` enum; str() on it yields "JobStatus.FINISHED"."""
    return getattr(status, "value", status)


def safe_status(job: Job) -> Any:
    try:
        return status_value(job.get_status())
    except Exception:
        return "unknown"
