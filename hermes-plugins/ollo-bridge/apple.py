"""Only a reviewed executable with a fixed JSON protocol; no shell/Agent."""
import asyncio
from pathlib import Path

from .protocol import BridgeError, ITEMS_RESPONSE_BYTES, dumps, loads, require, validate
from .http_client import Budget
from .helper_process import HelperProcesses, NotStarted
from .apple_config import names
from .apple_diagnostic import diagnostic_fields


MOVE_HELPER_LIMIT = 58
MOVE_OPERATION_BUDGET = 62
MOVE_REASONS = {
    'move_permission': '没有控制「提醒事项」的权限，未执行',
    'move_timeout': '「提醒事项」响应超时，结果未知，需核对；不可重试写入',
    'move_app_unavailable': '「提醒事项」无法打开，未执行',
    'move_connection_lost': '与「提醒事项」的连接中断，结果未知，需核对；不可重试写入',
    'move_failed': '移动失败，未执行',
    'move_precondition': '确认前事项或目标清单已变化，未执行',
    'move_budget': '处理超时，未执行',
    'move_unresolved': '已移动，但无法确认移动后的事项，需核对；不可重试写入',
    'move_partial': '部分已移动，其余修改结果未知，需核对；不可重试写入',
}


def group_limits(changes):
    if any(c.get('op') == 'update' and c.get('list') is not None for c in changes):
        return MOVE_OPERATION_BUDGET, MOVE_HELPER_LIMIT
    return 14, 10


def failure_reason(failure, fallback):
    # Never turn arbitrary helper/OS error text into a user-visible receipt.
    return MOVE_REASONS.get(str(failure), fallback)


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
            if isinstance(result, dict) and (returncode or 'error' in result or result.get('outcome') in ('not_executed', 'unknown')):
                diagnostic = diagnostic_fields(result.get('diagnostic'))
            if isinstance(result, dict) and result.get('outcome') == 'not_executed':
                reason = result.get('reason_code')
                raise NotExecuted(reason if isinstance(reason, str) and reason in MOVE_REASONS
                                  else 'helper_rejected_before_commit', diagnostic=diagnostic)
            if write and isinstance(result, dict) and result.get('outcome') == 'unknown':
                reason = result.get('reason_code')
                raise UnknownWrite(reason if reason in ('move_unresolved', 'move_partial', 'move_timeout', 'move_connection_lost')
                                   else 'write_outcome_unknown', diagnostic=diagnostic)
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

    async def group(self, permit, *, timeout=None):
        group = permit['group']
        if timeout is None:
            timeout = group_limits(group['changes'])[1]
        ids = {c[k] for c in group['changes'] for k in ('item', 'into') if k in c}
        originals = permit['originals']
        if not ids <= originals.keys():
            return [{'index': i, 'status': 'skipped', 'reason': '缺少生成时原值，未执行'} for i in group['indices']]
        if any(c['op'] == 'complete' and (originals[c['item']].get('source') != 'reminder'
               or originals[c['item']].get('completed') is not False) for c in group['changes']):
            return [{'index': i, 'status': 'skipped', 'reason': '完成操作仅支持原值未完成的提醒事项'} for i in group['indices']]
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
