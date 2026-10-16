"""Bounded, read-only gateway context shared by cron and Ollo turns."""
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from .places import location_summary
from .protocol import BridgeError, dumps, references, require, validate

ZONE = ZoneInfo('Asia/Shanghai')
CONTEXT_CHARS = 6000


async def agenda_day(client, day):
    value = validate('B_BridgeAgendaDay', await client.request('GET', '/internal/v1/schedule/agenda/' + day, timeout=2))
    require(value['business_date'] == day)
    return value


async def accepted_plan(client, day):
    try:
        value = validate('B_DayPlan', await client.request('GET', '/internal/v1/plans/' + day, timeout=2))
    except BridgeError as exc:
        if exc.code == 'not_found':
            return None
        raise
    require(value['date'] == day)
    return value


async def latest_location(client, config, now):
    try:
        value = validate('B_BridgeLatestLocation', await client.request('GET', '/internal/v1/location/latest', timeout=2))
        return location_summary(value['point'], config.knowledge.get('places'), now)
    except BridgeError:
        return {'place': None, 'age_minutes': None, 'sampled_at': None, 'unavailable': True}


async def relevant_reports(client, day, ledger):
    require(ledger is None or ledger['business_date'] == day)
    page = validate('B_BridgeReportPage', await client.request('GET', '/internal/v1/reports?limit=20', timeout=2))
    selected = {}
    agenda_id = ledger['report_id'] if ledger and ledger['status'] == 'generated' else None
    for wrapper in page['reports']:
        report = wrapper['report']
        gtd = report.get('gtd') or {}
        kind = gtd.get('kind')
        if report.get('delivery_kind', 'report') == 'failure':
            continue
        if kind in {'inbox', 'review'} or (kind == 'agenda' and report['id'] == agenda_id):
            selected.setdefault(kind, report)
    if agenda_id and 'agenda' not in selected:
        wrapper = validate('B_BridgeReport', await client.request('GET', '/internal/v1/reports/' + agenda_id, timeout=2))
        report = wrapper['report']
        require(report['id'] == agenda_id and (report.get('gtd') or {}).get('kind') == 'agenda')
        selected['agenda'] = report
    return [selected[k] for k in ('agenda', 'inbox', 'review') if k in selected], page['has_more']


def report_summary(report):
    gtd = report.get('gtd') or {}
    originals = gtd.get('originals') or {}
    # originals alone never expands the allowed reference set. Complete source
    # values remain on the gateway; a truncated display title is not an original.
    ids = references(gtd)
    items = []
    for identifier in sorted(ids & originals.keys()):
        row = originals[identifier]
        items.append({'id': identifier, **{k: row[k] for k in ('source', 'title', 'list', 'due', 'completed')},
                      **{k: row[k] for k in ('start', 'end') if k in row}})
    plan_ids = [card['id'] for card in report.get('cards') or []
                if any(block['type'] == 'plan' for block in card['blocks'])]
    return {'report_id': report['id'], 'kind': gtd['kind'], 'version': gtd['version'],
            'writable': gtd['kind'] != 'agenda' or gtd['version'] == 2,
            'plan_card_ids': plan_ids, 'allowed_items': items,
            'focus_confirmed': report.get('focus_confirmed')}


def compact_context(now, plan, location, reports, issues=(), *, budget=CONTEXT_CHARS):
    value = {'now': now.isoformat(), 'weekday': '星期' + '一二三四五六日'[now.weekday()],
             'plan': plan, 'location': location, 'reports': [], 'omitted': [], 'unavailable': list(issues)}
    pending = []
    for report in reports:
        summary = report_summary(report)
        rows = summary.pop('allowed_items')
        summary['allowed_items'] = []
        value['reports'].append(summary)
        pending.append((summary, rows))
    if plan is not None:
        value['plan'] = {**plan, 'rows': []}
        for row in plan['rows']:
            value['plan']['rows'].append(row)
            if len(dumps(value)) > budget - 1000:
                value['plan']['rows'].pop()
                value['omitted'].append('plan.rows')
                break
    # Report identities survive before their secondary item detail. Never cut
    # JSON or an ID; an omitted item needs a full report read before writing.
    for summary, rows in pending:
        for row in rows:
            summary['allowed_items'].append(row)
            if len(dumps(value)) > budget - 256:
                summary['allowed_items'].pop()
                value['omitted'].append(summary['report_id'])
                break
    while len(dumps(value)) > budget - 256 and value['reports']:
        removed = value['reports'].pop()
        if removed['report_id'] not in value['omitted']:
            value['omitted'].append(removed['report_id'])
    if value['plan']:
        while len(dumps(value)) > budget - 256 and value['plan']['rows']:
            value['plan']['rows'].pop()
            if 'plan.rows' not in value['omitted']:
                value['omitted'].append('plan.rows')
    if value['omitted']:
        value['truncation'] = '次要项已截断；省略项不能引用写入，请按 report_id 读取完整报告。'
    if issues:
        value['availability_note'] = '部分资料暂不可用或未查，不代表没有计划或事项。'
    return dumps(value)


async def conversation_context(client, config, *, now=None):
    now = (now or datetime.now(ZONE)).astimezone(ZONE)
    day, issues = now.date().isoformat(), []
    results = await asyncio.gather(agenda_day(client, day), accepted_plan(client, day),
                                   latest_location(client, config, now), return_exceptions=True)
    values = []
    for name, result in zip(('agenda', 'plan', 'location'), results):
        if isinstance(result, BridgeError):
            issues.append(name)
            result = None
        elif isinstance(result, BaseException):
            raise result
        values.append(result)
    ledger, plan, location = values
    try:
        reports, more = await relevant_reports(client, day, ledger)
        if more:
            issues.append('仅检索最近20份报告；更早报告未查')
    except BridgeError:
        reports = []
        issues.append('reports')
    return compact_context(now, plan, location, reports, issues)
