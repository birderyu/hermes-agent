"""Read-only cron collection. Stdlib only; never reads bridge.key or starts plugins."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

from .apple_config import names, scopes

ZONE = ZoneInfo('Asia/Shanghai')
MAX_ITEMS = 500
MAX_BYTES = 16 * 1024 * 1024


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def collect_request(config, kind, now):
    scope = scopes(config.get('collection'))[kind]
    apple = names(config.get('apple'))
    now = now.astimezone(ZONE)
    start = now if kind == 'review' else now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7 if kind == 'review' else 1)
    return {'method': 'collect', **scope, 'apple': apple, 'start': start.isoformat(), 'end': end.isoformat()}


def validate_collection(value):
    if not isinstance(value, dict) or set(value) != {'items', 'originals', 'collected_at'}:
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
                or row['id'] in ids or row.get('exists') is not True or row.get('completed') is not False
                or row.get('source') not in {'reminder', 'event'} or not isinstance(row.get('title'), str)
                or not isinstance(row.get('list'), str)):
            raise ValueError('invalid_collection_item')
        ids.add(row['id'])
        if row.get('modified_at') is not None:
            expected[row['id']] = {k: v for k, v in row.items()
                                   if k in {'source', 'title', 'list', 'due', 'completed', 'modified_at', 'start', 'end'}}
    if originals != expected:
        raise ValueError('invalid_collection_originals')
    return value


def collect(home, kind, *, now=None):
    config = json.loads((Path(home) / 'ollo/bridge.json').read_text(encoding='utf-8'))
    if not isinstance(config, dict):
        raise ValueError('invalid_collector_config')
    helper = config.get('helper_path')
    if not isinstance(helper, str) or not Path(helper).is_absolute():
        raise ValueError('helper_not_configured')
    request = collect_request(config, kind, now or datetime.now(ZONE))
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


def main(kind, home):
    try:
        value = collect(home, kind)
        # Emit one complete document, even for zero items. Empty stdout would
        # silently suppress the scheduled report in the host's pre-run gate.
        sys.stdout.buffer.write((encode(value) + '\n').encode('utf-8'))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        # Only our fixed reasons are public; never echo a path or Apple payload.
        reason = str(exc) if type(exc) is ValueError and str(exc) in {
            'collection_limit_exceeded', 'collection_output_too_large', 'helper_not_configured',
            'invalid_collection_originals', 'helper_failed', 'duplicate_apple_name',
        } else 'collection_unavailable'
        sys.stderr.write('Ollo collection failed: ' + reason + '\n')
        return 1
