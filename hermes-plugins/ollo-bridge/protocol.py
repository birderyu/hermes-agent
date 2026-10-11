"""Offline wire validation. Errors never echo user data or credentials."""
from datetime import date, datetime
import json
import math
from pathlib import Path
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker, validators, ValidationError

# Shared public/bridge contract table. None is the legacy "unknown" retry
# policy: omit the field, never manufacture permission to retry a write.
ERROR_POLICIES = {
    'invalid_request': (400, False), 'invalid_confirmation': (400, False),
    'invalid_cursor': (400, False), 'invalid_event_id': (400, False),
    'unauthorized': (401, False), 'forbidden': (403, False), 'runtime_mismatch': (403, False),
    'not_found': (404, False), 'expired': (410, False),
    'request_id_conflict': (409, False), 'operation_in_progress': (409, True),
    'write_outcome_unknown': (409, False), 'run_active': (409, True),
    'stale_execution': (409, False), 'idempotency_conflict': (409, False), 'run_finished': (409, False),
    'payload_too_large': (413, False), 'not_implemented': (501, False),
    'invalid_agent_response': (502, None), 'not_configured': (503, False),
    'dependency_unavailable': (503, True), 'not_executed': (503, True),
    'paused_job': (503, False), 'invalid_model_output': (503, True),
    'agent_unavailable': (503, None), 'history_scan_limit': (503, None), 'event_buffer_full': (503, None),
    'read_timeout': (504, True), 'interpret_timeout': (504, True),
}
ITEMS_RESPONSE_BYTES = 2097152


class BridgeError(Exception):
    def __init__(self, code='dependency_unavailable', status=503, retryable=True):
        if code not in ERROR_POLICIES:
            code = 'dependency_unavailable'
        previous_status = status
        status, retryable = ERROR_POLICIES[code]
        if code == 'expired' and previous_status == 404:
            status = 404  # Retained legacy task events, not location expiry.
        self.code, self.status, self.retryable = code, status, retryable
        super().__init__(code)

    def response(self):
        result = {'code': self.code, 'message': self.code}
        if self.retryable is not None:
            result['retryable'] = self.retryable
        return result


def dumps(value, *, sort_keys=False):
    """One wire encoding for HTTP, tools, helper input and byte accounting."""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=sort_keys, allow_nan=False)


def require(ok, code='invalid_request', status=400):
    if not ok:
        raise BridgeError(code, status, False)


def _bytes(validator, maximum, instance, schema):
    if isinstance(instance, str) and len(instance.encode('utf-8')) > maximum:
        yield ValidationError('UTF-8 budget exceeded')


_CHECKER = FormatChecker()


@_CHECKER.checks('date', raises=(ValueError, TypeError))
def _date(value):
    return isinstance(value, str) and len(value) == 10 and date.fromisoformat(value).isoformat() == value


@_CHECKER.checks('date-time', raises=(ValueError, TypeError))
def _datetime(value):
    return isinstance(value, str) and 'T' in value and datetime.fromisoformat(value.replace('Z', '+00:00')).utcoffset() is not None


@_CHECKER.checks('uuid', raises=(ValueError, TypeError, AttributeError))
def _uuid(value):
    return isinstance(value, str) and len(value) == 36 and str(UUID(value)) == value.lower()


_SCHEMA = json.loads((Path(__file__).parent / 'contracts/schema.json').read_text(encoding='utf-8'))
_VALIDATOR = validators.extend(Draft202012Validator, {'x-max-utf8-bytes': _bytes})


def _finite(value):
    if isinstance(value, float):
        require(math.isfinite(value))
    elif isinstance(value, str):
        require('\x00' not in value)
    elif isinstance(value, list):
        for child in value:
            _finite(child)
    elif isinstance(value, dict):
        for key, child in value.items():
            _finite(key)
            _finite(child)


def _byte_limit_error(error):
    # Combinators wrap field errors in context; inspect the actual failed
    # branches, not the schema (a different anyOf branch may accept the value).
    return error.validator == 'x-max-utf8-bytes' or any(_byte_limit_error(child) for child in error.context)


def validate(name, value):
    _finite(value)
    schema = {**_SCHEMA, '$ref': '#/$defs/' + name}
    try:
        invalid = False
        # A preceding maxLength/type error must not hide a later byte limit.
        for error in _VALIDATOR(schema, format_checker=_CHECKER).iter_errors(value):
            invalid = True
            if _byte_limit_error(error):
                raise BridgeError('payload_too_large', 413, False) from error
        if invalid:
            raise BridgeError('invalid_request', 400, False)
    except (ValidationError, RecursionError) as exc:
        raise BridgeError('invalid_request', 400, False) from exc
    return value


def schema(name):
    # Only the requested model's transitive definitions belong in a model call.
    selected = {}
    def visit(value):
        if isinstance(value, dict):
            if '$ref' in value:
                ref = value['$ref'].split('/')[-1]
                if ref not in selected:
                    selected[ref] = _SCHEMA['$defs'][ref]
                    visit(selected[ref])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit({'$ref': '#/$defs/' + name})
    return {'$schema': _SCHEMA['$schema'], '$defs': selected, '$ref': '#/$defs/' + name}



def loads(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs)
        _finite(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise BridgeError('invalid_request', 400, False) from exc


def references(value):
    """Only GTD reference fields; arbitrary titles and notes are never IDs."""
    result = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == 'originals':
                continue
            if key in {'id', 'item', 'into', 'with', 'merge_into'} and isinstance(child, str):
                result.add(child)
            elif isinstance(child, (dict, list)):
                result.update(references(child))
    elif isinstance(value, list):
        for child in value:
            result.update(references(child))
    return result
