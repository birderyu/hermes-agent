"""One wake receipt on the existing cron job, in the scheduler's own lock."""
from datetime import datetime
import hashlib
import time
from zoneinfo import ZoneInfo

from .protocol import BridgeError, dumps, require

ZONE = ZoneInfo('Asia/Shanghai')


def enqueue_wake(config, payload, *, now=None, timeout=2, deadline=None):
    from cron import jobs as cron

    deadline = deadline if deadline is not None else time.monotonic() + timeout
    require(deadline > time.monotonic(), "dependency_unavailable", 503)
    now = now or datetime.now(ZONE)
    day = payload['request']['local_date']
    fingerprint = hashlib.sha256(dumps(payload, sort_keys=True).encode()).hexdigest()
    require(now.astimezone(ZONE).date().isoformat() == day)
    job_id = config.agenda_jobs['weekday' if now.astimezone(ZONE).weekday() < 5 else 'weekend']
    from .cron_lock import scheduler_lock
    with scheduler_lock(max(0, deadline - time.monotonic())):
        require(getattr(cron.get_timezone(), 'key', None) == 'Asia/Shanghai', 'not_configured', 503)
        jobs = cron.load_jobs()
        job = next((job for job in jobs if job['id'] == job_id), None)
        require(job is not None, 'not_configured', 503)
        require(cron.is_job_runnable(job), 'paused_job', 503)
        require(job.get('schedule', {}).get('kind') == 'cron', 'dependency_unavailable', 503)
        receipt = job.get('external_trigger') or {}
        if receipt.get('id') == payload['operation_id']:
            require(receipt.get('business_date') == day and receipt.get('fingerprint') == fingerprint,
                    'request_id_conflict', 409)
            return {'status': 'already_triggered'}
        if receipt.get('business_date') == day:
            return {'status': 'already_triggered'}
        # A fixed fire already due/in progress owns today's occurrence. Persist
        # the wake identity but do not overwrite its next_run_at or manufacture
        # another execution. Gateway day uniqueness remains the final backstop.
        due = cron._parse_aware(job.get('next_run_at'))
        if due is None:
            raise BridgeError()
        busy = bool(job.get('run_claim') or job.get('fire_claim')) or cron._job_running_in_this_process(job_id)
        manual = None if busy or due <= now else now.isoformat()
        receipt = {'id': payload['operation_id'], 'business_date': day, 'manual_run_at': manual,
                   'fingerprint': fingerprint}
        job['external_trigger'] = receipt
        if manual:
            # Do not call trigger_job: it unpauses. This save combines the gate,
            # durable receipt and run-now intent under the same scheduler lock.
            job['manual_run_at'] = manual
            job['manual_run_prompt'] = None
            job['next_run_at'] = manual
        require(time.monotonic() < deadline, "dependency_unavailable", 503)
        cron.save_jobs(jobs)
        return {'status': 'already_triggered' if busy else 'triggered'}
