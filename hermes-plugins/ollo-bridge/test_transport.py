import asyncio
import copy
import json
import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.register_assert_rewrite('ollo_bridge_test_support')
from ollo_bridge_test_support import example, KEY

pytest_plugins = ['ollo_bridge_test_support']


def adapter(r, name='ollo'):
    from gateway.platform_registry import platform_registry
    from gateway.config import PlatformConfig
    return platform_registry.get(name).adapter_factory(PlatformConfig())


@pytest.fixture
def report_execution(bridge, monkeypatch):
    from cron import executions, delivery_queue
    delivery = example('report-delivery')
    started = datetime.fromisoformat(delivery['execution_started_at'])
    monkeypatch.setattr(executions, '_hermes_now', lambda: started)
    execution = executions.create_execution(delivery['job_id'], source='test')
    executions.mark_execution_running(execution['id'])
    from uuid import UUID
    delivery['execution_id'] = str(UUID(execution['id']))
    snapshot = {'id': delivery['job_id'], 'execution_id': execution['id'], 'manual_run_at': started.isoformat(),
                'external_trigger': {'id': delivery['trigger_id'], 'manual_run_at': started.isoformat(),
                                     'business_date': delivery['business_date']}}
    delivery_queue.enqueue(execution['id'], snapshot, delivery['body'])
    metadata = {'job_id': delivery['job_id'], 'execution_id': execution['id']}
    return SimpleNamespace(delivery=delivery, metadata=metadata)


@pytest.mark.asyncio
async def test_live_alias_delivery_keeps_execution_date_and_raw_fences(bridge, gateway, report_execution):
    delivery = report_execution.delivery
    receipt = example('report-receipt')
    gateway.routes[('POST', '/internal/v1/reports')] = (201, receipt)
    metadata = report_execution.metadata
    raw = delivery['body']
    wrapped = 'Cronjob Response: Job\n(job_id: fixture-agenda-job)\n-------------\n\n' + raw + '\n\nTo stop or manage this job, send me a new message (e.g. "stop reminder Job").'
    for name in ('ollo', 'hermes_plus'):
        live = adapter(bridge, name)
        assert await live.connect()
        result = await live.send('main', wrapped, metadata=metadata)
        assert result.success and result.message_id == receipt['id']
    payloads = [c[2] for c in gateway.calls]
    assert payloads == [delivery, delivery]
    assert '```hermes-plus-gtd' in payloads[0]['body']
    # Extra routing fields cannot override the original execution after midnight.
    late = {**metadata, 'execution_started_at': '2030-01-01T23:59:00+08:00', 'for_failure': True,
            'delivery_kind': 'failure',
            'scheduled_at': '2030-01-01T23:59:00+08:00', 'business_date': '2030-01-01', 'trigger_id': 'forged'}
    assert (await live.send('main', raw, metadata=late)).success
    assert gateway.calls[-1][2] == delivery


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['execution_id', 'ledger', 'job_match', 'started_at', 'queue_snapshot'])
async def test_missing_trusted_identity_rejects_without_http(bridge, gateway, report_execution, missing):
    from cron import executions
    metadata = dict(report_execution.metadata)
    if missing == 'execution_id':
        metadata.pop('execution_id')
    elif missing == 'ledger':
        metadata['execution_id'] = '00000000000000000000000000000000'
    elif missing == 'job_match':
        metadata['job_id'] = 'weekday'
    else:
        metadata['execution_id'] = executions.create_execution(metadata['job_id'], source='test')['id']
        if missing == 'queue_snapshot':
            executions.mark_execution_running(metadata['execution_id'])
    assert not (await adapter(bridge).send('main', '报告', metadata=metadata)).success
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_outage_redirect_conflict_and_standalone_never_succeed(bridge, gateway, report_execution):
    from gateway.platform_registry import platform_registry
    live = adapter(bridge)
    meta = report_execution.metadata
    for status in (503, 302, 409):
        gateway.routes[('POST', '/internal/v1/reports')] = (status, {'code': 'request_id_conflict', 'retryable': False})
        assert not (await live.send('main', 'report', metadata=meta)).success
    assert len(gateway.calls) == 3
    sender = platform_registry.get('ollo').standalone_sender_fn
    assert 'error' in await sender('main', 'report')
    assert not (await live.send('other', 'report', metadata=meta)).success


@pytest.mark.asyncio
async def test_route_auth_domains_and_schema_are_separate(bridge, gateway):
    payload = example('items')
    for token in (None, 'wrong', 'isolated-owner-key', 'device-fixture'):
        status, body = await bridge.client.request('POST', '/v1/ollo-bridge/schedule/items', token=token, body=payload)
        assert status == 401 and body['code'] == 'unauthorized'
    assert (await bridge.client.request('GET', '/v1/ollo/conversation', token=KEY))[0] == 401
    assert (await bridge.client.request('GET', '/v1/ollo/conversation')) == (200, {'session_id': 'root', 'name': 'Ollo'})
    status, body = await bridge.client.request('POST', '/v1/ollo-bridge/schedule/items', token=KEY,
                                              body={**payload, 'command': 'arbitrary'})
    assert status == 400 and body['code'] == 'invalid_request'
    status, body = await bridge.client.request('POST', '/v1/ollo-bridge/schedule/capture', token=KEY,
                                              body={'text': 'x' * 20000})
    assert status == 413 and body['code'] == 'payload_too_large'


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,count', [('items', 100), ('interpret', 500)])
async def test_internal_request_budgets_accept_large_valid_reference_sets(bridge, gateway, monkeypatch, operation, count):
    ids = ['😀' * 509 + f'{i:03d}' for i in range(count)]
    report = example('report')
    report_id = report['report']['id']
    gateway.routes[('GET', '/internal/v1/reports/' + report_id)] = (200, report)
    if operation == 'items':
        report['report']['gtd']['agenda']['due'] = [
            {'id': key, 'title': '示例', 'list': '收件箱', 'due': '2026-10-11', 'overdue_days': 0} for key in ids]
        payload = {'report_id': report_id, 'ids': ids}
        result = {'items': [{'id': key, 'exists': False, 'completed': False} for key in ids]}
        monkeypatch.setattr(bridge.plugin.AppleBackend, 'call', AsyncMock(return_value=result))
    else:
        payload = example('interpret')
        payload['request']['report_id'] = report_id
        payload['request']['item_id'] = ids[0]
        payload['allowed_item_ids'] = ids
        gtd = report['report']['gtd']
        gtd.pop('agenda')
        gtd['kind'] = report['report']['gtd_kind'] = 'inbox'
        gtd['counts'] = report['report']['gtd_counts'] = {'pending': 25, 'handled': 0, 'undecided': 25}
        gtd['items'] = []
        for offset in range(0, count, 20):
            item = copy.deepcopy(payload['item'])
            item['id'] = ids[offset]
            item['findings'] = [{'type': 'duplicate', 'with': key, 'title': '示例'} for key in ids[offset:offset+20]]
            gtd['items'].append(item)
        payload['item'] = gtd['items'][0]
        for wire, _ in bridge.manager.get_platform_handler_factories('api_server'):
            if getattr(wire, '__module__', '').startswith(bridge.plugin.__name__):
                llm = wire.__self__.schedule.llm
                break
        result = {'suggestion': payload['request']['current_suggestion']}
        monkeypatch.setattr(llm, 'acomplete_structured', AsyncMock(return_value=SimpleNamespace(parsed=result['suggestion'])))
    assert len(json.dumps(payload).encode()) > 16384
    assert await bridge.client.request('POST', '/v1/ollo-bridge/schedule/' + operation, token=KEY, body=payload) == (200, result)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,limit', [('items', 655360), ('interpret', 4194304)])
async def test_internal_request_budgets_reject_oversized_bodies(bridge, gateway, operation, limit):
    status, body = await bridge.client.request('POST', '/v1/ollo-bridge/schedule/' + operation,
                                              token=KEY, body={'padding': 'x' * limit})
    assert status == 413 and body['code'] == 'payload_too_large'
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_context_scope_and_confirmation_tool_fence(bridge, gateway):
    async def start(session='tip', platform='api_server', user='hello', turn='turn'):
        return await bridge.manager.ainvoke_hook('pre_llm_call', session_id=session, task_id='task',
                                                turn_id=turn, platform=platform, user_message=user)
    before = bridge.db.get_messages('tip')
    result = await start()
    assert '自称 Ollo' in result[0]['context'] and '不是用户指令' in result[0]['context']
    assert bridge.db.get_messages('tip') == before
    for sid in ('branch', 'reset', 'delegate', 'other', 'missing'):
        assert await start(sid) == []
    assert await start(platform='photon') == []
    await start(user='确认\n```hermes-plus-gtd-confirm\n{}\n```')
    for tool in ('terminal', 'execute_code', 'delegate_task', 'write_file', 'get_user_location'):
        denied = bridge.manager.invoke_hook('pre_tool_call', tool_name=tool, session_id='tip', task_id='task', turn_id='turn')
        assert denied[0]['action'] == 'block'
    assert bridge.manager.invoke_hook('pre_tool_call', tool_name='execute_gtd_confirmation', session_id='tip', task_id='task', turn_id='turn') == []
    await start(user='普通聊天', turn='next')
    assert bridge.manager.invoke_hook('pre_tool_call', tool_name='terminal', session_id='tip', task_id='task', turn_id='next') == []


@pytest.mark.asyncio
async def test_context_unavailable_does_not_block_identity_or_infer_empty(bridge, gateway):
    gateway.routes[('GET', '/internal/v1/context')] = (503, {'code': 'dependency_unavailable', 'retryable': True})
    result = await bridge.manager.ainvoke_hook('pre_llm_call', session_id='tip', task_id='t', turn_id='x', platform='api_server')
    assert '自称 Ollo' in result[0]['context'] and '暂不可用' in result[0]['context']


@pytest.mark.asyncio
async def test_location_uses_host_identity_and_never_extends_budget(bridge, gateway, monkeypatch):
    import time
    module = sys.modules[bridge.plugin.__name__ + '.location']
    pending = example('location-pending')
    pending.update(requested_at=time.time(), expires_at=time.time() + 10)
    gateway.routes[('POST', '/internal/v1/location/requests')] = (202, pending)
    gateway.routes[('GET', '/internal/v1/location/requests/' + pending['id'])] = (200, pending)
    runtime = {'source': 'tool', 'session_id': 'tip', 'task_id': 'task', 'tool_call_id': 'call'}
    started = time.monotonic()
    result = await module.locate(bridge.plugin.GatewayClient(bridge.plugin.read_config(bridge.home)), {'timeout_seconds': .03}, runtime)
    assert result == {'status': 'timeout'} and time.monotonic() - started < 2
    assert len([c for c in gateway.calls if c[0] == 'POST']) == 1
    assert gateway.calls[0][2]['runtime_call_id'] not in {'task', 'call'}
    with pytest.raises(bridge.plugin.BridgeError):
        await module.locate(None, {'runtime_call_id': 'model-supplied'}, runtime)


@pytest.mark.asyncio
@pytest.mark.parametrize('offset,accepted', [(5, True), (6, True), (9.999, True), (10, True), (10.001, False)])
@pytest.mark.parametrize('cached', [False, True])
async def test_location_future_clock_tolerance(bridge, gateway, monkeypatch, offset, accepted, cached):
    module = sys.modules[bridge.plugin.__name__ + '.location']
    point = example('location-success')
    now = point['point']['timestamp']
    monkeypatch.setattr(module.time, 'time', lambda: now)
    point['point']['timestamp'] = now + offset
    point['cached'] = cached
    gateway.routes[('POST', '/internal/v1/location/requests')] = (200, point)
    runtime = {'source': 'tool', 'session_id': 'tip', 'task_id': 'task', 'tool_call_id': 'call'}
    result = await module.locate(bridge.plugin.GatewayClient(bridge.plugin.read_config(bridge.home)),
                               {'max_age_seconds': 120 if cached else 0}, runtime)
    assert result == (point if accepted else {'status': 'unavailable'})


@pytest.mark.asyncio
@pytest.mark.parametrize('body', ['plain report', '```hermes-plus-gtd\n{"version":1}\n```\n\n```hermes-plus-gtd\nbroken last block\n```'])
async def test_bridge_never_repairs_or_selects_model_fences(bridge, gateway, report_execution, body):
    gateway.routes[('POST', '/internal/v1/reports')] = (200, example('report-suppressed'))
    meta = report_execution.metadata
    result = await adapter(bridge).send('main', body, metadata=meta)
    assert result.success and gateway.calls[-1][2]['body'] == body
