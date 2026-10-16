"""Read-only cron collection using the owning profile's authenticated bridge."""
import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

from .apple_config import names, scopes
from .config import read_config
from .day_context import agenda_day, accepted_plan, latest_location, relevant_reports, report_summary
from .http_client import GatewayClient
from .protocol import BridgeError, validate

ZONE = ZoneInfo('Asia/Shanghai')
MAX_ITEMS = 500
MAX_BYTES = 16 * 1024 * 1024


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def collect_request(config, kind, now, plan=None):
    scope = scopes(config.get('collection'))[kind]
    apple = names(config.get('apple'))
    now = now.astimezone(ZONE)
    start = now if kind == 'review' else now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7 if kind == 'review' else 1)
    return {'method': 'collect', **scope, 'apple': apple, 'start': start.isoformat(), 'end': end.isoformat(),
            'include_completed_today': kind == 'inbox',
            'plan_item_ids': sorted({row['item_id'] for row in (plan or {}).get('rows', []) if row.get('item_id')})}


def validate_collection(value):
    if not isinstance(value, dict) or set(value) != {'items', 'originals', 'collected_at', 'missing_plan_item_ids'}:
        raise ValueError('invalid_collection')
    rows, originals = value['items'], value['originals']
    if not isinstance(rows, list) or not isinstance(originals, dict):
        raise ValueError('invalid_collection')
    if len(rows) > MAX_ITEMS:
        raise ValueError('collection_limit_exceeded')
    if not isinstance(value['collected_at'], str) or datetime.fromisoformat(value['collected_at'].replace('Z', '+00:00')).utcoffset() is None:
        raise ValueError('invalid_collection_time')
    expected, ids = {}, set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']
                or row['id'] in ids or row.get('exists') is not True or type(row.get('completed')) is not bool
                or row.get('source') not in {'reminder', 'event'} or not isinstance(row.get('title'), str)
                or not isinstance(row.get('list'), str)):
            raise ValueError('invalid_collection_item')
        ids.add(row['id'])
        validate('B_ItemID', row['id'])
        if row['source'] == 'event':
            validate('B_ISODateTime', row.get('start'))
            validate('B_ISODateTime', row.get('end'))
            if (not isinstance(row.get('calendar'), str) or not isinstance(row.get('location'), str)
                    or type(row.get('all_day')) is not bool or type(row.get('subscribed')) is not bool
                    or datetime.fromisoformat(row['end']) <= datetime.fromisoformat(row['start'])):
                raise ValueError('invalid_collection_event')
        if row.get('modified_at') is not None:
            expected[row['id']] = {k: v for k, v in row.items()
                                   if k in {'source', 'title', 'list', 'due', 'completed', 'modified_at', 'start', 'end'}}
    if originals != expected:
        raise ValueError('invalid_collection_originals')
    validate('B_GTDOriginals', originals)
    missing = value['missing_plan_item_ids']
    if not isinstance(missing, list) or any(not isinstance(v, str) or v in ids for v in missing) or len(set(missing)) != len(missing):
        raise ValueError('invalid_missing_plan_items')
    return value


def helper_collection(config, kind, now, plan):
    helper = config.helper_path
    if not isinstance(helper, str) or not Path(helper).is_absolute():
        raise ValueError('helper_not_configured')
    request = collect_request({'apple': config.apple, 'collection': config.collection}, kind, now, plan)
    # run() kills and reaps on timeout. The helper also has its independent 7s
    # watchdog; no shell, retry, model, or write method exists on this path.
    result = subprocess.run([helper], input=encode(request).encode('utf-8'),
                            capture_output=True, timeout=6, check=False)
    if len(result.stdout) > MAX_BYTES:
        raise ValueError('collection_output_too_large')
    value = json.loads(result.stdout)
    if result.returncode:
        reason = 'collection_limit_exceeded' if isinstance(value, dict) and value.get('error') == 'collection_limit_exceeded' else 'helper_failed'
        raise ValueError(reason)
    return validate_collection(value)


def workday(items, now):
    today = now.date()
    evidence = []
    for row in items:
        if row['source'] != 'event' or not row['subscribed'] or not row['all_day']:
            continue
        start = datetime.fromisoformat(row['start']).astimezone(ZONE).date()
        end = datetime.fromisoformat(row['end']).astimezone(ZONE).date()
        if start <= today < end:
            title = row['title']
            if any(word in title for word in ('补班', '调休上班', '工作日', '上班')):
                evidence.append((True, row['id']))
            elif any(word in title for word in ('放假', '休息', '假期', '调休')) or (
                    any(word in row['calendar'] for word in ('节假日', 'Holidays', 'holidays'))
                    and any(word in title for word in ('元旦', '春节', '清明', '劳动节', '端午', '中秋', '国庆'))):
                evidence.append((False, row['id']))
    decisions = {flag for flag, _ in evidence}
    return {'value': next(iter(decisions)) if len(decisions) == 1 else (None if decisions else now.weekday() < 5),
            'source': 'holiday_calendar' if evidence else 'weekday_fallback',
            'event_ids': [identifier for _, identifier in evidence]}


async def collect_async(home, kind, *, now=None, client=None):
    config = read_config(Path(home))
    if config is None:
        raise ValueError('invalid_collector_config')
    now = (now or datetime.now(ZONE)).astimezone(ZONE)
    day = now.date().isoformat()
    client = client or GatewayClient(config)
    ledger = await agenda_day(client, day)
    if kind == 'agenda' and ledger['status'] == 'generated':
        return {'wakeAgent': False, 'reason': 'agenda_already_generated', 'business_date': day,
                'report_id': ledger['report_id']}
    plan, location = await asyncio.gather(accepted_plan(client, day), latest_location(client, config, now))
    # A failed plan read is not "no plan": evening collection must not lose
    # out-of-list references. 404 alone is the contract's empty-plan response.
    value = helper_collection(config, kind, now, plan)
    reports, report_error = [], None
    try:
        reports, more = await relevant_reports(client, day, ledger)
        if more:
            report_error = 'recent_reports_limited_to_20'
    except BridgeError:
        report_error = 'reports_unavailable'
    anchors = []
    for report in reports:
        summary = report_summary(report)
        # Old reports may authorize only unchanged originals from this pass.
        originals = (report.get('gtd') or {}).get('originals') or {}
        summary['allowed_items'] = [row for row in summary['allowed_items']
                                    if row['id'] in value['originals'] and originals[row['id']] == value['originals'][row['id']]]
        anchors.append(summary)
    return {**value, 'business_date': day, 'weekday': '星期' + '一二三四五六日'[now.weekday()],
            'workday': workday(value['items'], now), 'plan': plan, 'location': location,
            'reports': anchors, 'reports_warning': report_error}


def collect(home, kind, *, now=None):
    return asyncio.run(collect_async(home, kind, now=now))


def main(kind, home):
    try:
        value = collect(home, kind)
        # Emit one complete document, even for zero items. Empty stdout would
        # silently suppress the scheduled report in the host's pre-run gate.
        encoded = (encode(value) + '\n').encode('utf-8')
        if len(encoded) > MAX_BYTES:
            raise ValueError('collection_output_too_large')
        sys.stdout.buffer.write(encoded)
        return 0
    except (OSError, ValueError, KeyError, TypeError, BridgeError, subprocess.SubprocessError) as exc:
        # Only our fixed reasons are public; never echo a path or Apple payload.
        reason = str(exc) if type(exc) is ValueError and str(exc) in {
            'collection_limit_exceeded', 'collection_output_too_large', 'helper_not_configured',
            'invalid_collection_originals', 'helper_failed', 'duplicate_apple_name',
        } else 'collection_unavailable'
        sys.stderr.write('Ollo collection failed: ' + reason + '\n')
        return 1
