"""How jobs get executed. Both runners expose submit(job_id) / shutdown()."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

log = logging.getLogger("certgen")


class InlineRunner:
    """Runs the job immediately in the calling thread (tests / tiny deployments)."""

    def __init__(self, fn: Callable[[str], None]) -> None:
        self._fn = fn

    def submit(self, job_id: str) -> None:
        self._fn(job_id)

    def shutdown(self) -> None:
        pass


class ThreadRunner:
    """Runs jobs on a small thread pool so the POST request returns immediately."""

    def __init__(self, fn: Callable[[str], None], workers: int) -> None:
        self._fn = fn
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="certgen")

    def submit(self, job_id: str) -> None:
        self._pool.submit(self._run, job_id)

    def _run(self, job_id: str) -> None:
        try:
            self._fn(job_id)
        except Exception:
            # The job stays "processing"; it is re-queued on the next startup and
            # resumes with only the certificates that are still pending.
            log.exception("job %s crashed", job_id)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True, cancel_futures=True)
