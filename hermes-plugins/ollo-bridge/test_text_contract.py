"""A5 wire byte limits and lossless titles/originals, without an Apple store."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.register_assert_rewrite('ollo_bridge_test_support')
from ollo_bridge_test_support import example, KEY

pytest_plugins = ['ollo_bridge_test_support']

LIMIT = 2097152


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def sized_items(size):
    # All fields remain valid; necessary control-character escaping can take a
    # 100-item response beyond 2 MiB even with ensure_ascii=False.
    result = {'items': [{'id': f'item-{i}', 'exists': True, 'completed': False, 'title': 'x'} for i in range(100)]}
    remaining = size - len(encode(result))
    for row in result['items']:
        controls = min(4095, remaining // 6)
        row['title'] += '\x01' * controls
        remaining -= controls * 6
        plain = min(4095 - controls, remaining)
        row['title'] += 'a' * plain
        remaining -= plain
    assert remaining == 0 and len(encode(result)) == size
    return result


@pytest.fixture
def response_bodies(bridge, monkeypatch):
    bodies = []
    handle = bridge.app._handle
    async def observe(request):
        response = await handle(request)
        bodies.append(response.body)
        return response
    monkeypatch.setattr(bridge.app, '_handle', observe)
    return bodies


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['emoji_100', 'below_limit', 'at_limit', 'over_limit'])
async def test_items_actual_utf8_response_budget(service, bridge, gateway, response_bodies, monkeypatch, case):
    if case == 'emoji_100':
        result = {'items': [{'id': f'item-{i}', 'exists': True, 'completed': False,
                             'title': '😀' * 1024} for i in range(100)]}
        # The exact repro: every title is legal, ASCII escaping alone exceeds 1 MiB.
        assert len(json.dumps(result).encode()) > 1048576
    else:
        result = sized_items(LIMIT + {'below_limit': -1, 'at_limit': 0, 'over_limit': 1}[case])
    service.protocol.validate('B_ScheduleItemsResponse', result)
    report = example('report')
    report_id = report['report']['id']
    ids = [item['id'] for item in result['items']]
    report['report']['gtd']['agenda']['due'] = [
        {'id': key, 'title': '引用', 'list': '收件箱', 'due': '2026-10-11', 'overdue_days': 0} for key in ids]
    gateway.routes[('GET', '/internal/v1/reports/' + report_id)] = (200, report)
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(encode(result), b'')),
                              wait=AsyncMock(return_value=0))
    # Keep AppleBackend, schema checks and the HTTP encoder real.
    monkeypatch.setattr(service.apple_module.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    status, body = await bridge.client.request('POST', '/v1/ollo-bridge/schedule/items', token=KEY,
                                              body={'report_id': report_id, 'ids': ids})
    if case == 'over_limit':
        assert status == 413 and body == {'code': 'payload_too_large', 'message': 'payload_too_large', 'retryable': False}
        assert 'items' not in body
    else:
        assert status == 200 and body == result
        assert response_bodies[-1] == encode(result) and len(response_bodies[-1]) <= LIMIT
        assert [item['id'] for item in body['items']] == ids
    assert process.communicate.await_count == 1


@pytest.mark.asyncio
async def test_http_items_guard_checks_encoding_even_for_custom_backend(service, bridge, gateway, response_bodies, monkeypatch):
    # Exercise the public response guard independently of the helper's stdout cap.
    result = sized_items(LIMIT + 1)
    ids = [row['id'] for row in result['items']]
    report = example('report')
    report_id = report['report']['id']
    report['report']['gtd']['agenda']['due'] = [
        {'id': key, 'title': '引用', 'list': '收件箱', 'due': '2026-10-11', 'overdue_days': 0} for key in ids]
    gateway.routes[('GET', '/internal/v1/reports/' + report_id)] = (200, report)
    monkeypatch.setattr(bridge.plugin.AppleBackend, 'call', AsyncMock(return_value=result))
    status, body = await bridge.client.request('POST', '/v1/ollo-bridge/schedule/items', token=KEY,
                                              body={'report_id': report_id, 'ids': ids})
    assert status == 413 and body['code'] == 'payload_too_large' and body['retryable'] is False
    assert response_bodies[-1] == encode(body) and 'items' not in body


def title_samples(text):
    report = example('report')['report']['gtd']
    item = example('interpret')['item']
    yield 'P_GTDItem', {**item, 'title': text}
    for finding in item['findings'][:2]:
        yield 'P_GTDFinding', {**finding, 'title': text}
    for change in ({'op': 'update', 'item': 'a'}, {'op': 'merge', 'item': 'a', 'into': 'b'},
                   {'op': 'create_reminder', 'list': '收件箱'}):
        yield 'P_GTDChange', {**change, 'title': text}
    yield 'P_GTDSuggestion', {**item['suggestion'], 'title': text}
    yield 'P_GTDChoices', {**item['choices'], 'titles': [text]}
    for name, kind in [('P_GTDAgendaEvent', 'events'), ('P_GTDAgendaDue', 'due'), ('P_GTDAgendaFocus', 'focus')]:
        yield name, {**report['agenda'][kind][0], 'title': text}
    yield 'P_GTDOriginal', {**report['originals']['fixture-reminder-1'], 'title': text}


@pytest.mark.parametrize('text', ['x' * 513, 'x' * 4096, '中' * 1365 + 'a', '😀' * 1024])
def test_all_gtd_title_fields_accept_full_capture_text(service, text):
    for name, value in title_samples(text):
        assert service.protocol.validate(name, value) == value


@pytest.mark.parametrize('text', ['x' * 4097, '中' * 1365 + 'ab', '😀' * 1024 + 'a'])
def test_gtd_titles_reject_excess_utf8_bytes(service, text):
    for name, value in title_samples(text):
        with pytest.raises(service.protocol.BridgeError, match='payload_too_large') as caught:
            service.protocol.validate(name, value)
        assert caught.value.status == 413 and caught.value.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize('title', ['x' * 513, '😀' * 1024])
async def test_long_original_survives_report_and_confirmation_helper_input(service, gateway, title):
    report = example('report')
    report['report']['gtd']['originals']['fixture-reminder-1']['title'] = title
    report['report']['gtd']['agenda']['due'][0]['title'] = title
    service.protocol.validate('B_BridgeReport', report)
    path = '/internal/v1/reports/' + report['report']['id']
    gateway.routes[('GET', path)] = (200, report)
    assert await service.api.client.request('GET', path) == report
    permit = copy.deepcopy(example('claimed-group')['operation'])
    permit['originals']['fixture-reminder-1']['title'] = title
    service.protocol.validate('B_BridgeOperationClaimResponse', {**example('claimed-group'), 'operation': permit})
    backend = service.apple_module.AppleBackend('/not-executed')
    backend.call = AsyncMock(return_value={'status': 'done'})
    assert [row['status'] for row in await backend.group(permit)] == ['done', 'done']
    assert backend.call.call_args.args[0]['expected']['fixture-reminder-1']['title'] == title


@pytest.mark.asyncio
async def test_report_byte_budget_matches_outbound_utf8(service, gateway):
    from cron import executions, delivery_queue
    from gateway.config import PlatformConfig
    from gateway.platform_registry import platform_registry
    execution = executions.create_execution('fixture-agenda-job', source='test')
    executions.mark_execution_running(execution['id'])
    job = {'id': 'fixture-agenda-job', 'execution_id': execution['id']}
    text = '😀' * 32000  # 128000 UTF-8 bytes, much larger if escaped as surrogate pairs.
    delivery_queue.enqueue(execution['id'], job, text)
    gateway.routes[('POST', '/internal/v1/reports')] = (201, example('report-receipt'))
    adapter = platform_registry.get('ollo').adapter_factory(PlatformConfig())
    result = await adapter.send('main', text, metadata={'job_id': job['id'], 'execution_id': execution['id']})
    assert result.success
    assert gateway.calls[-1][2]['body'] == text
    assert len(gateway.request_bytes[-1]) <= 262144 and text.encode() in gateway.request_bytes[-1]
