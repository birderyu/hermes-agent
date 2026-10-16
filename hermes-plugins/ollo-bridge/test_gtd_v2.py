"""G4 read contracts, bounded context and schema-valid candidate cards."""
import copy
from datetime import datetime
from importlib import import_module
import json
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ollo_bridge_test_support import example

pytest_plugins = ['ollo_bridge_test_support']
NOW = datetime.fromisoformat('2026-10-16T21:00:00+08:00')
DAY = '2026-10-16'
RID = '10000000-0000-4000-8000-000000000001'


def plan():
    return {'date': DAY, 'card_id': 'plan-oct16', 'report_id': RID,
            'rows': [{'kind': 'task', 'time': '14:00', 'title': '计划项', 'item_id': 'elsewhere'}],
            'accepted_at': NOW.isoformat(), 'updated_at': NOW.isoformat(), 'revision': 1}


def ledger(generated=False):
    return {'business_date': DAY, 'status': 'generated' if generated else 'not_generated',
            'report_id': RID if generated else None, 'execution_id': RID if generated else None, 'trigger_id': None}


@pytest.fixture
def v2(service, gateway):
    module = import_module(service.base + '.day_context')
    gateway.routes[('GET', '/internal/v1/schedule/agenda/' + DAY)] = (200, ledger())
    gateway.routes[('GET', '/internal/v1/plans/' + DAY)] = (200, plan())
    gateway.routes[('GET', '/internal/v1/location/latest')] = (200, {'point': None})
    gateway.routes[('GET', '/internal/v1/reports')] = (200, {'reports': [], 'has_more': False})
    return module


@pytest.mark.asyncio
async def test_generated_agenda_is_real_host_wake_gate_before_helper(v2, service, bridge, gateway, monkeypatch):
    from cron.scheduler_prompt import _parse_wake_gate
    collector = import_module(service.base + '.collector')
    runner = Mock(side_effect=AssertionError('must not collect on a generated day'))
    monkeypatch.setattr(collector.subprocess, 'run', runner)
    gateway.routes[('GET', '/internal/v1/schedule/agenda/' + DAY)] = (200, ledger(True))
    value = await collector.collect_async(bridge.home, 'agenda', now=NOW)
    assert value['reason'] == 'agenda_already_generated' and value['report_id'] == RID
    assert _parse_wake_gate(collector.encode(value) + '\n') is False
    assert len(gateway.calls) == 1
    runner.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['not_generated', 'triggered'])
async def test_inbox_and_ungenerated_agenda_collect_plan_originals(v2, service, bridge, gateway, monkeypatch, status):
    collector = import_module(service.base + '.collector')
    row = {'id': 'elsewhere', 'source': 'reminder', 'exists': True, 'completed': True, 'title': '计划项',
           'list': '其他清单', 'due': None, 'modified_at': NOW.isoformat(), 'completed_at': NOW.isoformat()}
    original = {k: row[k] for k in ('source', 'completed', 'title', 'list', 'due', 'modified_at')}
    doc = {'items': [row], 'originals': {'elsewhere': original}, 'collected_at': NOW.isoformat(), 'missing_plan_item_ids': []}
    runner = Mock(return_value=SimpleNamespace(stdout=json.dumps(doc).encode(), returncode=0))
    monkeypatch.setattr(collector.subprocess, 'run', runner)
    state = ledger(); state['status'] = status
    if status == 'triggered':
        state['trigger_id'] = RID
    gateway.routes[('GET', '/internal/v1/schedule/agenda/' + DAY)] = (200, state)
    for kind in ('inbox', 'agenda'):
        value = await collector.collect_async(bridge.home, kind, now=NOW)
        request = json.loads(runner.call_args.kwargs['input'])
        assert request['plan_item_ids'] == ['elsewhere']
        assert request['include_completed_today'] is (kind == 'inbox')
        assert value['originals'] == doc['originals'] and value['plan'] == plan()
        assert value['location']['place'] is None and 'wakeAgent' not in value


@pytest.mark.asyncio
async def test_unavailable_plan_does_not_masquerade_as_empty(v2, service, bridge, gateway, monkeypatch):
    collector = import_module(service.base + '.collector')
    runner = Mock(); monkeypatch.setattr(collector.subprocess, 'run', runner)
    gateway.routes[('GET', '/internal/v1/plans/' + DAY)] = (503, {'code': 'dependency_unavailable'})
    with pytest.raises(service.protocol.BridgeError):
        await collector.collect_async(bridge.home, 'inbox', now=NOW)
    runner.assert_not_called()
    gateway.routes[('GET', '/internal/v1/plans/' + DAY)] = (404, {'code': 'not_found'})
    assert await v2.accepted_plan(service.api.client, DAY) is None


def test_places_only_one_note_and_conservative_matching(service, tmp_path):
    places = import_module(service.base + '.places')
    note = tmp_path / 'places.md'
    note.write_text('# 常去地点\n\n| 名称 | 地址 | 纬度 | 经度 | 半径米 |\n| --- | --- | --- | --- | --- |\n'
                    '| 合成地点 | 示例地址 | 0 | 0 | 200 |\n\n[[do-not-follow]]\n', encoding='utf-8')
    point = {'latitude': 0, 'longitude': 0, 'accuracy': 20, 'timestamp': NOW.timestamp() - 3600}
    result = places.location_summary(point, str(note), NOW)
    assert result['place'] == '合成地点' and result['age_minutes'] == 60
    for point_change in ({'accuracy': 201}, {'latitude': 1}, {'timestamp': NOW.timestamp() + 11}):
        assert places.location_summary({**point, **point_change}, str(note), NOW)['place'] is None
    for text in ('broken', note.read_text().replace('| 200 |', '| nan |')):
        note.write_text(text)
        assert places.location_summary(point, str(note), NOW)['place'] is None
    assert places.location_summary(point, str(tmp_path / 'absent'), NOW)['place'] is None


def test_holiday_and_makeup_day_use_only_matching_subscribed_all_day_events(service):
    collector = import_module(service.base + '.collector')
    event = {'id': 'holiday', 'source': 'event', 'title': '国庆节', 'calendar': '中国节假日', 'subscribed': True,
             'all_day': True, 'start': DAY + 'T00:00:00+08:00', 'end': '2026-10-17T00:00:00+08:00'}
    assert collector.workday([event], NOW)['value'] is False
    event['title'] = '补班'
    assert collector.workday([event], NOW)['value'] is True
    event['subscribed'] = False
    assert collector.workday([event], NOW)['source'] == 'weekday_fallback'
    event.update(subscribed=True, start='2026-10-15T00:00:00+08:00', end=DAY + 'T00:00:00+08:00')
    assert collector.workday([event], NOW)['event_ids'] == []


def test_context_budget_preserves_plan_and_anchors_never_adds_original_only_ids(v2):
    report = example('report')['report']
    report['gtd']['version'] = 2
    report['gtd'].pop('agenda')
    report['gtd']['items'] = [{'id': 'fixture-reminder-1', 'source': 'reminder', 'title': '示例资料',
                             'status': 'undecided', 'findings': [], 'suggestion': {'action': 'do', 'title': '示例资料'}}]
    report['gtd']['originals']['not-allowed'] = copy.deepcopy(next(iter(report['gtd']['originals'].values())))
    before = copy.deepcopy(report)
    summary = v2.report_summary(report)
    assert 'not-allowed' not in {row['id'] for row in summary['allowed_items']}
    for i in range(80):
        item = copy.deepcopy(report['gtd']['items'][0]); item['id'] = f'long-{i}'; item['title'] = '长' * 1000
        report['gtd']['items'].append(item)
        original = copy.deepcopy(next(iter(report['gtd']['originals'].values())))
        original['title'] = item['title']; report['gtd']['originals'][item['id']] = original
    text = v2.compact_context(NOW, plan(), {'place': None, 'age_minutes': None}, [report])
    result = json.loads(text)
    assert len(text) <= v2.CONTEXT_CHARS and result['truncation']
    assert result['plan'] == plan() and result['reports'][0]['report_id'] == report['id']
    assert result['reports'][0]['allowed_items'][0]['id'] != 'not-allowed'
    assert before['gtd']['originals']['not-allowed'] == report['gtd']['originals']['not-allowed']


@pytest.mark.asyncio
@pytest.mark.parametrize('session,platform,parent', [('other', 'api_server', ''), ('branch', 'api_server', ''),
    ('reset', 'api_server', ''), ('delegate', 'api_server', ''), ('tip', 'telegram', ''), ('tip', 'api_server', 'parent')])
async def test_context_outside_binding_does_not_fetch_or_read_note(bridge, gateway, session, platform, parent):
    result = await bridge.manager.ainvoke_hook('pre_llm_call', session_id=session, task_id='task', turn_id='turn',
                                             platform=platform, parent_session_id=parent, user_message='现在做什么')
    assert not any(row for row in result if row is not None)
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_context_live_hook_keeps_history_and_injects_time_plan_location(v2, bridge, service, gateway, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)
    monkeypatch.setattr(v2, 'datetime', Clock)
    before = bridge.db.get_messages('tip')
    result = await bridge.manager.ainvoke_hook('pre_llm_call', session_id='tip', task_id='task', turn_id='turn',
                                             platform='api_server', user_message='现在做什么')
    text = result[0]['context']
    assert '星期五' in text and 'plan-oct16' in text and 'age_minutes' in text
    assert len(text) <= 7000 and bridge.db.get_messages('tip') == before
    assert all(method == 'GET' for method, *_ in gateway.calls)


def test_candidate_card_examples_match_exported_g1_schema(service):
    root = Path(__file__).parent / 'docs'
    for filename in ('gtd-skill-v2-candidate.md', 'cron-prompts-v2-candidate.md', 'hermes-plus-gtd-v2-candidate.md'):
        text = (root / filename).read_text()
        documents = re.findall(r'^<!-- schema: (P_OlloCards|P_OlloReportCardsInput) -->\n```ollo-card\n(.*?)\n```',
                               text, re.M | re.S)
        assert documents and len(documents) == len(re.findall(r'^```ollo-card$', text, re.M)), filename
        for schema, raw in documents:
            value = json.loads(raw)
            service.protocol.validate(schema, value)
            if schema == 'P_OlloReportCardsInput':
                assert all('anchor' not in card for card in value['cards'])
                for card in value['cards']:
                    card['anchor'] = {'report_id': RID}
                service.protocol.validate('P_OlloCards', value)


def v2_report():
    wrapper = example('report')
    report = wrapper['report']
    gtd = report['gtd']
    gtd['version'] = 2
    gtd.pop('agenda')
    gtd['items'] = [{'id': identifier, 'source': row['source'], 'title': row['title'], 'list': row['list'],
                     'status': 'undecided', 'findings': [], 'suggestion': {'action': 'do', 'title': row['title']}}
                    for identifier, row in gtd['originals'].items()]
    return wrapper


@pytest.mark.asyncio
async def test_today_ledger_fetches_exact_report_beyond_page_and_does_not_grant_other_originals(v2, service, gateway):
    wrapper = v2_report(); wrapper['report']['id'] = RID
    gateway.routes[('GET', '/internal/v1/reports/' + RID)] = (200, wrapper)
    gateway.routes[('GET', '/internal/v1/reports')] = (200, {'reports': [], 'has_more': True})
    reports, more = await v2.relevant_reports(service.api.client, DAY, ledger(True))
    assert more is True and [r['id'] for r in reports] == [RID]
    summary = v2.report_summary(reports[0])
    assert summary['writable'] is True
    assert {r['id'] for r in summary['allowed_items']} == set(wrapper['report']['gtd']['originals'])
    wrapper['report']['id'] = '20000000-0000-4000-8000-000000000001'
    with pytest.raises(service.protocol.BridgeError):
        await v2.relevant_reports(service.api.client, DAY, ledger(True))


@pytest.mark.asyncio
async def test_v2_agenda_confirmation_reaches_existing_claim_helper_finish_chain(service, gateway):
    from unittest.mock import AsyncMock
    from ollo_bridge_test_support import Operations
    document = example('confirmation')
    document['confirmation']['kind'] = 'agenda'
    report = v2_report()
    report['report']['id'] = document['confirmation']['report_id']
    report['report']['gtd']['originals'] = document['originals']
    report['report']['gtd']['items'] = [
        {'id': identifier, 'source': row['source'], 'title': row['title'], 'status': 'undecided',
         'findings': [], 'suggestion': {'action': 'do', 'title': row['title']}}
        for identifier, row in document['originals'].items()]
    service.protocol.validate('B_BridgeReport', report)
    cid = document['confirmation_id']
    gateway.routes[('GET', '/internal/v1/schedule/confirmations/' + cid)] = (200, document)
    gateway.routes[('GET', '/internal/v1/reports/' + report['report']['id'])] = (200, report)
    operations = Operations(gateway)
    for group in document['groups']:
        operations.add(group['operation_id'], {'kind': 'gtd_group', 'confirmation_id': cid,
            'report_id': report['report']['id'], 'group': group, 'originals': document['originals']})
    async def apply(permit, **kwargs):
        return [{'index': i, 'status': 'done'} for i in permit['group']['indices']]
    service.apple.group = AsyncMock(side_effect=apply)
    runtime = {'source': 'tool', 'session_id': 'tip', 'task_id': document['run_id'], 'tool_call_id': 'call'}
    result = await service.api.confirm(cid, runtime)
    assert all(row['status'] == 'done' for row in result['results'])
    assert len(operations.finishes) == len(document['groups'])


@pytest.mark.asyncio
async def test_missing_binding_store_never_fetches_context(bridge, gateway, monkeypatch):
    monkeypatch.setattr(bridge.api, '_ensure_session_db', lambda: None)
    result = await bridge.manager.ainvoke_hook('pre_llm_call', session_id='tip', task_id='task', turn_id='turn', platform='api_server')
    assert result == [] and gateway.calls == []


def test_knowledge_config_is_one_explicit_absolute_note(service):
    module = import_module(service.base + '.apple_config')
    assert module.knowledge() == {'places': None}
    assert module.knowledge({'places': '/synthetic/places.md'}) == {'places': '/synthetic/places.md'}
    for value in ({'places': 'relative.md'}, {'places': ['/one', '/two']}, {'vault': '/synthetic'}):
        with pytest.raises(ValueError):
            module.knowledge(value)
