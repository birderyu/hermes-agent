"""Real temporary cron store/queue, no scheduler worker, models or live jobs."""
import copy
from datetime import datetime
from importlib import import_module
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

pytest.register_assert_rewrite('ollo_bridge_test_support')
from ollo_bridge_test_support import example

pytest_plugins = ['ollo_bridge_test_support']

ZONE = ZoneInfo('Asia/Shanghai')


@pytest.fixture
def cron_job(bridge, monkeypatch):
    from cron import jobs
    now = datetime(2026, 10, 12, 6, 0, tzinfo=ZONE)
    monkeypatch.setattr(jobs, '_hermes_now', lambda: now)
    monkeypatch.setattr(jobs, 'get_timezone', lambda: ZONE)
    monkeypatch.setattr(jobs, '_job_running_in_this_process', lambda _: False)
    job = jobs.create_job(prompt='synthetic read-only report', schedule='0 8 * * 1-5', deliver='ollo:main')
    config = bridge.plugin.read_config(bridge.home)
    config.agenda_jobs.update(weekday=job['id'], weekend=job['id'])
    payload = example('wake')
    payload['request']['local_date'] = now.date().isoformat()
    return SimpleNamespace(jobs=jobs, job=job, config=config, payload=payload, now=now,
                           wake=import_module(bridge.plugin.__name__ + '.wake').enqueue_wake, error=bridge.plugin.BridgeError)


def test_wake_persists_once_and_preserves_fixed_fallback(cron_job, monkeypatch):
    c = cron_job
    original = c.jobs.get_job(c.job['id'])
    assert c.wake(c.config, c.payload, now=c.now) == {'status': 'triggered'}
    assert c.wake(c.config, c.payload, now=c.now) == {'status': 'already_triggered'}
    conflict = copy.deepcopy(c.payload)
    conflict['request']['device_id'] = str(uuid4())
    with pytest.raises(c.error, match='request_id_conflict'):
        c.wake(c.config, conflict, now=c.now)
    different = copy.deepcopy(c.payload)
    different['operation_id'], different['request']['request_id'], different['request']['device_id'] = [str(uuid4()) for _ in range(3)]
    assert c.wake(c.config, different, now=c.now) == {'status': 'already_triggered'}
    queued = c.jobs.get_job(c.job['id'])
    assert queued['schedule'] == original['schedule']
    assert queued['external_trigger']['id'] == c.payload['operation_id']
    assert queued['next_run_at'] == c.now.isoformat()
    # A failed early execution still leaves the normal 08:00 occurrence.
    c.jobs.mark_job_run(c.job['id'], False, error='synthetic failed generation')
    resumed = c.jobs.get_job(c.job['id'])
    assert datetime.fromisoformat(resumed['next_run_at']).astimezone(ZONE).hour == 8
    tomorrow = c.now.replace(day=13)
    monkeypatch.setattr(c.jobs, '_hermes_now', lambda: tomorrow)
    next_payload = copy.deepcopy(c.payload)
    next_payload['operation_id'] = str(uuid4())
    next_payload['request']['local_date'] = tomorrow.date().isoformat()
    assert c.wake(c.config, next_payload, now=tomorrow)['status'] == 'triggered'


def test_paused_or_late_wake_never_unpauses_or_moves_due_slot(cron_job):
    c = cron_job
    c.jobs.pause_job(c.job['id'])
    before = c.jobs.get_job(c.job['id'])
    with pytest.raises(c.error, match='paused_job') as error:
        c.wake(c.config, c.payload, now=c.now)
    assert error.value.status == 503 and error.value.retryable is False
    assert c.jobs.get_job(c.job['id']) == before
    c.jobs.resume_job(c.job['id'])
    fixed = c.jobs.get_job(c.job['id'])['next_run_at']
    assert c.wake(c.config, c.payload, now=c.now.replace(hour=9))['status'] == 'triggered'
    assert c.jobs.get_job(c.job['id'])['next_run_at'] == fixed
    with pytest.raises(c.error, match='invalid_request'):
        c.wake(c.config, c.payload, now=c.now.replace(day=13))


def test_wake_lock_failure_does_not_leave_a_late_intent(cron_job, monkeypatch):
    c = cron_job
    before = c.jobs.get_job(c.job['id'])
    monkeypatch.setattr(c.jobs, '_acquire_flock', lambda *_: False)
    with pytest.raises(c.error, match='dependency_unavailable'):
        c.wake(c.config, c.payload, now=c.now, timeout=.01)
    assert c.jobs.get_job(c.job['id']) == before


def test_report_identity_uses_exact_execution_and_original_queue_snapshot(cron_job, monkeypatch):
    from cron import executions, delivery_queue
    c = cron_job
    identity = import_module(c.wake.__module__.rsplit('.', 1)[0] + '.execution').report_identity
    monkeypatch.setattr(executions, '_hermes_now', lambda: c.now)
    c.wake(c.config, c.payload, now=c.now)
    snapshot = c.jobs.get_job(c.job['id'])
    execution = executions.create_execution(snapshot['id'], source='test')
    executions.mark_execution_running(execution['id'])
    snapshot['execution_id'] = execution['id']
    delivery_queue.enqueue(execution['id'], snapshot, 'raw fence content')
    before = identity(snapshot['id'], execution['id'])
    assert before == {'delivery_kind': 'report', 'trigger_id': c.payload['operation_id'], 'business_date': c.now.date().isoformat(),
                      'execution_started_at': c.now.isoformat()}
    # A later wake changes jobs.json but not this execution's queued receipt.
    tomorrow = c.now.replace(day=13)
    changed = copy.deepcopy(c.payload)
    changed['operation_id'] = str(uuid4())
    changed['request']['local_date'] = tomorrow.date().isoformat()
    c.wake(c.config, changed, now=tomorrow)
    assert identity(snapshot['id'], execution['id']) == before
    # Fixed fires never inherit the persistent wake, even if its receipt is still present.
    fixed = executions.create_execution(snapshot['id'], source='test', scheduled_instant=tomorrow.isoformat())
    executions.mark_execution_running(fixed['id'])
    delivery_queue.enqueue(fixed['id'], dict(snapshot, execution_id=fixed['id']), 'fixed report')
    assert identity(snapshot['id'], fixed['id']) == {
        'delivery_kind': 'report', 'business_date': tomorrow.date().isoformat(), 'execution_started_at': c.now.isoformat()}
    # A different manual dispatch cannot inherit an old wake either.
    manual = executions.create_execution(snapshot['id'], source='test')
    executions.mark_execution_running(manual['id'])
    delivery_queue.enqueue(manual['id'], dict(snapshot, execution_id=manual['id'], manual_run_at=tomorrow.isoformat()), 'manual')
    assert 'trigger_id' not in identity(snapshot['id'], manual['id'])
    for job_id, execution_id in [('unrelated', execution['id']), (snapshot['id'], str(uuid4()))]:
        with pytest.raises(c.error, match='dependency_unavailable'):
            identity(job_id, execution_id)


@pytest.mark.asyncio
async def test_inprocess_cron_http_failure_enters_host_queue_and_retries_same_execution(bridge, gateway, monkeypatch):
    import asyncio
    from gateway import config as gateway_config
    from gateway.platform_registry import platform_registry
    from cron import executions, delivery_queue, delivery_retry, scheduler_delivery
    from cron.scheduler import drain_delivery_queue
    platform = gateway_config.Platform('ollo')
    pc = gateway_config.PlatformConfig(enabled=True)
    adapter = platform_registry.get('ollo').adapter_factory(pc)
    assert await adapter.connect()
    config = gateway_config.GatewayConfig(platforms={platform: pc})
    monkeypatch.setattr(gateway_config, 'load_gateway_config', lambda: config)
    monkeypatch.delenv('_HERMES_CRON_EXTERNAL_WORKER', raising=False)
    clock = [1000.0]
    monkeypatch.setattr(delivery_retry.time, 'time', lambda: clock[0])
    execution = executions.create_execution('fixture-agenda-job', source='test')
    executions.mark_execution_running(execution['id'])
    job = {'id': 'fixture-agenda-job', 'execution_id': execution['id'], 'deliver': 'ollo:main', 'attach_to_session': False}
    gateway.routes[('POST', '/internal/v1/reports')] = (503, {'code': 'dependency_unavailable', 'retryable': True})
    loop = asyncio.get_running_loop()
    adapters = {platform: adapter}
    error = await asyncio.to_thread(scheduler_delivery._deliver_result, job, 'raw report', adapters, loop)
    assert error and delivery_queue.get_status(execution['id'])['status'] == 'pending'
    sent = [call[2] for call in gateway.calls if call[0] == 'POST']
    assert len(sent) == 1
    gateway.routes[('POST', '/internal/v1/reports')] = (201, example('report-receipt'))
    clock[0] += 10
    assert await asyncio.to_thread(drain_delivery_queue, adapters, loop) == 1
    assert delivery_queue.get_status(execution['id'])['status'] == 'delivered'
    assert [call[2] for call in gateway.calls if call[0] == 'POST'] == sent * 2


@pytest.mark.asyncio
@pytest.mark.parametrize('for_failure', [False, True])
async def test_inprocess_wake_keeps_original_day_trigger_and_category_through_retry(
        bridge, gateway, cron_job, monkeypatch, for_failure):
    import asyncio
    from gateway import config as gateway_config
    from gateway.platform_registry import platform_registry
    from cron import executions, delivery_queue, delivery_retry, scheduler_delivery
    from cron.scheduler import drain_delivery_queue
    c = cron_job
    # Use the adapter's configured job ID, but retain a real temporary job,
    # wake, due scan and fire claim (not a fabricated delivery-only snapshot).
    old_id = c.job['id']
    job = c.jobs.get_job(old_id)
    job.update(id='fixture-agenda-job', attach_to_session=False)
    c.jobs.save_jobs([job], removed_ids={old_id})
    c.config.agenda_jobs.update(weekday=job['id'], weekend=job['id'])
    assert c.wake(c.config, c.payload, now=c.now)['status'] == 'triggered'
    tomorrow = c.now.replace(day=13)
    monkeypatch.setattr(c.jobs, '_hermes_now', lambda: tomorrow)
    monkeypatch.setattr(executions, '_hermes_now', lambda: tomorrow)
    due = c.jobs.get_due_jobs()
    assert len(due) == 1 and due[0]['_scheduled_instant'] is None
    snapshot = c.jobs.claim_job_for_fire(job['id'], return_job=True)
    assert snapshot and snapshot['manual_run_at'] == c.now.isoformat()
    execution = executions.create_execution(job['id'], source='builtin', scheduled_instant=due[0]['_scheduled_instant'])
    executions.mark_execution_running(execution['id'])
    snapshot['execution_id'] = execution['id']
    # A new wake changes the persisted job even before this run first delivers.
    # Delivery must use the claimed execution's copy, not reload jobs.json.
    changed = copy.deepcopy(c.payload)
    changed['operation_id'] = str(uuid4())
    changed['request']['local_date'] = tomorrow.date().isoformat()
    c.wake(c.config, changed, now=tomorrow)
    assert c.jobs.get_job(job['id'])['external_trigger']['id'] == changed['operation_id']
    platform = gateway_config.Platform('ollo')
    pc = gateway_config.PlatformConfig(enabled=True)
    live = platform_registry.get('ollo').adapter_factory(pc)
    assert await live.connect()
    monkeypatch.setattr(gateway_config, 'load_gateway_config',
                        lambda: gateway_config.GatewayConfig(platforms={platform: pc}))
    monkeypatch.delenv('_HERMES_CRON_EXTERNAL_WORKER', raising=False)
    monkeypatch.setattr(delivery_retry, 'DELAYS', (0, 0, 0, 0))
    loop, adapters = asyncio.get_running_loop(), {platform: live}
    # Failure category comes from the scheduler's argument, even when the body
    # looks exactly like a valid agenda and the job contains a forged opposite.
    raw = example('report-delivery')['body']
    snapshot['for_failure'] = not for_failure
    snapshot['delivery_kind'] = 'report' if for_failure else 'failure'
    gateway.routes[('POST', '/internal/v1/reports')] = (503, {'code': 'dependency_unavailable', 'retryable': True})
    assert delivery_queue.get_status(execution['id']) is None
    error = await asyncio.to_thread(scheduler_delivery._deliver_result, snapshot, raw, adapters, loop, for_failure=for_failure)
    assert error
    row = delivery_queue.get_status(execution['id'])
    assert row['status'] == 'pending' and bool(row['for_failure']) is for_failure
    sent = [call[2] for call in gateway.calls if call[0] == 'POST']
    assert len(sent) == 1
    assert sent[0]['trigger_id'] == c.payload['operation_id']
    assert sent[0]['business_date'] == c.now.date().isoformat()
    assert sent[0]['execution_started_at'] == tomorrow.isoformat()
    assert sent[0]['delivery_kind'] == ('failure' if for_failure else 'report')
    assert sent[0]['body'] == raw
    # Changes to the caller's dict cannot rewrite the queued retry either.
    snapshot['external_trigger'] = {'id': str(uuid4()), 'business_date': '2030-01-01'}
    snapshot['manual_run_at'] = tomorrow.isoformat()
    retry_rows = []
    async def accept_retry(request, body):
        # Observe the real queue while the adapter is sending, before the HTTP
        # receipt allows _finish to redact the terminal payload. Assert outside
        # the adapter call so its error handling cannot swallow test failures.
        retry_rows.append(delivery_queue.get_status(execution['id']))
        return gateway.web.json_response(
            example('report-receipt-failure' if for_failure else 'report-receipt'), status=201)
    gateway.routes[('POST', '/internal/v1/reports')] = accept_retry
    assert await asyncio.to_thread(drain_delivery_queue, adapters, loop) == 1
    assert len(retry_rows) == 1 and retry_rows[0] is not None
    assert retry_rows[0]['status'] == 'delivering'
    assert retry_rows[0]['job_json'] == row['job_json']
    assert retry_rows[0]['content'] == row['content'] == raw
    assert retry_rows[0]['for_failure'] == row['for_failure']
    assert [call[2] for call in gateway.calls if call[0] == 'POST'] == sent * 2
    terminal = delivery_queue.get_status(execution['id'])
    assert terminal['status'] == 'delivered'
    assert terminal['job_json'] == '{}' and terminal['content'] == ''
    # Completed deliveries keep only outcome metadata. Re-entry must stop at
    # the queue's idempotency record, without calling bridge on a redacted row.
    assert await asyncio.to_thread(scheduler_delivery._deliver_result, snapshot, raw, adapters, loop,
                                   for_failure=for_failure) is None
    assert len(retry_rows) == 1
    assert [call[2] for call in gateway.calls if call[0] == 'POST'] == sent * 2
    # The fixed fallback keeps its own occurrence and is always a formal report.
    fixed = executions.create_execution(job['id'], source='builtin', scheduled_instant=tomorrow.replace(hour=8).isoformat())
    executions.mark_execution_running(fixed['id'])
    fallback = {**c.jobs.get_job(job['id']), 'execution_id': fixed['id']}
    gateway.routes[('POST', '/internal/v1/reports')] = (201, example('report-receipt'))
    assert await asyncio.to_thread(scheduler_delivery._deliver_result, fallback, raw, adapters, loop) is None
    last = [call[2] for call in gateway.calls if call[0] == 'POST'][-1]
    assert last['delivery_kind'] == 'report' and 'trigger_id' not in last
    assert last['business_date'] == tomorrow.date().isoformat()
