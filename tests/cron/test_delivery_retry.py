"""Opt-in durable retries retain the default at-most-once transport policy."""
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def isolated_queue(tmp_path, monkeypatch):
    from cron import delivery_queue as queue
    monkeypatch.setattr(queue, 'DELIVERY_DB', tmp_path / 'deliveries.db')


def test_queue_retries_only_certified_transport_with_same_payload(isolated_queue, monkeypatch):
    from cron import delivery_queue as queue, delivery_retry as retry
    clock = [1000.0]
    monkeypatch.setattr(retry.time, 'time', lambda: clock[0])
    execution_id = str(uuid4())
    job = {'id': 'synthetic', 'execution_id': execution_id}
    queue.enqueue(execution_id, job, 'immutable report')
    calls = []
    def send(job, content, failure):
        retry.enable_for_claim(job['execution_id'])
        calls.append((job, content, failure))
        return 'HTTP unavailable' if len(calls) == 1 else None
    assert queue.drain(send) == 1
    assert queue.get_status(execution_id)['status'] == 'pending'
    assert queue._terminalize_wait_timeout(execution_id)  # queued != delivered
    assert queue.drain(send) == 0
    clock[0] += 10
    assert queue.drain(send) == 1
    assert calls[0] == calls[1]
    assert queue.get_status(execution_id)['status'] == 'delivered'
    # Uncertified transports keep the existing at-most-once behavior.
    other = str(uuid4())
    queue.enqueue(other, {'id': 'other'}, 'once')
    queue.drain(lambda *args: 'offline')
    assert queue.get_status(other)['status'] == 'failed'


@pytest.mark.parametrize('lost_process', [False, True])
def test_queue_recovers_certified_inflight_but_never_retries_arbitrary_send(isolated_queue, monkeypatch, lost_process):
    from cron import delivery_queue as queue, delivery_retry as retry
    for safe in (False, True):
        eid = str(uuid4())
        queue.enqueue(eid, {'id': 'synthetic'}, 'original')
        row = queue.claim_next()
        assert row['execution_id'] == eid
        if safe:
            retry.enable_for_claim(eid)
        queue._ACTIVE_DELIVERIES.discard(eid)
        with monkeypatch.context() as recovered:
            if lost_process:
                recovered.setattr(queue, '_PROCESS_ID', 'replacement-process')
                recovered.setattr(queue, '_owner_is_live', lambda *args: False)
            assert queue.recover_abandoned() == 1
        assert queue.get_status(eid)['status'] == ('pending' if safe else 'unknown')



def test_retries_are_bounded(isolated_queue, monkeypatch):
    from cron import delivery_queue as queue, delivery_retry as retry
    clock = [1000.0]
    monkeypatch.setattr(retry.time, 'time', lambda: clock[0])
    eid = str(uuid4())
    queue.enqueue(eid, {'id': 'synthetic', 'execution_id': eid}, 'report')
    def offline(job, *args):
        retry.enable_for_claim(job['execution_id'])
        return 'offline'
    for _ in range(5):
        assert queue.drain(offline) == 1
        clock[0] += 1000
    assert queue.get_status(eid)['status'] == 'failed'
    assert queue.drain(offline) == 0


@pytest.mark.parametrize('capability,count,media,relay', [
    (False, 1, [], False), (True, 2, [], False),
    (True, 1, [('photo.jpg', 'image')], False), (True, 1, [], True),
])
def test_noneligible_targets_do_not_enter_retry_queue(isolated_queue, capability, count, media, relay):
    from cron import delivery_queue as queue, delivery_retry as retry
    target = SimpleNamespace(job={'id': 'job', 'execution_id': 'exec', 'retry_safe': True},
                             is_relay=relay, runtime_adapter=SimpleNamespace(idempotent_delivery=capability))
    assert retry.queued_live_delivery(target, count, media, 'body', {}, None, False) == (False, None)
    assert queue.get_status('exec') is None


@pytest.mark.parametrize('certified', [False, True])
def test_started_timeout_and_standalone_policy_only_change_for_certified_claim(isolated_queue, monkeypatch, certified):
    import asyncio
    from unittest.mock import AsyncMock, Mock
    from cron import delivery_queue as queue, delivery_retry as retry, scheduler_delivery as delivery
    from gateway.config import Platform
    from gateway.platforms.base import SendResult

    job = {'id': 'job', 'execution_id': 'exec'}
    target = SimpleNamespace(job=job, runtime_adapter=SimpleNamespace(idempotent_delivery=True),
        config=None, target_adapters={}, platform=Platform.TELEGRAM, platform_name='telegram',
        chat_id='123', where='telegram:123', transport=None, loop=None, is_relay=False,
        mirror_text='body', thread_id=None, origin_user_id=None, mirror_this_target=False)
    router = SimpleNamespace(_deliver_to_platform=AsyncMock(return_value=SendResult(success=True)))
    monkeypatch.setattr('gateway.delivery.DeliveryRouter', lambda *args: router)
    def timeout(**kwargs):
        raise TimeoutError()
    def scheduled(coroutine, loop):
        # Transport began, but the caller did not receive its completion signal.
        asyncio.run(coroutine)
        return SimpleNamespace(result=timeout)
    monkeypatch.setattr('agent.async_utils.safe_schedule_threadsafe', scheduled)
    standalone = Mock(return_value=({'success': True}, None))
    monkeypatch.setattr(delivery, '_standalone_send', standalone)
    monkeypatch.setattr(delivery, '_maybe_mirror_cron_delivery', Mock())
    queue.enqueue('exec', job, 'body')
    def send(*args):
        if certified:
            retry.enable_for_claim('exec')
            assert queue._terminalize_wait_timeout('exec')
            assert queue.get_status('exec')['status'] == 'delivering'
        errors = []
        outcome = delivery._live_send_text(target, 'body', None, {}, target_errors=errors,
                                          delivery_errors=[], unverified_targets=[])
        assert outcome == (not certified, True, None)
        fallback_errors = []
        delivery._deliver_standalone(target, 'body', [], errors, fallback_errors)
        assert bool(fallback_errors) is certified
        return 'unconfirmed' if certified else None
    assert queue.drain(send) == 1
    assert standalone.call_count == (0 if certified else 1)
    assert queue.get_status('exec')['status'] == ('pending' if certified else 'delivered')


def test_job_fields_cannot_certify_replay_or_revive_terminal_rows(isolated_queue):
    from cron import delivery_queue as queue
    job = {'id': 'job', 'execution_id': 'exec', 'retry_safe': True, 'idempotent_delivery': True}
    queue.enqueue('exec', job, 'first')
    assert queue.drain(lambda *args: 'offline') == 1
    queue.enqueue('exec', job, 'replacement')
    assert queue.drain(lambda *args: None) == 0
    assert queue.get_status('exec')['status'] == 'failed'
