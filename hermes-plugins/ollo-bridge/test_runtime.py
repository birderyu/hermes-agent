"""Real host hooks, middleware and /v1/runs path against HTTP/model substitutes."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.register_assert_rewrite('ollo_bridge_test_support')
from ollo_bridge_test_support import example, Operations

pytest_plugins = ['ollo_bridge_test_support']


@pytest.mark.asyncio
async def test_execute_run_exports_unique_host_identity_and_executes_only_receipted_groups(bridge, gateway, monkeypatch):
    from hermes_cli import plugins
    # run_agent imports model_tools, whose discovery must use the already wired
    # fixture manager; a second manager would replace its registered handlers.
    monkeypatch.setattr(plugins, 'get_plugin_manager', lambda: bridge.manager)
    from tools.registry import registry
    entry = registry.get_entry('execute_gtd_confirmation')
    from tools.approval_context import _approval_session_key
    from run_agent import AIAgent

    assert registry.get_entry('execute_gtd_confirmation') is entry
    document = example('confirmation')
    cid = document['confirmation_id']
    checked_runs = []
    async def details(request, body):
        # Mirror C's authorize_runtime: call the real host GET with the Hermes
        # key, require a live run and compare session + exported per-run task ID.
        run_id = request.headers['X-Ollo-Task-ID']
        status, host = await bridge.client.request('GET', '/v1/runs/' + run_id)
        assert status == 200 and host['status'] == 'running'
        assert host['task_id'] == host['run_id'] == run_id
        assert host['session_id'] == request.headers['X-Ollo-Session-ID'] == 'tip'
        assert host['task_id'] != host['session_id']
        document['run_id'] = run_id
        checked_runs.append(host)
        return gateway.web.json_response(document)
    gateway.routes[('GET', '/internal/v1/schedule/confirmations/' + cid)] = details
    operations = Operations(gateway)
    for group in document['groups']:
        operations.add(group['operation_id'], {'kind': 'gtd_group', 'confirmation_id': cid,
            'report_id': document['confirmation']['report_id'], 'group': group, 'originals': document['originals']})
    async def apply(self, permit, **kwargs):
        assert _approval_session_key.get() == document['run_id']
        return [{'index': i, 'status': 'done'} for i in permit['group']['indices']]
    monkeypatch.setattr(bridge.plugin.AppleBackend, 'group', apply)
    metadata = lambda *a, **k: {'fake/model': {'context_length': 128000}}
    monkeypatch.setattr('agent.model_metadata.fetch_model_metadata', metadata)
    monkeypatch.setattr('agent.agent_init.fetch_model_metadata', metadata)
    seen, prompts = [], []
    def primary(**kwargs):
        seen.append(kwargs)
        prompts.append(json.dumps(kwargs['messages'], ensure_ascii=False))
        if len(seen) == 1:
            calls = [SimpleNamespace(id='call-confirm', type='function', function=SimpleNamespace(
                     name='execute_gtd_confirmation', arguments=json.dumps({'confirmation_id': cid})))]
            message = SimpleNamespace(content=None, tool_calls=calls, reasoning=None)
            reason = 'tool_calls'
        else:
            message = SimpleNamespace(content='已按执行回执处理。', tool_calls=None, reasoning=None)
            reason = 'stop'
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=reason)],
                               usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15), model='fake/model')
    with patch('model_tools.get_tool_definitions', return_value=[{'type': 'function', 'function': entry.schema}]), \
         patch('model_tools.check_toolset_requirements', return_value={}), patch('agent.process_bootstrap.OpenAI'):
        agent = AIAgent(api_key='synthetic-key-123456789', base_url='https://openrouter.ai/api/v1', model='fake/model',
                        quiet_mode=True, skip_context_files=True, skip_memory=True, platform='api_server',
                        session_id='tip', session_db=bridge.db, enabled_toolsets=['ollo_bridge'])
    agent.client = MagicMock()
    agent.client.chat.completions.create = primary
    monkeypatch.setattr(bridge.api, '_create_agent', lambda **kwargs: agent)
    # C forwards the original text unchanged; UUID exists only in this header.
    user = '确认\n```hermes-plus-gtd-confirm\n' + json.dumps(document['confirmation']) + '\n```'
    assert cid not in user
    status, admitted = await bridge.client.request('POST', '/v1/runs', body={'input': user, 'session_id': 'root'},
                                                   headers={'Idempotency-Key': cid})
    assert status == 202
    run_id = admitted['run_id']
    task = bridge.api._active_run_tasks.get(run_id)
    if task is not None:
        await asyncio.wait_for(task, 20)
    status, response = await bridge.client.request('GET', '/v1/runs/' + run_id)
    assert status == 200 and response['status'] == 'completed'
    assert response['task_id'] == run_id and response['session_id'] == 'tip'
    # Assert outside the model double: the host retries AssertionError as a
    # provider failure, which otherwise hides the original missing-context fault.
    assert f'confirmation_id={cid}' in prompts[0]
    assert len(checked_runs) == 1
    tool_results = [json.loads(message['content']) for message in seen[-1]['messages'] if message.get('role') == 'tool']
    assert len(tool_results) == 1 and 'error' not in tool_results[0], tool_results
    assert tool_results[0]['results'] == [{'index': i, 'status': 'done'} for i in range(3)]
    assert len(operations.outcomes) == 2
    confirmation_call = next(c for c in gateway.calls if '/confirmations/' in c[1])
    assert confirmation_call[3]['X-Ollo-Task-ID'] == run_id
    assert confirmation_call[3]['X-Ollo-Tool-Call-ID'] == 'call-confirm'
    assert _approval_session_key.get() == ''
    # Same UUID replays the host run without another tool/Apple execution.
    status, replay = await bridge.client.request('POST', '/v1/runs', body={'input': user, 'session_id': 'root'},
                                                headers={'Idempotency-Key': cid})
    assert status == 202 and replay['run_id'] == run_id and replay['replayed']
    assert len(operations.outcomes) == 2 and len(checked_runs) == 1


def test_guard_survives_missing_context_hook_and_model_id_spoof(bridge):
    from tools.approval_context import set_current_session_key, reset_current_session_key
    from importlib import import_module
    identity = import_module(bridge.plugin.__name__ + '.identity')
    binding = identity.Binding(bridge.home, bridge.plugin.read_config(bridge.home))
    binding.adapter = bridge.api
    bridge.api._active_run_agents['real-run'] = SimpleNamespace(
        session_id='tip', _current_task_id='task', _current_turn_id='t')
    token = set_current_session_key('real-run')
    try:
        assert binding.guard(tool_name='terminal', session_id='tip', task_id='task', turn_id='t')['action'] == 'block'
        with pytest.raises(bridge.plugin.BridgeError, match='forbidden'):
            binding.runtime(confirmation=True)
    finally:
        reset_current_session_key(token)


@pytest.mark.asyncio
async def test_profile_switch_does_not_reuse_first_profile_capability(bridge, gateway, tmp_path):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    await bridge.manager.ainvoke_hook('pre_llm_call', session_id='tip', task_id='t', turn_id='x', platform='api_server')
    other = tmp_path / 'other-profile'
    other.mkdir()
    token = set_hermes_home_override(other)
    try:
        assert await bridge.manager.ainvoke_hook('pre_llm_call', session_id='tip', task_id='t', turn_id='x', platform='api_server') == []
        assert bridge.manager.invoke_hook('pre_tool_call', tool_name='get_user_location', session_id='tip', task_id='t', turn_id='x')[0]['action'] == 'block'
    finally:
        reset_hermes_home_override(token)
    # Returning to A requires a fresh host hook, not the old capability.
    assert bridge.manager.invoke_hook('pre_tool_call', tool_name='get_user_location', session_id='tip', task_id='t', turn_id='x')[0]['action'] == 'block'


def test_confirmation_guard_fails_closed_when_session_store_is_unavailable(bridge, monkeypatch):
    import sqlite3
    from importlib import import_module
    from tools.approval_context import set_current_session_key, reset_current_session_key
    identity = import_module(bridge.plugin.__name__ + '.identity')
    binding = identity.Binding(bridge.home, bridge.plugin.read_config(bridge.home))
    binding.adapter = bridge.api
    def unavailable(*args):
        raise sqlite3.OperationalError('synthetic store unavailable')
    monkeypatch.setattr(bridge.db, 'get_session', unavailable)
    bridge.api._active_run_agents['real-run'] = SimpleNamespace(
        session_id='tip', _current_task_id='task', _current_turn_id='t')
    token = set_current_session_key('real-run')
    try:
        assert binding.guard(tool_name='terminal', session_id='tip', task_id='task', turn_id='t')['action'] == 'block'
        assert binding.guard(tool_name='execute_gtd_confirmation', session_id='tip', task_id='task', turn_id='t')['action'] == 'block'
    finally:
        reset_current_session_key(token)


def test_run_identity_requires_context_and_exact_live_turn(bridge, monkeypatch):
    from importlib import import_module
    from tools.approval_context import set_current_session_key, reset_current_session_key
    identity = import_module(bridge.plugin.__name__ + '.identity')
    binding = identity.Binding(bridge.home, bridge.plugin.read_config(bridge.home))
    binding.adapter = bridge.api
    bridge.api._active_run_agents['first'] = SimpleNamespace(
        session_id='tip', _current_task_id='shared-task', _current_turn_id='one')
    bridge.api._active_run_agents['second'] = SimpleNamespace(
        session_id='tip', _current_task_id='shared-task', _current_turn_id='two')
    # Public approval getters have a legacy environment fallback; it is not authority here.
    monkeypatch.setenv('HERMES_SESSION_KEY', 'first')
    token = set_current_session_key('')
    try:
        assert binding.current_run_id('tip', 'shared-task', 'one') is None
    finally:
        reset_current_session_key(token)

    token = set_current_session_key('first')
    try:
        assert binding.current_run_id('tip', 'shared-task', 'one') == 'first'
        assert binding.current_run_id('tip', 'shared-task', 'two') is None
        assert binding.current_run_id('other', 'shared-task', 'one') is None
        assert binding.current_run_id('tip', 'model-supplied', 'one') is None
        bridge.api._active_run_agents['first'].session_id = 'other'
        assert binding.guard(tool_name='terminal', session_id='other', task_id='shared-task', turn_id='one') is None
        bridge.api._active_run_agents['first'].session_id = 'tip'
        binding.start('tip', 'shared-task', 'one', 'api_server', '```hermes-plus-gtd-confirm\n{}\n```')
        def execute():
            return binding.runtime(confirmation=True)
        assert binding.middleware(execute, 'execute_gtd_confirmation', 'tip', 'shared-task', 'one', 'call')['task_id'] == 'first'
        del bridge.api._active_run_agents['first']
        with pytest.raises(bridge.plugin.BridgeError, match='forbidden'):
            binding.middleware(execute, 'execute_gtd_confirmation', 'tip', 'shared-task', 'one', 'call')
    finally:
        reset_current_session_key(token)

@pytest.mark.asyncio
async def test_confirmation_uuid_requires_authenticated_request_and_cannot_be_swapped(bridge):
    from aiohttp import web
    from importlib import import_module
    from tools.approval_context import set_current_session_key, reset_current_session_key
    identity = import_module(bridge.plugin.__name__ + '.identity')
    integration = import_module(bridge.plugin.__name__ + '.integration')
    binding = identity.Binding(bridge.home, bridge.plugin.read_config(bridge.home))
    binding.adapter = bridge.api
    run_id, cid = 'real-run', example('confirmation')['confirmation_id']
    bridge.api._active_run_agents[run_id] = SimpleNamespace(session_id='tip', _current_task_id='task', _current_turn_id='turn')
    bridge.api._run_owners[run_id] = 'scope'
    task = asyncio.get_running_loop().create_future()
    bridge.api._active_run_tasks[run_id] = task
    schedule = SimpleNamespace(confirm=AsyncMock(return_value={'ok': True}))
    service = integration.Integration(bridge.home, binding.config, None, binding, schedule)
    run_token = set_current_session_key(run_id)
    call_token = identity.CALL.set({'session_id': 'tip', 'task_id': 'task', 'turn_id': 'turn', 'tool_call_id': 'call'})
    try:
        accepted = web.json_response({'run_id': run_id, 'replayed': False}, status=202)
        for owner, key, response in (
                ('other-owner', cid, accepted), ('scope', None, accepted),
                ('scope', cid, web.json_response({'run_id': run_id}, status=401)),
                ('scope', cid, web.json_response({'run_id': run_id, 'replayed': True}, status=202))):
            binding.requests.remember(bridge.api, owner, key, response)
            binding.start('tip', 'task', 'turn', 'api_server', '```hermes-plus-gtd-confirm\n{}\n```')
            with pytest.raises(bridge.plugin.BridgeError, match='forbidden'):
                await service.confirmation({'confirmation_id': cid})
        schedule.confirm.assert_not_awaited()
        binding.requests.remember(bridge.api, 'scope', cid, accepted)
        assert binding.requests.get(object(), run_id) is None
        bridge.api._run_owners[run_id] = 'changed-owner'
        assert binding.request_id('tip', 'task', 'turn') is None
        bridge.api._run_owners[run_id] = 'scope'
        binding.start('tip', 'task', 'turn', 'api_server', '```hermes-plus-gtd-confirm\n{}\n```')
        with pytest.raises(bridge.plugin.BridgeError, match='forbidden'):
            await service.confirmation({'confirmation_id': '40000000-0000-4000-8000-000000000002'})
        assert await service.confirmation({'confirmation_id': cid}) == {'ok': True}
        schedule.confirm.assert_awaited_once_with(cid, {'source': 'tool', 'session_id': 'tip',
            'task_id': run_id, 'tool_call_id': 'call'})
        task.set_result(None)
        await asyncio.sleep(0)  # Let the admitted task's done callback revoke the UUID.
        with pytest.raises(bridge.plugin.BridgeError, match='forbidden'):
            await service.confirmation({'confirmation_id': cid})
        # Replaying an old acceptance cannot restore execution authority.
        binding.requests.remember(bridge.api, 'scope', cid, accepted)
        assert binding.request_id('tip', 'task', 'turn') is None
    finally:
        if not task.done():
            task.cancel()
        bridge.api._active_run_tasks.pop(run_id, None)
        identity.CALL.reset(call_token)
        reset_current_session_key(run_token)


@pytest.mark.asyncio
async def test_confirmation_uuids_cross_executor_without_crossing_runs(bridge):
    from aiohttp import web
    from importlib import import_module
    from tools.approval_context import set_current_session_key, reset_current_session_key
    identity = import_module(bridge.plugin.__name__ + '.identity')
    binding = identity.Binding(bridge.home, bridge.plugin.read_config(bridge.home))
    binding.adapter = bridge.api
    loop = asyncio.get_running_loop()
    calls = [('first', '40000000-0000-4000-8000-000000000001'),
             ('second', '40000000-0000-4000-8000-000000000002')]
    tasks = []
    for run_id, cid in calls:
        task = loop.create_future()
        tasks.append(task)
        bridge.api._active_run_tasks[run_id] = task
        bridge.api._run_owners[run_id] = 'scope'
        bridge.api._active_run_agents[run_id] = SimpleNamespace(
            session_id='tip', _current_task_id='tip', _current_turn_id=run_id)
        binding.requests.remember(bridge.api, 'scope', cid,
            web.json_response({'run_id': run_id, 'replayed': False}, status=202))

    def in_worker(run_id):
        # Like _run_agent_sync, bind only host identity in the executor; no
        # copy_context and no plugin request ContextVar enter this thread.
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        profile = set_hermes_home_override(bridge.home)
        token = set_current_session_key(run_id)
        try:
            assert binding.request_id('tip', 'tip', 'other-turn') is None
            return binding.request_id('tip', 'tip', run_id)
        finally:
            reset_current_session_key(token)
            reset_hermes_home_override(profile)

    try:
        assert await asyncio.gather(*(loop.run_in_executor(None, in_worker, run_id) for run_id, _ in calls)) == [
            cid for _, cid in calls]
        tasks[0].set_result(None)
        await asyncio.sleep(0)
        assert await loop.run_in_executor(None, in_worker, 'first') is None
        assert await loop.run_in_executor(None, in_worker, 'second') == calls[1][1]
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        for run_id, _ in calls:
            bridge.api._active_run_tasks.pop(run_id, None)


@pytest.mark.asyncio
async def test_run_export_preserves_auth_owner_and_non_ollo_boundaries(bridge):
    from aiohttp.test_utils import make_mocked_request
    from ollo_bridge_test_support import KEY
    owner_request = make_mocked_request('GET', '/', headers={'Authorization': 'Bearer isolated-owner-key'})
    owner = bridge.api._run_idempotency_scope(owner_request)
    for run_id, session in [('one', 'tip'), ('two', 'tip'), ('other', 'other')]:
        bridge.api._run_owners[run_id] = owner
        bridge.api._set_run_status(run_id, 'running', session_id=session)
    for run_id in ('one', 'two'):
        status, result = await bridge.client.request('GET', '/v1/runs/' + run_id)
        assert status == 200 and result['task_id'] == run_id and result['session_id'] == 'tip'
    for token in (None, 'wrong', KEY):
        assert (await bridge.client.request('GET', '/v1/runs/one', token=token))[0] == 401
    bridge.api._run_owners['one'] = 'different-owner'
    assert (await bridge.client.request('GET', '/v1/runs/one'))[0] == 404
    status, result = await bridge.client.request('GET', '/v1/runs/other')
    assert status == 200 and 'task_id' not in result
