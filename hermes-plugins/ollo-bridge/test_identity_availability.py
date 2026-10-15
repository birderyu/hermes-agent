"""Store failures restrict this adapter's API execution, not unrelated tools."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

pytest_plugins = ['ollo_bridge_test_support']


@pytest.mark.parametrize('failure', ['missing', 'sqlite'])
@pytest.mark.parametrize('execution', ['cron', 'wechat', 'other_adapter', 'canonical', 'legacy'])
def test_unknown_lineage_only_blocks_own_api_execution(bridge, monkeypatch, failure, execution):
    from gateway.session_api_turn import api_execution
    from tools.approval_context import _approval_session_key

    def unavailable():
        if failure == 'sqlite':
            raise sqlite3.OperationalError('synthetic database is locked')

    monkeypatch.setattr(bridge.api, '_ensure_session_db', unavailable)
    api = {'adapter': bridge.api} if execution == 'canonical' else None
    if execution == 'other_adapter':
        api = {'adapter': object()}
    if execution == 'legacy':
        bridge.api._active_run_agents['live-run'] = SimpleNamespace(
            session_id='tip', _current_task_id='task', _current_turn_id='turn')
    api_token = api_execution.set(api)
    approval_token = _approval_session_key.set('live-run')
    try:
        results = bridge.manager.invoke_hook('pre_tool_call', tool_name='terminal',
            session_id='tip', task_id='task', turn_id='turn', platform=execution)
        if execution in {'canonical', 'legacy'}:
            assert results[0]['action'] == 'block'
        else:
            assert results == []
        # Absence of a context never authorizes Ollo-specific tools.
        assert bridge.manager.invoke_hook('pre_tool_call', tool_name='execute_gtd_confirmation',
            session_id='tip', task_id='task', turn_id='turn')[0]['action'] == 'block'
        if execution == 'legacy':
            bridge.api._active_run_agents.clear()
            assert bridge.manager.invoke_hook('pre_tool_call', tool_name='terminal',
                session_id='tip', task_id='task', turn_id='turn') == []
    finally:
        _approval_session_key.reset(approval_token)
        api_execution.reset(api_token)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing', 'sqlite_open', 'sqlite_resolve'])
async def test_run_projection_keeps_host_response_when_store_fails(bridge, monkeypatch, failure):
    from aiohttp.test_utils import make_mocked_request

    request = make_mocked_request('GET', '/v1/runs/live-run',
        headers={'Authorization': 'Bearer isolated-owner-key'}, match_info={'run_id': 'live-run'})
    bridge.api._run_owners['live-run'] = bridge.api._run_idempotency_scope(request)
    bridge.api._set_run_status('live-run', 'running', session_id='root')
    original = await bridge.api._handle_get_run(request)
    assert original.status == 200
    reads = []

    def unavailable(*args):
        raise sqlite3.OperationalError('synthetic database is locked')

    def store():
        reads.append(True)
        if len(reads) == 1:
            return bridge.db  # Lineage check succeeds before projection loses its store.
        if failure == 'sqlite_open':
            return unavailable()
        if failure == 'sqlite_resolve':
            return SimpleNamespace(resolve_resume_session_id=unavailable)
        return None

    monkeypatch.setattr(bridge.api, '_ensure_session_db', store)
    status, body = await bridge.client.request('GET', '/v1/runs/live-run')
    assert len(reads) == 2
    assert status == original.status and body == json.loads(original.body)
