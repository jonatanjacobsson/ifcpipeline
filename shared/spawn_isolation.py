"""Result handoff for workers that run ifcopenshell in a spawn subprocess.

``multiprocessing.Queue.put()`` does not write to the pipe directly. It hands
the object to a feeder thread, which blocks once the pipe's buffer is full
(64 KB on Linux). A parent that calls ``proc.join()`` before reading the queue
therefore waits for a child that cannot exit until the parent reads — a
permanent deadlock on any result larger than the buffer.

Every worker here runs the same pattern, so every worker had the same bug: the
validation or conversion finishes, the child logs success, and the job never
returns. With one worker per queue, one such job blocks the queue entirely.

``drain_and_join`` reads the result first, then reaps the child. Callers keep
their own error wording — several depend on it (``ifcclash`` phrases its
message so n8n's RETRYABLE_ERROR_PATTERNS classifies the failure as retryable).
"""

import logging
import time
from queue import Empty
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = ["drain_and_join"]


def drain_and_join(
    proc,
    queue,
    *,
    result_timeout: int = 600,
    operation: str = "worker",
    poll_interval: float = 1.0,
    join_timeout: int = 60,
) -> Tuple[bool, Optional[str], Any]:
    """Read the child's result, then reap the child.

    Args:
        proc: a started ``multiprocessing.Process``.
        queue: the ``multiprocessing.Queue`` the child puts ``(status, data)`` on.
        result_timeout: seconds to wait for a result before giving up.
        operation: name used in log lines.
        poll_interval: how often to re-check whether the child is still alive.
        join_timeout: seconds to wait for a clean exit before terminating.

    Returns:
        ``(got, status, data)``. ``got`` is False when the child sent nothing at
        all — the caller decides how to phrase that, and can still inspect
        ``proc.exitcode`` to tell a clean exit from a crash.
    """
    status: Optional[str] = None
    data: Any = None
    got = False
    deadline = time.monotonic() + result_timeout

    while True:
        try:
            status, data = queue.get(timeout=poll_interval)
            got = True
            break
        except Empty:
            if not proc.is_alive():
                # The child is gone. It may still have flushed a result between
                # the timeout above and this check, so look once more.
                try:
                    status, data = queue.get_nowait()
                    got = True
                except Empty:
                    pass
                break
            if time.monotonic() >= deadline:
                logger.error(
                    "%s: no result after %ss, abandoning child",
                    operation,
                    result_timeout,
                )
                break

    proc.join(timeout=join_timeout)

    if not got:
        # The feeder thread can finish flushing the pipe only after is_alive()
        # has already flipped to False, so a result put moments before exit can
        # still be unreadable above. Look once more after the join before
        # declaring the child produced nothing.
        try:
            status, data = queue.get_nowait()
            got = True
        except Empty:
            pass

    if proc.is_alive():
        logger.warning(
            "%s: child still running after result, terminating", operation
        )
        proc.terminate()
        proc.join(timeout=30)
        if proc.is_alive():
            logger.error("%s: child ignored SIGTERM, killing", operation)
            proc.kill()
            proc.join()

    return got, status, data
