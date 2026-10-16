"""G4b completion uses the existing group permit, helper and durable receipt chain."""
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ollo_bridge_test_support import example, Operations

pytest_plugins = ['ollo_bridge_test_support']


def confirmation(gateway, mixed=True):
    document = example('confirmation')
    changes = [{'op': 'update', 'item': 'fixture-reminder-1', 'title': '交付资料', 'by': 'hermes'},
               {'op': 'complete', 'item': 'fixture-reminder-1', 'by': 'user'}]
    if not mixed:
        changes = changes[1:]
    group = {**document['groups'][0], 'indices': list(range(len(changes))), 'changes': changes}
    document['confirmation']['changes'], document['groups'] = changes, [group]
    cid = document['confirmation_id']
    gateway.routes[('GET', '/internal/v1/schedule/confirmations/' + cid)] = (200, document)
    permit = {'kind': 'gtd_group', 'confirmation_id': cid, 'report_id': document['confirmation']['report_id'],
              'group': group, 'originals': document['originals']}
    operations = Operations(gateway)
    operations.add(group['operation_id'], permit)
    runtime = {'source': 'tool', 'session_id': 'tip', 'task_id': document['run_id'], 'tool_call_id': 'call'}
    return document, permit, operations, runtime


@pytest.mark.asyncio
@pytest.mark.parametrize('mixed', [False, True])
@pytest.mark.parametrize('helper', [{'status': 'done'}, {'status': 'skipped', 'reason': '确认前已完成或不存在'},
                                   {'status': 'skipped', 'reason': '确认前事项已变化'}])
async def test_complete_shares_one_helper_call_and_replays_real_group_receipt(service, gateway, helper, mixed):
    document, permit, operations, runtime = confirmation(gateway, mixed)
    backend = service.apple_module.AppleBackend('/synthetic/helper')
    backend.processes.run = AsyncMock(return_value=(0, json.dumps(helper).encode()))
    service.api.apple = backend
    before = copy.deepcopy(permit)
    first = await service.api.confirm(document['confirmation_id'], runtime)
    assert first['results'] == [{'index': index, **helper} for index in permit['group']['indices']]
    assert await service.api.confirm(document['confirmation_id'], runtime) == first
    backend.processes.run.assert_awaited_once()
    request = json.loads(backend.processes.run.call_args.args[1])
    assert request['method'] == 'apply' and request['changes'] == permit['group']['changes']
    assert request['expected'] == {'fixture-reminder-1': permit['originals']['fixture-reminder-1']}
    assert permit == before
    assert operations.outcomes[permit['group']['operation_id']] == {
        'kind': 'gtd_group', 'status': 'succeeded', 'results': first['results']}


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['missing', 'event', 'completed'])
async def test_complete_rejects_ineligible_originals_before_claim_or_helper(service, gateway, invalid):
    document, permit, operations, runtime = confirmation(gateway)
    if invalid == 'missing':
        document['originals'].pop('fixture-reminder-1')
    elif invalid == 'event':
        document['confirmation']['changes'][1]['item'] = 'fixture-event-1'
    else:
        document['originals']['fixture-reminder-1']['completed'] = True
    backend = service.apple_module.AppleBackend('/synthetic/helper')
    backend.processes.run = AsyncMock()
    service.api.apple = backend
    with pytest.raises(service.protocol.BridgeError) as failure:
        await service.api.confirm(document['confirmation_id'], runtime)
    assert failure.value.code == 'invalid_request'
    assert len(gateway.calls) == 1 and not operations.finishes
    # The backend also refuses a malformed permit without launching the helper.
    rows = await backend.group(permit)
    assert all(row['status'] == 'skipped' for row in rows)
    backend.processes.run.assert_not_awaited()


def test_completion_and_report_input_examples_match_exported_contract(service):
    root = Path(__file__).parent / 'fixtures/gtd-v2'
    schemas = {'complete-confirmation': 'P_GTDConfirmation', 'template-complete': 'P_GTDConfirmedChangeTemplate',
               'complete-bridge-report': 'B_BridgeReport', 'complete-report-delivery': 'B_BridgeReportDelivery',
               'report-cards-input': 'P_OlloReportCardsInput', 'complete-result': 'P_GTDResult'}
    for name, schema in schemas.items():
        service.protocol.validate(schema, json.loads((root / (name + '.json')).read_text()))
    report_input = json.loads((root / 'report-cards-input.json').read_text())
    with pytest.raises(service.protocol.BridgeError):
        service.protocol.validate('P_OlloCards', report_input)
    # Simulate only the documented input transformation; the gateway owns its transaction.
    saved = copy.deepcopy(report_input)
    for card in saved['cards']:
        card['anchor'] = {'report_id': '10000000-0000-4000-8000-000000000016'}
    service.protocol.validate('P_OlloCards', saved)
    for anchor in (None, {}, {'report_id': 'self'}):
        invalid = copy.deepcopy(report_input)
        invalid['cards'][0]['anchor'] = anchor
        with pytest.raises(service.protocol.BridgeError):
            service.protocol.validate('P_OlloReportCardsInput', invalid)


@pytest.mark.parametrize('field,value', [('by', None), ('due', '2030-01-01'), ('completed', True),
                                       ('title', '不得改名'), ('item', {'$draft': 'selected-item'})])
def test_complete_execution_accepts_only_resolved_op_item_by(service, field, value):
    change = {'op': 'complete', 'item': 'fixture-reminder-1', 'by': 'user'}
    service.protocol.validate('B_GTDConfirmedChange', change)
    change[field] = value
    with pytest.raises(service.protocol.BridgeError):
        service.protocol.validate('B_GTDConfirmedChange', change)
