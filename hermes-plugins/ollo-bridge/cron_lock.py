"""Strict bounded variant of the host jobs lock for deadline-bound external calls.

The normal cron writer intentionally degrades on flock failure. An HTTP wake
must instead fail closed and must not mutate jobs after its reply has timed out.
"""
from contextlib import contextmanager
import time

from .protocol import BridgeError


@contextmanager
def scheduler_lock(timeout):
    from cron import jobs as cron

    deadline = time.monotonic() + timeout
    acquired = cron._jobs_file_lock.acquire(timeout=max(0, timeout))
    if not acquired:
        raise BridgeError()
    lock = None
    previous_depth = getattr(cron._jobs_lock_state, 'depth', 0)
    try:
        if previous_depth:
            raise BridgeError()
        cron.ensure_dirs()
        lock = open(cron._jobs_lock_file(), 'a+', encoding='utf-8')
        remaining = deadline - time.monotonic()
        if remaining <= 0 or cron._acquire_flock(lock, remaining) is not True:
            raise BridgeError()
        if time.monotonic() >= deadline:
            raise BridgeError()
        cron._jobs_lock_state.depth = 1
        cron._jobs_lock_state.load_stamp = None
        yield
    finally:
        cron._jobs_lock_state.depth = previous_depth
        cron._jobs_lock_state.load_stamp = None
        if lock is not None:
            cron._release_flock(lock)
        cron._jobs_file_lock.release()
