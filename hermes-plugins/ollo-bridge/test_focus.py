"""A7 focus readback and the retirement of agenda Apple confirmations."""
import asyncio
import copy
from datetime import datetime
from importlib import import_module
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.register_assert_rewrite('ollo_bridge_test_support')
from ollo_bridge_test_support import example

pytest_plugins = ['ollo_bridge_test_support']


@pytest.fixture
def focus(service, monkeypatch):
    module = import_module(service.base + '.focus')
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            # Shanghai is already on the 13th; the runner's UTC date is the 12th.
            return datetime.fromisoformat('2026-10-12T16:30:00Z').astimezone(tz)
    monkeypatch.setattr(module, 'datetime', Clock)
    return module


@pytest.fixture
def integration(bridge, service):
    binding = bridge.plugin.Binding(bridge.home, service.api.config)
    binding.adapter = bridge.api
    api = bridge.plugin.Integration(bridge.home, service.api.config, service.api.client, binding, service.api)
    assert binding.start('tip', 'task', 'turn', 'api_server', '普通对话') == (True, False)
    identity = import_module(service.base + '.identity')
    token = identity.CALL.set({'session_id': 'tip', 'task_id': 'task', 'turn_id': 'turn', 'tool_call_id': 'call'})
    try:
        yield api
    finally:
        identity.CALL.reset(token)


@pytest.mark.parametrize('state', ['missing', 'null', 'confirmed'])
def test_a7_report_page_context_and_derived_schemas_accept_focus(service, state):
    document = example('report-focus-confirmed')
    context = example('context-focus-confirmed')
    for row in (document['report'], context['reports'][0]):
        if state == 'missing':
            row.pop('focus_confirmed')
        elif state == 'null':
            row['focus_confirmed'] = None
    page = {'reports': [document], 'has_more': False}
    for name, value in [('P_Report', document['report']), ('B_BridgeReport', document),
                        ('B_BridgeReportPage', page), ('B_BridgeContext', context)]:
        assert service.protocol.validate(name, value) == value
        validator = service.protocol._VALIDATOR(service.protocol.schema(name), format_checker=service.protocol._CHECKER)
        assert list(validator.iter_errors(value)) == []


@pytest.mark.parametrize('state', [
    {'item_ids': ['a']}, {'item_ids': [], 'confirmed_at': 1},
    {'item_ids': ['a', 'a'], 'confirmed_at': 1}, {'item_ids': ['a', 'b', 'c', 'd'], 'confirmed_at': 1},
    {'item_ids': ['a'], 'confirmed_at': '2026-10-13T08:00Z'},
    {'item_ids': ['a'], 'confirmed_at': -1}, {'item_ids': ['a'], 'confirmed_at': 1, 'permission': True},
])
def test_focus_schema_keeps_unix_seconds_and_selection_constraints(service, state):
    document, context = example('report-focus-confirmed'), example('context-focus-confirmed')
    document['report']['focus_confirmed'] = context['reports'][0]['focus_confirmed'] = state
    for name, value in [('B_BridgeReport', document), ('B_BridgeContext', context)]:
        with pytest.raises(service.protocol.BridgeError) as error:
            service.protocol.validate(name, value)
        assert (error.value.status, error.value.code, error.value.retryable) == (400, 'invalid_request', False)


def test_failure_report_cannot_carry_focus(service):
    document = example('report-failure')
    document['report']['focus_confirmed'] = example('report-focus-confirmed')['report']['focus_confirmed']
    with pytest.raises(service.protocol.BridgeError, match='invalid_request'):
        service.protocol.validate('B_BridgeReport', document)


@pytest.mark.asyncio
@pytest.mark.parametrize('page', [False, True])
async def test_report_tool_shows_current_selection_without_changing_report(integration, gateway, focus, page):
    document = example('report-focus-confirmed')
    report = document['report']
    report['gtd']['agenda']['date'] = '2026-10-13'
    report['gtd']['agenda']['focus'][0]['preselected'] = False
    report['gtd']['agenda']['focus'].append({'id': 'not-selected', 'title': '未确认的候选',
                                            'list': '工作', 'reason': '候选', 'preselected': True})
    report['focus_confirmed']['confirmed_at'] = datetime.fromisoformat('2026-10-12T23:59:00+08:00').timestamp()
    path = '/internal/v1/reports' if page else '/internal/v1/reports/' + report['id']
    response = {'reports': [document], 'has_more': False} if page else document
    before = copy.deepcopy(response)
    gateway.routes[('GET', path)] = (200, response)
    args = {'limit': 6} if page else {'report_id': report['id']}
    result = json.loads(await integration.safe(integration.reports)(args))
    summary = result.pop('focus_summary')
    assert '今天已确认的重点：' in summary and '示例资料' in summary
    assert '未确认的候选' not in summary and '不改提醒事项' in summary
    assert result == before and response == before
    assert [(method, route) for method, route, _, _ in gateway.calls] == [
        ('GET', path + ('?limit=6' if page else ''))]
    # Another device can change selection without changing sequence/raw_body.
    report['focus_confirmed']['item_ids'] = ['not-selected']
    changed = json.loads(await integration.safe(integration.reports)(args))
    summary = changed.pop('focus_summary')
    assert '未确认的候选' in summary and '示例资料' not in summary
    assert changed == response
    original = before['reports'][0] if page else before
    assert document['raw_body'] == original['raw_body']
    assert report['sequence'] == original['report']['sequence']


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['missing', 'null'])
async def test_unconfirmed_tool_output_is_unchanged_despite_preselected(integration, gateway, focus, state):
    document = example('report')
    if state == 'null':
        document['report']['focus_confirmed'] = None
    identifier = document['report']['id']
    gateway.routes[('GET', '/internal/v1/reports/' + identifier)] = (200, document)
    result = json.loads(await integration.safe(integration.reports)({'report_id': identifier}))
    assert result == document and 'focus_summary' not in result


@pytest.mark.asyncio
async def test_items_report_preflight_accepts_focus_and_remains_read_only(service, gateway):
    document = example('report-focus-confirmed')
    identifier = document['report']['id']
    gateway.routes[('GET', '/internal/v1/reports/' + identifier)] = (200, document)
    ids = document['report']['focus_confirmed']['item_ids']
    result = {'items': [{'id': ids[0], 'exists': True, 'completed': False}]}
    service.apple.items = AsyncMock(return_value=result)
    assert await service.api.items({'report_id': identifier, 'ids': ids}) == result
    assert service.apple.items.call_args.args == (ids,)
    service.apple.capture.assert_not_called()
    service.apple.group.assert_not_called()


def test_historical_focus_stays_with_report_date_not_confirmation_date(focus):
    document = example('report-focus-confirmed')
    document['report']['focus_confirmed']['confirmed_at'] = datetime.fromisoformat('2026-10-13T08:00+08:00').timestamp()
    summary = focus.with_focus_summary(document)['focus_summary']
    assert '2026-10-11已确认的重点' in summary and '今天已确认' not in summary
    # Abbreviate only the optional annotation, never titles/originals/raw_body.
    document['report']['gtd']['agenda']['focus'][0]['title'] = '中' * 200
    before = copy.deepcopy(document)
    result = focus.with_focus_summary(document)
    assert '中' * 80 + '…' in result.pop('focus_summary')
    assert result == before and document == before


def test_compact_context_retains_legacy_selection_without_injecting_raw_body(service):
    module = import_module(service.base + '.day_context')
    report = example('report-focus-confirmed')['report']
    value = json.loads(module.compact_context(datetime.fromisoformat('2026-10-13T08:00+08:00'),
                                            None, None, [report]))
    assert value['reports'][0]['focus_confirmed'] == report['focus_confirmed']
    assert value['reports'][0]['writable'] is False
    assert 'raw_body' not in value['reports'][0] and 'body' not in value['reports'][0]


@pytest.mark.asyncio
@pytest.mark.parametrize('upstream_status', [200, 400])
async def test_agenda_confirmation_is_contract_error_before_claim_or_apple(
        bridge, service, integration, gateway, upstream_status):
    from tools.approval_context import set_current_session_key, reset_current_session_key
    document = example('confirmation')
    document['confirmation']['kind'] = 'agenda'
    # The historical shape remains decodable; its execution permission is gone.
    service.protocol.validate('B_BridgeConfirmation', document)
    cid, run_id = document['confirmation_id'], document['run_id']
    path = '/internal/v1/schedule/confirmations/' + cid
    report_path = '/internal/v1/reports/' + document['confirmation']['report_id']
    legacy = example('report-focus-confirmed')
    legacy['report']['id'] = document['confirmation']['report_id']
    gateway.routes[('GET', report_path)] = (200, legacy)
    gateway.routes[('GET', path)] = (upstream_status, document if upstream_status == 200 else {
        'code': 'invalid_request', 'message': '请改用 focus 接口', 'retryable': False})
    bridge.api._active_run_agents[run_id] = SimpleNamespace(session_id='tip', _current_task_id='task', _current_turn_id='turn')
    bridge.api._run_owners[run_id] = 'scope'
    task = asyncio.get_running_loop().create_future()
    bridge.api._active_run_tasks[run_id] = task
    token = set_current_session_key(run_id)
    try:
        integration.binding.requests.remember(bridge.api, 'scope', cid,
            gateway.web.json_response({'run_id': run_id, 'replayed': False}, status=202))
        assert integration.binding.start('tip', 'task', 'turn', 'api_server', '```hermes-plus-gtd-confirm\n{}\n```') == (True, True)
        runtime = integration.binding.runtime(confirmation=True)
        with pytest.raises(service.protocol.BridgeError) as error:
            await service.api.confirm(cid, runtime)
        assert (error.value.status, error.value.code, error.value.retryable) == (400, 'invalid_request', False)
        result = json.loads(await integration.safe(integration.confirmation)({'confirmation_id': cid}))
        assert result == {'error': {'code': 'invalid_request', 'message': 'invalid_request', 'retryable': False}}
        service.apple.group.assert_not_called()
        assert all(method == 'GET' for method, *_ in gateway.calls)
        assert [route for _, route, _, _ in gateway.calls].count(path) == 2
    finally:
        task.set_result(None)
        await asyncio.sleep(0)
        bridge.api._active_run_tasks.pop(run_id, None)
        bridge.api._active_run_agents.pop(run_id, None)
        bridge.api._run_owners.pop(run_id, None)
        reset_current_session_key(token)
