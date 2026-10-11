"""Bounded replay on the existing delivery queue for idempotent transports only.

The live router certifies a single target before sending. Merely adding a field
on a job does not opt it in; all existing platforms retain at-most-once handling.
"""
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar

MAX_ATTEMPTS = 5
DELAYS = (1, 5, 30, 120)
_DELIVERY_EXECUTION = ContextVar('cron_delivery_execution', default=None)


@contextmanager
def delivery_attempt(execution_id):
    token = _DELIVERY_EXECUTION.set(execution_id)
    try:
        yield
    finally:
        _DELIVERY_EXECUTION.reset(token)


def enable_for_claim(execution_id):
    from cron import delivery_queue as queue
    with queue._transaction() as conn:
        conn.execute('''UPDATE deliveries SET retry_safe=1
                        WHERE execution_id=? AND status='delivering'
                        AND owner_process_id=? AND owner_pid=?''',
                     (execution_id, queue._PROCESS_ID, os.getpid()))


def retry_failed(conn, execution_id, error):
    """Called in the finish transaction, with current-owner fencing."""
    from cron import delivery_queue as queue
    row = conn.execute('''SELECT attempts FROM deliveries WHERE execution_id=? AND retry_safe=1
                          AND status='delivering' AND owner_process_id=? AND owner_pid=?''',
                       (execution_id, queue._PROCESS_ID, os.getpid())).fetchone()
    if not row or row['attempts'] >= MAX_ATTEMPTS:
        return False
    conn.execute('''UPDATE deliveries SET status='pending', error=?, retry_at=?,
                    owner_process_id=NULL, owner_pid=NULL, owner_started_at=NULL
                    WHERE execution_id=?''',
                 (error, time.time() + DELAYS[max(0, row['attempts'] - 1)], execution_id))
    return True


def recover_claim(conn, execution_id):
    """Only called after the queue has established that no live sender owns it."""
    row = conn.execute('SELECT retry_safe, attempts FROM deliveries WHERE execution_id=?', (execution_id,)).fetchone()
    if not row or not row['retry_safe'] or row['attempts'] >= MAX_ATTEMPTS:
        return False
    conn.execute('''UPDATE deliveries SET status='pending', retry_at=?,
                    owner_process_id=NULL, owner_pid=NULL, owner_started_at=NULL
                    WHERE execution_id=? AND status='delivering' ''',
                 (time.time() + DELAYS[max(0, row['attempts'] - 1)], execution_id))
    return True


def pending_wait_error(conn, execution_id):
    row = conn.execute('SELECT status, retry_safe FROM deliveries WHERE execution_id=?', (execution_id,)).fetchone()
    if row and row['retry_safe'] and row['status'] in {'pending', 'delivering'}:
        return 'Idempotent delivery is still queued or in flight; delivery is not confirmed.'
    return None


def eligible(target, target_count, media_files):
    return (target_count == 1 and not media_files and not target.is_relay
            and getattr(target.runtime_adapter, 'idempotent_delivery', False) is True
            and bool(target.job.get('execution_id')))


def certified_attempt(target):
    from cron.delivery_queue import get_status
    execution_id = str(target.job.get('execution_id') or '')
    if _DELIVERY_EXECUTION.get() != execution_id:
        return False
    row = get_status(execution_id)
    return bool(row and row['status'] == 'delivering' and row['retry_safe'])


def queued_live_delivery(target, target_count, media_files, content, adapters, loop, for_failure):
    """In-process cron also persists before sending a replay-safe transport.

    Try this exact row once now, then let the existing housekeeping drain retry.
    ContextVar authority comes from queue.drain, never from serialized job fields.
    """
    from cron import delivery_queue as queue, scheduler_delivery as delivery
    execution_id = str(target.job.get('execution_id') or '')
    if not eligible(target, target_count, media_files):
        return False, None
    if _DELIVERY_EXECUTION.get() == execution_id:
        enable_for_claim(execution_id)
        return False, None
    queue.enqueue(execution_id, target.job, content, for_failure=for_failure)
    queue.drain(lambda job, text, failure: delivery._deliver_result(
        job, text, adapters=adapters, loop=loop, for_failure=failure), limit=1, execution_id=execution_id)
    result = queue.get_status(execution_id)
    if result and result['status'] in {'delivered', 'suppressed'}:
        return True, None
    return True, (result or {}).get('error') or 'Delivery is queued or in flight; delivery is not confirmed.'
