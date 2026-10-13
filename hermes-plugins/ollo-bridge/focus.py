"""Render gateway-owned focus as conversation data, never as Apple permission."""
from datetime import datetime
from zoneinfo import ZoneInfo

from .protocol import dumps


def focus_summary(reports):
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    lines = []
    for report in reports:
        confirmed = report.get('focus_confirmed')
        if not confirmed:
            continue
        agenda = (report.get('gtd') or {}).get('agenda') or {}
        day = agenda.get('date')
        label = '今天' if day == today else (day or '该报告')
        titles = {item['id']: item['title'] for item in agenda.get('focus', [])}
        # Only the gateway selection counts. preselected/raw_body are immutable
        # suggestions, and confirmed_at is not the report's business date.
        items = []
        for identifier in confirmed['item_ids']:
            title = titles.get(identifier)
            items.append(title[:80] + ('…' if len(title) > 80 else '') if title else identifier)
        note = '；日期请读完整报告核对' if day is None else ''
        lines.append(f'{label}已确认的重点：{dumps(items)}（报告 {report["id"]}；'
                     f'仅网关记忆，不改提醒事项{note}）。')
    return '\n'.join(lines)


def with_focus_summary(result):
    rows = [result] if 'report' in result else result['reports']
    summary = focus_summary([row['report'] for row in rows])
    # This is a tool-only annotation, not a field written back to the gateway.
    # Keep raw_body, originals and the full state unchanged, including long titles.
    return {**result, 'focus_summary': summary} if summary else result
