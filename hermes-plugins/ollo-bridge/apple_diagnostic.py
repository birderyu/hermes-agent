"""Allowlisted EventKit failure metadata; never serialize an exception or payload."""
import json
import logging


logger = logging.getLogger(__name__)
OPERATIONS = {'update', 'delete', 'merge', 'to_event', 'update_event', 'create_reminder', 'complete', 'capture', 'batch', 'unknown'}
FIELDS = {'title', 'list', 'due', 'waiting_for', 'follow_up', 'calendar', 'start', 'minutes', 'alarm', 'notes', 'url'}


def _error_identity(value):
    if not isinstance(value, dict):
        return None
    domain, code = value.get('domain'), value.get('code')
    if not isinstance(domain, str) or not domain or type(code) is not int:
        return None
    return {'domain': domain, 'code': code}


def diagnostic_fields(value):
    """Ignore unknown keys at both levels, including userInfo and descriptions."""
    identity = _error_identity(value)
    if identity is None:
        return None
    phase, op, fields = value.get('phase'), value.get('op'), value.get('fields')
    if phase not in ('prepare', 'move', 'reconcile', 'commit') or not isinstance(op, str) or op not in OPERATIONS:
        return None
    if not isinstance(fields, list):
        return None
    result = {**identity, 'phase': phase, 'op': op,
              'fields': sorted({field for field in fields if isinstance(field, str) and field in FIELDS})}
    underlying = _error_identity(value.get('underlyingError'))
    if underlying is not None:
        result['underlyingError'] = underlying
    return result


def log_failure(operation_id, failure):
    diagnostic = diagnostic_fields(getattr(failure, 'diagnostic', None))
    if diagnostic is not None:
        # JSON escaping keeps each diagnostic on one line, even for unusual domains.
        logger.warning('EventKit failure %s', json.dumps(
            {'operation_id': operation_id, 'diagnostic': diagnostic},
            ensure_ascii=True, sort_keys=True, separators=(',', ':')))
