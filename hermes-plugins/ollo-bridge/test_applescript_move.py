"""Move receipts and time budgets through the real bridge; Apple is always a double."""
from importlib import import_module
import json
from unittest.mock import AsyncMock

import pytest

pytest.register_assert_rewrite('ollo_bridge_test_support')
from ollo_bridge_test_support import example, Operations

pytest_plugins = ['ollo_bridge_test_support']


def confirmation(gateway):
    document = example('confirmation')
    change = {'op': 'update', 'item': 'fixture-reminder-1', 'list': '工作', 'due': '2030-01-01', 'by': 'user'}
    group = {**document['groups'][0], 'indices': [0], 'changes': [change]}
    document['confirmation']['changes'], document['groups'] = [change], [group]
    cid = document['confirmation_id']
    gateway.routes[('GET', '/internal/v1/schedule/confirmations/' + cid)] = (200, document)
    permit = {'kind': 'gtd_group', 'confirmation_id': cid, 'report_id': document['confirmation']['report_id'],
              'group': group, 'originals': document['originals']}
    ops = Operations(gateway)
    ops.add(group['operation_id'], permit)
    runtime = {'source': 'tool', 'session_id': 'tip', 'task_id': document['run_id'], 'tool_call_id': 'call'}
    return cid, runtime, permit, ops


@pytest.mark.asyncio
@pytest.mark.parametrize('code,reason,outcome', [
    (-1743, 'move_permission', 'not_executed'), (-1712, 'move_timeout', 'unknown'),
    (-600, 'move_app_unavailable', 'not_executed'), (-609, 'move_connection_lost', 'unknown'),
    (1, 'move_unresolved', 'unknown'), (-1743, 'move_partial', 'unknown'),
])
async def test_move_failure_receipt_classification_and_replay(service, gateway, caplog, code, reason, outcome):
    cid, runtime, permit, ops = confirmation(gateway)
    backend = service.apple_module.AppleBackend('/synthetic/helper')
    helper = {'error': 'PRIVATE_TITLE', 'outcome': outcome, 'reason_code': reason,
              'diagnostic': {'domain': 'OlloAppleScript', 'code': code, 'phase': 'move', 'op': 'update',
                             'fields': ['list', 'due', 'PRIVATE_NOTES'], 'userInfo': {'title': 'PRIVATE_TITLE'}}}
    backend.processes.run = AsyncMock(return_value=(1, json.dumps(helper).encode()))
    service.api.apple = backend
    first = await service.api.confirm(cid, runtime)
    assert await service.api.confirm(cid, runtime) == first
    backend.processes.run.assert_awaited_once()
    receipt = ops.outcomes[permit['group']['operation_id']]
    assert receipt['status'] == ('failed_not_executed' if outcome == 'not_executed' else 'unknown')
    assert receipt['retryable'] is (outcome == 'not_executed')
    assert receipt['reason'] == service.apple_module.MOVE_REASONS[reason]
    assert first['results'] == [{'index': 0, 'status': 'failed', 'reason': receipt['reason']}]
    assert 'PRIVATE' not in json.dumps([ops.finishes, first]) + caplog.text
    assert '"phase":"move"' in caplog.text


@pytest.mark.asyncio
async def test_move_success_omits_reason_and_has_cold_start_budget(service, gateway):
    cid, runtime, permit, ops = confirmation(gateway)
    backend = service.apple_module.AppleBackend('/synthetic/helper')
    helper = {'status': 'done'}
    limits = []
    async def run(executable, data, budget, key):
        limits.append(budget.remaining())
        assert json.loads(data)['changes'] == permit['group']['changes']
        return 0, json.dumps(helper).encode()
    backend.processes.run = AsyncMock(side_effect=run)
    service.api.apple = backend
    first = await service.api.confirm(cid, runtime)
    assert first['results'] == [{'index': 0, 'status': 'done'}]
    assert await service.api.confirm(cid, runtime) == first
    assert len(limits) == 1 and 55 < limits[0] <= 58
    assert ops.outcomes[permit['group']['operation_id']] == {
        'kind': 'gtd_group', 'status': 'succeeded', 'results': [{'index': 0, 'status': 'done'}]}


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [TimeoutError, BrokenPipeError])
async def test_parent_timeout_or_broken_pipe_after_move_is_never_retryable(service, gateway, failure):
    cid, runtime, permit, ops = confirmation(gateway)
    backend = service.apple_module.AppleBackend('/synthetic/helper')
    backend.processes.run = AsyncMock(side_effect=failure())
    service.api.apple = backend
    await service.api.confirm(cid, runtime)
    receipt = ops.outcomes[permit['group']['operation_id']]
    assert receipt['status'] == 'unknown' and receipt['retryable'] is False
    backend.processes.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_insufficient_move_budget_rejects_before_launch_and_finishes_receipt(service, gateway):
    _, runtime, permit, ops = confirmation(gateway)
    schedule = import_module(service.base + '.schedule')
    execute = AsyncMock()
    outcome = await service.api.operation(permit['group']['operation_id'], runtime, permit, execute,
                                          schedule.Budget(58), 58)
    execute.assert_not_awaited()
    assert outcome['status'] == 'failed_not_executed' and outcome['retryable'] is True
    assert outcome['reason'] == service.apple_module.MOVE_REASONS['move_budget']
    assert len(ops.finishes) == 1


def test_only_list_updates_extend_budget_including_merge_target(service):
    limits = service.apple_module.group_limits
    for change in ({'op': 'update', 'due': '2030-01-01'}, {'op': 'update', 'list': None},
                   {'op': 'delete'}, {'op': 'to_event'}, {'op': 'create_reminder', 'list': '工作'}):
        assert limits([change]) == (14, 10)
    changes = [{'op': 'merge', 'item': 'a', 'into': 'b'}, {'op': 'update', 'item': 'b', 'list': '工作'}]
    seconds, helper = limits(changes)
    assert helper > 55 and seconds >= helper + 4 and seconds < 90


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['PRIVATE_REASON', {}, None])
async def test_untrusted_reason_does_not_escape_helper_boundary(service, reason):
    backend = service.apple_module.AppleBackend('/synthetic/helper')
    backend.processes.run = AsyncMock(return_value=(1, json.dumps(
        {'error': 'PRIVATE_ERROR', 'outcome': 'unknown', 'reason_code': reason}).encode()))
    with pytest.raises(service.apple_module.UnknownWrite) as error:
        await backend.group(example('claimed-group')['operation'])
    assert str(error.value) == 'write_outcome_unknown'
