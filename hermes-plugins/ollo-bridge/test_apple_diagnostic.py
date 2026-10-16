"""Safe failure metadata crosses the helper boundary, never the receipt boundary."""
import copy
from importlib import import_module
import json
import logging
from unittest.mock import AsyncMock

import pytest

pytest.register_assert_rewrite('ollo_bridge_test_support')
from ollo_bridge_test_support import example, Operations

pytest_plugins = ['ollo_bridge_test_support']

PRIVATE = 'PRIVATE_TITLE PRIVATE_NOTES PRIVATE_LIST PRIVATE_ID'


def diagnostic(phase):
    return {'phase': phase, 'domain': 'EKErrorDomain', 'code': 17, 'op': 'update', 'fields': ['list', 'title'],
            'underlyingError': {'domain': 'NSCocoaErrorDomain', 'code': 42}}


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['prepare', 'commit'])
@pytest.mark.parametrize('kind', ['group', 'complete', 'capture'])
async def test_diagnostic_warning_keeps_classification_receipts_and_replay(service, gateway, caplog, phase, kind):
    safe = diagnostic(phase)
    if kind == 'complete':
        safe.update(op='complete', fields=[])
    raw = {**safe, 'fields': [*safe['fields'], PRIVATE, 'item', None, {'notes': PRIVATE}],
           'title': PRIVATE, 'notes': PRIVATE, 'list': PRIVATE, 'id': PRIVATE, 'localizedDescription': PRIVATE,
           'underlyingError': {**safe['underlyingError'], 'localizedDescription': PRIVATE,
                               'userInfo': {'notes': PRIVATE}, 'underlyingError': {'domain': PRIVATE, 'code': 9}}}
    helper = {'error': PRIVATE, 'outcome': 'not_executed' if phase == 'prepare' else 'unknown', 'diagnostic': raw}
    backend = service.apple_module.AppleBackend('/synthetic/helper')
    backend.processes.run = AsyncMock(return_value=(1, json.dumps(helper).encode()))
    service.api.apple = backend
    ops = Operations(gateway)
    if kind in {'group', 'complete'}:
        document = example('confirmation')
        change = {'op': 'update', 'item': 'fixture-reminder-1', 'title': PRIVATE, 'list': '工作', 'by': 'user'}
        if kind == 'complete':
            change = {'op': 'complete', 'item': 'fixture-reminder-1', 'by': 'user'}
        group = {**document['groups'][0], 'indices': [0], 'changes': [change]}
        document['confirmation']['changes'] = [change]
        document['groups'] = [group]
        cid = document['confirmation_id']
        gateway.routes[('GET', '/internal/v1/schedule/confirmations/' + cid)] = (200, document)
        operation_id = group['operation_id']
        permit = {'kind': 'gtd_group', 'confirmation_id': cid, 'report_id': document['confirmation']['report_id'],
                  'group': group, 'originals': document['originals']}
        runtime = {'source': 'tool', 'session_id': 'tip', 'task_id': document['run_id'], 'tool_call_id': 'call'}
        async def invoke():
            return await service.api.confirm(cid, runtime)
    else:
        payload = example('capture')
        payload['request']['text'] = PRIVATE
        operation_id = payload['operation_id']
        permit = {'kind': 'capture', 'request': payload['request']}
        async def invoke():
            with pytest.raises(service.protocol.BridgeError) as error:
                await service.api.capture(payload)
            assert error.value.code == ('not_executed' if phase == 'prepare' else 'write_outcome_unknown')
            assert error.value.retryable is (phase == 'prepare')
            return str(error.value)
    ops.add(operation_id, permit)
    caplog.set_level(logging.WARNING)
    first = await invoke()
    assert await invoke() == first
    backend.processes.run.assert_awaited_once()
    outcome = ops.outcomes[operation_id]
    assert outcome == {
        'kind': permit['kind'], 'status': 'failed_not_executed' if phase == 'prepare' else 'unknown',
        'reason': '执行未启动或提交前被拒绝' if phase == 'prepare' else '写入结果未知，需核对；不可重试写入',
        'retryable': phase == 'prepare'}
    warnings = [r for r in caplog.records if r.name == service.base + '.apple_diagnostic']
    assert len(warnings) == 1 and warnings[0].levelno == logging.WARNING
    message = warnings[0].getMessage()
    assert message.startswith('EventKit failure ')
    assert json.loads(message.removeprefix('EventKit failure ')) == {'operation_id': operation_id, 'diagnostic': safe}
    assert warnings[0].exc_info is None and all(value not in message for value in PRIVATE.split())
    receipts = json.dumps([first, ops.finishes], ensure_ascii=False)
    assert all(text not in receipts for text in (*PRIVATE.split(), 'diagnostic', 'EKErrorDomain', 'NSCocoaErrorDomain'))


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome,returncode', [('unknown', 1), ('unknown', 0), ('not_executed', 1)])
async def test_legacy_helper_without_diagnostic_keeps_exception(service, caplog, outcome, returncode):
    backend = service.apple_module.AppleBackend('/synthetic/helper')
    backend.processes.run = AsyncMock(return_value=(returncode, json.dumps({'error': PRIVATE, 'outcome': outcome}).encode()))
    expected = service.apple_module.NotExecuted if outcome == 'not_executed' else service.apple_module.UnknownWrite
    with pytest.raises(expected) as error:
        await backend.capture(example('capture')['request'])
    assert error.value.diagnostic is None and PRIVATE not in str(error.value)
    assert not caplog.records


@pytest.mark.parametrize('invalid', [None, [], PRIVATE, {}, {'phase': []}, {'op': PRIVATE},
                                   {'op': []}, {'fields': PRIVATE}, {'domain': {}}, {'code': True}, {'code': PRIVATE}])
def test_malformed_diagnostic_cannot_change_failure_classification(service, invalid):
    module = import_module(service.base + '.apple_diagnostic')
    value = {**diagnostic('commit'), **invalid} if isinstance(invalid, dict) and invalid else invalid
    before = copy.deepcopy(value)
    assert module.diagnostic_fields(value) is None
    for cls in (service.apple_module.NotExecuted, service.apple_module.UnknownWrite):
        assert cls('fixed', diagnostic=value).diagnostic is None
    assert value == before
