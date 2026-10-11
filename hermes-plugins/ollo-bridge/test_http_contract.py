"""A5 error policies and the whole interpret deadline, through real routing."""
import asyncio
import json
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.register_assert_rewrite('ollo_bridge_test_support')
from ollo_bridge_test_support import example, KEY

pytest_plugins = ['ollo_bridge_test_support']

# Expectations from the shared contract, including legacy unknown retry policy.
ERRORS = [
    ('invalid_request', 400, False), ('invalid_confirmation', 400, False),
    ('invalid_cursor', 400, False), ('invalid_event_id', 400, False),
    ('unauthorized', 401, False), ('forbidden', 403, False), ('runtime_mismatch', 403, False),
    ('not_found', 404, False), ('expired', 410, False), ('expired', 404, False),
    ('request_id_conflict', 409, False), ('operation_in_progress', 409, True),
    ('write_outcome_unknown', 409, False), ('run_active', 409, True),
    ('stale_execution', 409, False), ('idempotency_conflict', 409, False), ('run_finished', 409, False),
    ('payload_too_large', 413, False), ('not_implemented', 501, False),
    ('invalid_agent_response', 502, None), ('not_configured', 503, False),
    ('dependency_unavailable', 503, True), ('not_executed', 503, True),
    ('paused_job', 503, False), ('invalid_model_output', 503, True),
    ('agent_unavailable', 503, None), ('history_scan_limit', 503, None), ('event_buffer_full', 503, None),
    ('read_timeout', 504, True), ('interpret_timeout', 504, True),
]


@pytest.mark.asyncio
@pytest.mark.parametrize('code,status,retryable', ERRORS)
async def test_gateway_errors_keep_contract_status_and_retry_policy(service, gateway, code, status, retryable):
    # Wrong peer retry hints cannot turn permanent failure into a new write.
    gateway.routes[('GET', '/internal/v1/context')] = (
        status, {'code': code, 'message': 'private upstream text', 'retryable': not retryable})
    with pytest.raises(service.protocol.BridgeError) as caught:
        await service.api.client.request('GET', '/internal/v1/context')
    error = caught.value
    assert (error.code, error.status, error.retryable) == (code, status, retryable)
    expected = {'code': code, 'message': code}
    if retryable is not None:
        expected['retryable'] = retryable
    assert error.response() == expected
    service.protocol.validate('P_Error', error.response())


@pytest.mark.asyncio
@pytest.mark.parametrize('status,raw', [
    (401, b'{"code":"unknown_private_failure"}'), (410, b'not json'),
    (413, b'{"code":[]}'), (503, b'{"message":"private"}'),
    (302, b'{}'), (500, b'{}'), (200, b'{bad'),
])
async def test_hidden_upstream_failures_use_one_legal_503(service, gateway, status, raw):
    async def reply(request, body):
        return gateway.web.Response(body=raw, status=status)
    gateway.routes[('GET', '/internal/v1/context')] = reply
    with pytest.raises(service.protocol.BridgeError) as caught:
        await service.api.client.request('GET', '/internal/v1/context')
    assert caught.value.status == 503
    assert caught.value.response() == {'code': 'dependency_unavailable', 'message': 'dependency_unavailable', 'retryable': True}
    service.protocol.validate('B_OperationError', caught.value.response())


@pytest.mark.asyncio
@pytest.mark.parametrize('peer_status,code,status,retryable', [
    (401, 'dependency_unavailable', 503, True), (503, 'forbidden', 403, False),
    (400, 'not_configured', 503, False), (504, 'unauthorized', 401, False),
])
async def test_peer_status_cannot_create_an_illegal_code_pair(service, gateway, peer_status, code, status, retryable):
    gateway.routes[('GET', '/internal/v1/context')] = (peer_status, {'code': code, 'retryable': not retryable})
    with pytest.raises(service.protocol.BridgeError) as caught:
        await service.api.client.request('GET', '/internal/v1/context')
    assert (caught.value.code, caught.value.status, caught.value.retryable) == (code, status, retryable)


@pytest.fixture
def routed_interpret(bridge, gateway, monkeypatch):
    wire = next(wire for wire, _ in bridge.manager.get_platform_handler_factories('api_server')
                if getattr(wire, '__module__', '').startswith(bridge.plugin.__name__))
    api = wire.__self__.schedule
    model = AsyncMock(return_value=SimpleNamespace(parsed=example('interpret')['request']['current_suggestion']))
    monkeypatch.setattr(api.llm, 'acomplete_structured', model)
    payload, report = example('interpret'), example('report')
    report['report']['id'] = payload['request']['report_id']
    report['report']['gtd_kind'] = report['report']['gtd']['kind'] = 'inbox'
    report['report']['gtd_counts'] = report['report']['gtd']['counts'] = {'pending': 1, 'handled': 0, 'undecided': 1}
    report['report']['gtd'].pop('agenda')
    report['report']['gtd']['items'] = [payload['item']]
    path = '/internal/v1/reports/' + payload['request']['report_id']
    gateway.routes[('GET', path)] = (200, report)
    return SimpleNamespace(api=api, model=model, payload=payload, report=report, path=path)


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', [
    'report_http_504', 'report_transport', 'model', 'model_read_timeout',
    'before_read', 'after_read', 'after_model', 'outer_deadline',
])
async def test_entire_interpret_path_normalizes_timeouts(bridge, gateway, routed_interpret, monkeypatch, phase):
    r = routed_interpret
    module = import_module(bridge.plugin.__name__ + '.schedule')
    protocol = import_module(bridge.plugin.__name__ + '.protocol')
    budget = module.Budget(.2 if phase == 'outer_deadline' else 18)
    monkeypatch.setattr(module, 'Budget', lambda _: budget)
    read_started = []
    if phase == 'report_http_504':
        gateway.routes[('GET', r.path)] = (504, {'code': 'read_timeout', 'retryable': True})
    elif phase in {'report_transport', 'outer_deadline', 'after_read'}:
        async def read(request, body):
            read_started.append(True)
            if phase == 'report_transport':
                raise asyncio.TimeoutError()
            if phase == 'outer_deadline':
                await asyncio.Event().wait()
            budget.deadline = 0
            return gateway.web.json_response(r.report)
        gateway.routes[('GET', r.path)] = read
    elif phase == 'before_read':
        budget.deadline = 0
    elif phase == 'after_model':
        async def answer(**kwargs):
            budget.deadline = 0
            return SimpleNamespace(parsed=r.payload['request']['current_suggestion'])
        r.model.side_effect = answer
    else:
        r.model.side_effect = asyncio.TimeoutError() if phase == 'model' else protocol.BridgeError('read_timeout')
    status, result = await bridge.client.request('POST', '/v1/ollo-bridge/schedule/interpret', token=KEY, body=r.payload)
    assert status == 504
    assert result == {'code': 'interpret_timeout', 'message': 'interpret_timeout', 'retryable': True}
    protocol.validate('B_OperationError', result)
    if phase not in {'model', 'model_read_timeout', 'after_model'}:
        r.model.assert_not_awaited()
    if phase == 'outer_deadline':
        assert read_started == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize('code,status', [('unauthorized', 401), ('expired', 410), ('payload_too_large', 413), ('not_configured', 503)])
async def test_interpret_preserves_permanent_report_read_failures(bridge, gateway, routed_interpret, code, status):
    r = routed_interpret
    gateway.routes[('GET', r.path)] = (status, {'code': code, 'retryable': False})
    actual, result = await bridge.client.request('POST', '/v1/ollo-bridge/schedule/interpret', token=KEY, body=r.payload)
    assert actual == status and result == {'code': code, 'message': code, 'retryable': False}
    r.model.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('title', ['x' * 4097, '中' * 1365 + 'ab', [], ''])
async def test_invalid_model_output_stays_model_error_including_byte_limits(bridge, routed_interpret, title):
    r = routed_interpret
    r.model.return_value = SimpleNamespace(parsed={**r.payload['request']['current_suggestion'], 'title': title})
    status, result = await bridge.client.request('POST', '/v1/ollo-bridge/schedule/interpret', token=KEY, body=r.payload)
    assert status == 503
    assert result == {'code': 'invalid_model_output', 'message': 'invalid_model_output', 'retryable': True}
    r.model.assert_awaited_once()


@pytest.mark.asyncio
async def test_gateway_request_uses_compact_utf8_not_ascii_escapes(service, gateway):
    body = {'text': '中文😀/"\\\n\x01'}
    gateway.routes[('POST', '/internal/v1/location/requests')] = (200, {'status': 'unavailable'})
    await service.api.client.request('POST', '/internal/v1/location/requests', body=body)
    raw = gateway.request_bytes[-1]
    assert raw == json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    assert '中文😀'.encode() in raw and b'\\u4e2d' not in raw and b'\\ud83d' not in raw
    assert b'\\u0001' in raw and json.loads(raw) == body
