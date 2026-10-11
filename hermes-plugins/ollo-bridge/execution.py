"""Resolve report identity from existing host stores, never from report text."""
from datetime import datetime
import json
from zoneinfo import ZoneInfo

from .protocol import require, validate

ZONE = ZoneInfo('Asia/Shanghai')


def business_day(instant):
    validate('B_ISODateTime', instant)
    return datetime.fromisoformat(instant.replace('Z', '+00:00')).astimezone(ZONE).date().isoformat()


def report_identity(job_id, execution_id):
    from cron.executions import get_execution
    from cron.delivery_queue import get_status

    # Host IDs are UUID hex; only normalize for the wire, after the exact lookup.
    execution = get_execution(execution_id)
    require(execution is not None and execution['job_id'] == job_id, 'dependency_unavailable', 503)
    started = execution.get('started_at')
    validate('B_ISODateTime', started)
    scheduled = execution.get('scheduled_instant')
    queued = get_status(execution_id)
    # Both detached workers and the in-process idempotent lane enqueue BEFORE
    # sending. A missing/pruned row cannot prove either wake identity or failure
    # category; never turn that absence into a new-day successful report.
    require(queued is not None and queued.get('job_json') and queued.get('for_failure') in (0, 1),
            'dependency_unavailable', 503)
    snapshot = json.loads(queued['job_json'])
    require(snapshot.get('id') == job_id and snapshot.get('execution_id') == execution_id)
    result = {'execution_started_at': started, 'business_date': business_day(scheduled or started),
              'delivery_kind': 'failure' if queued['for_failure'] else 'report'}
    trigger = snapshot.get('external_trigger')
    if (not scheduled and isinstance(trigger, dict) and trigger.get('manual_run_at')
            and trigger['manual_run_at'] == snapshot.get('manual_run_at')):
        # The original queued job survives retries/restarts and later wakes.
        # Never consult today's mutable jobs.json or guess by arrival time.
        require(trigger.get('business_date') == business_day(trigger['manual_run_at']))
        result.update(trigger_id=trigger['id'], business_date=trigger['business_date'])
    return result
