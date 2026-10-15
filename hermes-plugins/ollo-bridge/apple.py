"""Only a reviewed executable with a fixed JSON protocol; no shell/Agent."""
import asyncio
from pathlib import Path

from .protocol import BridgeError, ITEMS_RESPONSE_BYTES, dumps, loads, require, validate
from .http_client import Budget
from .helper_process import HelperProcesses, NotStarted
from .apple_config import names
from .apple_diagnostic import diagnostic_fields


class NotExecuted(Exception):
    def __init__(self, message='', *, diagnostic=None):
        super().__init__(message)
        self.diagnostic = diagnostic_fields(diagnostic)


class UnknownWrite(Exception):
    def __init__(self, message='', *, diagnostic=None):
        super().__init__(message)
        self.diagnostic = diagnostic_fields(diagnostic)


class AppleBackend:
    def __init__(self, executable, apple=None):
        self.executable = executable
        self.apple = names(apple)
        self.processes = HelperProcesses()

    async def call(self, request, *, timeout, write=False, request_id=None):
        if not self.executable or not Path(self.executable).is_absolute():
            raise NotExecuted('helper_not_configured')
        budget = Budget(timeout)
        diagnostic = None
        try:
            returncode, stdout = await self.processes.run(
                self.executable, dumps({**request, 'apple': self.apple}).encode('utf-8'), budget, request_id)
            limit = ITEMS_RESPONSE_BYTES if request.get('method') == 'items' else 1048576
            require(len(stdout) <= limit, 'payload_too_large', 413)
            result = loads(stdout)
            if isinstance(result, dict) and (returncode or 'error' in result or result.get('outcome') == 'not_executed'):
                diagnostic = diagnostic_fields(result.get('diagnostic'))
            if isinstance(result, dict) and result.get('outcome') == 'not_executed':
                raise NotExecuted('helper_rejected_before_commit', diagnostic=diagnostic)
            require(not returncode and isinstance(result, dict) and 'error' not in result)
            return result
        except NotStarted as exc:
            raise NotExecuted('helper_not_started') from exc
        except NotExecuted:
            raise
        except (asyncio.CancelledError, asyncio.TimeoutError, OSError, BridgeError) as exc:
            if write:
                raise UnknownWrite('write_outcome_unknown', diagnostic=diagnostic) from exc
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, BridgeError) and exc.code == 'payload_too_large':
                raise  # Read-only caller can shrink its batch; never truncate it.
            raise BridgeError('read_timeout' if isinstance(exc, asyncio.TimeoutError) else 'dependency_unavailable',
                              504 if isinstance(exc, asyncio.TimeoutError) else 503, True) from exc

    async def items(self, ids, *, timeout=5):
        try:
            result = await self.call({'method': 'items', 'ids': ids}, timeout=timeout)
        except NotExecuted as exc:
            raise BridgeError() from exc
        rows = result.get('items')
        require(isinstance(rows, list) and len(rows) == len(ids), 'dependency_unavailable', 503)
        require({row.get('id') for row in rows} == set(ids), 'dependency_unavailable', 503)
        # The native snapshot contains originals and notes; public refresh omits them.
        public = [{key: value for key, value in row.items()
                   if key in {'id', 'exists', 'completed', 'title', 'list', 'due', 'modified_at'}} for row in rows]
        return validate('B_ScheduleItemsResponse', {'items': public})

    async def capture(self, request, *, timeout=6):
        result = await self.call({'method': 'capture', 'request': request}, timeout=timeout, write=True,
                                 request_id=request['request_id'])
        try:
            return validate('B_ScheduleCaptureResponse', {'item_id': result['id']})['item_id']
        except (BridgeError, KeyError) as exc:
            raise UnknownWrite('write_receipt_invalid') from exc

    async def capture_lookup(self, request_id, *, timeout=5):
        if self.processes.is_active(request_id):
            return {'status': 'unavailable', 'reason': 'in_progress'}
        try:
            result = await self.call({'method': 'capture_lookup', 'request_id': request_id}, timeout=timeout)
            return validate('B_BridgeCaptureLookupResponse', result)
        except (NotExecuted, BridgeError):
            return {'status': 'unavailable', 'reason': 'reminder_read_unavailable'}

    async def group(self, permit, *, timeout=10):
        group = permit['group']
        ids = {c[k] for c in group['changes'] for k in ('item', 'into') if k in c}
        originals = permit['originals']
        if not ids <= originals.keys():
            return [{'index': i, 'status': 'skipped', 'reason': '缺少生成时原值，未执行'} for i in group['indices']]
        # The native helper re-reads the entire dependency group before staging.
        # An empty originals map is valid only for create-only groups.
        result = await self.call({'method': 'apply', 'changes': group['changes'],
                                  'expected': {i: originals[i] for i in ids}}, timeout=timeout, write=True)
        if result.get('status') not in {'done', 'skipped'}:
            raise UnknownWrite('write_receipt_invalid')
        if result['status'] == 'skipped' and not isinstance(result.get('reason'), str):
            raise UnknownWrite('write_receipt_invalid')
        rows = [{'index': i, **result} for i in group['indices']]
        for row in rows:
            validate('B_GTDResultEntry', row)
        return rows
