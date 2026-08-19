"""Regression tests for shared.spawn_isolation.drain_and_join.

The bug these cover: every worker called ``proc.join()`` before reading the
result queue. ``multiprocessing.Queue.put()`` blocks its feeder thread once the
pipe buffer fills (64 KB on Linux), so the child could not exit until the parent
read — and the parent would not read until the child exited. Any result larger
than the buffer hung both processes forever, taking the queue down with them.

``test_large_result_does_not_deadlock`` is the one that matters: it fails (by
timeout) against the old ordering and passes against the new one.
"""

import sys
from multiprocessing import get_context
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.spawn_isolation import drain_and_join  # noqa: E402

# Comfortably over the 64 KB pipe buffer that triggers the deadlock.
LARGE = 1024 * 1024


def _put_result(q, payload):
    q.put(("ok", {"blob": "x" * payload["size"]}))


def _put_error(q, payload):
    q.put(("err", "boom"))
    raise SystemExit(1)


def _exit_silently(q, payload):
    raise SystemExit(3)


def _crash_hard(q, payload):
    import os

    os.kill(os.getpid(), 9)


def _run(target, size=0, **kwargs):
    ctx = get_context("spawn")
    q = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=target, args=(q, {"size": size}))
    proc.start()
    return proc, drain_and_join(proc, q, operation="test", **kwargs)


@pytest.mark.parametrize("size", [0, 8 * 1024, LARGE])
def test_result_returned_regardless_of_size(size):
    """A result must come back whether or not it exceeds the pipe buffer."""
    proc, (got, status, data) = _run(_put_result, size=size)
    assert got is True
    assert status == "ok"
    assert len(data["blob"]) == size
    assert proc.exitcode == 0


def test_large_result_does_not_deadlock():
    """The actual regression: >64 KB used to hang forever.

    A 30 s cap is far beyond the ~0.1 s this needs, so a timeout here means the
    deadlock is back rather than that the machine is slow.
    """
    proc, (got, status, data) = _run(_put_result, size=LARGE, result_timeout=30)
    assert got is True, "child produced no result -- deadlock regression"
    assert len(data["blob"]) == LARGE
    assert not proc.is_alive(), "child was not reaped"


def test_error_from_child_is_surfaced():
    """A child that reports an error then dies still hands the message back."""
    proc, (got, status, data) = _run(_put_error)
    assert got is True
    assert status == "err"
    assert "boom" in data
    assert proc.exitcode != 0


def test_child_exiting_without_result():
    """got is False, and the caller can still read exitcode to explain why."""
    proc, (got, status, data) = _run(_exit_silently, result_timeout=15)
    assert got is False
    assert status is None
    assert proc.exitcode == 3


def test_child_killed_by_signal():
    """SIGKILL (what the OOM killer sends) must not hang the parent."""
    proc, (got, status, data) = _run(_crash_hard, result_timeout=15)
    assert got is False
    assert proc.exitcode == -9


def test_timeout_when_child_never_finishes():
    """A child that neither sends nor exits is abandoned, not waited on forever."""
    proc, (got, status, data) = _run(_sleep_forever, result_timeout=2)
    assert got is False
    assert not proc.is_alive(), "hung child should have been terminated"


def _sleep_forever(q, payload):
    import time

    time.sleep(3600)
