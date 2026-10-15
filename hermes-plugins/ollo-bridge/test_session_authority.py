"""Real API admission, canonical TurnRunner and AIAgent; only inference/Apple/HTTP are synthetic."""
import asyncio
import copy
from contextvars import copy_context
import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio

pytest.register_assert_rewrite('ollo_bridge_test_support')
from ollo_bridge_test_support import Operations, example

pytest_plugins = ['ollo_bridge_test_support']


class Model:
    def __init__(self):
        self.calls, self.action, self.inspect = [], None, lambda: None
        self.first = True

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        self.inspect()
        calls = None
        if self.first and self.action:
            name, args = self.action
            calls = [SimpleNamespace(id='call-confirm', type='function', function=SimpleNamespace(
                name=name, arguments=json.dumps(args)))]
        self.first = False
        message = SimpleNamespace(content=None if calls else '已按回执处理。', tool_calls=calls, reasoning=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason='tool_calls' if calls else 'stop')],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15), model='fake/model')


@pytest_asyncio.fixture(params=['router', 'http'])
async def canonical(bridge, gateway, monkeypatch, request):
    from hermes_cli import plugins
    monkeypatch.setattr(plugins, 'get_plugin_manager', lambda: bridge.manager)
    from gateway.config import GatewayConfig, Platform
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from run_agent import AIAgent
    from tools.registry import registry

    model = Model()
    metadata = lambda *a, **kw: {'fake/model': {'context_length': 128000}}
    monkeypatch.setattr('agent.model_metadata.fetch_model_metadata', metadata)
    monkeypatch.setattr('agent.agent_init.fetch_model_metadata', metadata)
    monkeypatch.setattr('agent.process_bootstrap.OpenAI', MagicMock())
    monkeypatch.setattr('agent.title_generator.generate_title', lambda *a, **kw: None)
    entries = [registry.get_entry(n) for n in ('execute_gtd_confirmation', 'get_gtd_reports', 'terminal')]
    monkeypatch.setattr('model_tools.get_tool_definitions', lambda **kw: [
        {'type': 'function', 'function': e.schema} for e in entries])
    monkeypatch.setattr('model_tools.check_toolset_requirements', lambda *a, **kw: {})
    original_init = AIAgent.__init__

    def init(agent, *args, **kwargs):
        kwargs.update(skip_context_files=True, skip_memory=True)
        original_init(agent, *args, **kwargs)
        agent.client = MagicMock()
        agent.client.chat.completions.create = model.create
    monkeypatch.setattr(AIAgent, '__init__', init)
    runner = GatewayRunner(config=GatewayConfig())
    monkeypatch.setattr(runner, '_resolve_session_agent_runtime', lambda **kw: (
        'fake/model', {'api_key': 'synthetic-model-key', 'base_url': 'https://openrouter.ai/api/v1'}))
    authority = await initialize_session_authority(runner, profile_id=str(bridge.home),
        instance_id='ollo-test-owner', db=bridge.db)
    bridge.api.gateway_runner = runner
    bridge.api.set_message_handler(runner._handle_message)
    runner.adapters[Platform.API_SERVER] = bridge.api
    # A fallback into the old API executor must fail this integration test.
    monkeypatch.setattr(bridge.api, '_create_agent', MagicMock(side_effect=AssertionError('legacy path')))
    service = bridge.manager._hooks['pre_llm_call'][0].__self__
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    app = web.Application()
    app.router.add_post('/v1/runs', bridge.api._handle_runs)
    app.router.add_get('/v1/runs/{run_id}', bridge.api._handle_get_run)
    app.router.add_post('/v1/runs/{run_id}/stop', bridge.api._handle_stop_run)
    service.wire(app, bridge.api)
    http = None
    if request.param == 'http':
        from aiohttp.client import ClientSession
        monkeypatch.setattr('aiohttp.test_utils.ClientSession', ClientSession)
        http = TestClient(TestServer(app))
        try:
            await http.start_server()
        except PermissionError:
            await http.close()
            runner._session_db._db.close()
            pytest.skip('sandbox denies aiohttp TestServer listening socket; router variant still runs')

        loop = asyncio.get_running_loop()

        async def request_on_loop(method, path, token, body, headers):
            headers = {**({'Authorization': 'Bearer ' + token} if token else {}), **(headers or {})}
            async with http.request(method, path, json=body, headers=headers) as response:
                return response.status, await response.json()

        async def http_request(method, path, *, token='isolated-owner-key', body=None, headers=None):
            call = request_on_loop(method, path, token, body, headers)
            if asyncio.get_running_loop() is loop:
                return await call
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(call, loop))
        bridge.client = SimpleNamespace(request=http_request)
    else:
        bridge.client = type(bridge.client)(app)
    state = SimpleNamespace(bridge=bridge, gateway=gateway, runner=runner, authority=authority,
        model=model, binding=service.binding, service=service)
    try:
        yield state
    finally:
        tasks = [live.task for live in authority.sessions.values() if live.task and not live.task.done()]
        tasks += [t for t in bridge.api._active_run_tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if http is not None:
            await http.close()
        runner._shutdown_executor(drain_timeout=5)
        runner._session_db._db.close()


async def submit(c, text, *, key=None, session='root', action=None):
    c.model.first, c.model.action = True, action
    c.model.calls.clear()
    body = {'input': text, 'session_id': session}
    headers = {'Idempotency-Key': key} if key else {}
    status, admitted = await c.bridge.client.request('POST', '/v1/runs', body=body, headers=headers)
    assert status == 202, admitted
    task = c.bridge.api._active_run_tasks.get(admitted['run_id'])
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), 30)
    status, result = await c.bridge.client.request('GET', '/v1/runs/' + admitted['run_id'])
    assert status == 200 and result['status'] == 'completed', result
    assert not c.bridge.api._active_run_agents
    return admitted, result


def confirm_text():
    return '确认\n```hermes-plus-gtd-confirm\n' + json.dumps(example('confirmation')['confirmation']) + '\n```'


def tool_results(c):
    return [json.loads(m['content']) for m in c.model.calls[-1]['messages'] if m.get('role') == 'tool']


def install_confirmation(c, monkeypatch):
    document = example('confirmation')
    cid = document['confirmation_id']
    seen = []

    async def details(request, body):
        run_id = request.headers['X-Ollo-Task-ID']
        status, host = await c.bridge.client.request('GET', '/v1/runs/' + run_id)
        seen.append((dict(request.headers), host))
        assert status == 200 and host['status'] == 'running'
        assert host['task_id'] == host['run_id'] == run_id
        assert host['session_id'] == request.headers['X-Ollo-Session-ID'] == 'tip'
        document['run_id'] = run_id
        return c.gateway.web.json_response(document)

    c.gateway.routes['GET', '/internal/v1/schedule/confirmations/' + cid] = details
    operations = Operations(c.gateway)
    for group in document['groups']:
        operations.add(group['operation_id'], {'kind': 'gtd_group', 'confirmation_id': cid,
            'report_id': document['confirmation']['report_id'], 'group': group, 'originals': document['originals']})

    async def apply(self, permit, **kwargs):
        return [{'index': i, 'status': 'done'} for i in permit['group']['indices']]
    monkeypatch.setattr(c.bridge.plugin.AppleBackend, 'group', apply)
    return cid, seen, operations


@pytest.mark.asyncio
async def test_confirmation_then_plain_turn_and_replay(canonical, monkeypatch):
    c = canonical
    cid, seen, operations = install_confirmation(c, monkeypatch)
    loop, replays = asyncio.get_running_loop(), []

    def replay_while_running():
        if replays:
            return
        status, replay = asyncio.run_coroutine_threadsafe(c.bridge.client.request(
            'POST', '/v1/runs', body={'input': confirm_text(), 'session_id': 'root'},
            headers={'Idempotency-Key': cid}), loop).result(timeout=10)
        replays.append((status, replay))
    c.model.inspect = replay_while_running
    assert cid not in confirm_text()
    admitted, result = await submit(c, confirm_text(), key=cid,
        action=('execute_gtd_confirmation', {'confirmation_id': cid}))
    assert f'confirmation_id={cid}' in json.dumps(c.model.calls[0]['messages'], ensure_ascii=False)
    assert len(seen) == 1 and len(operations.outcomes) == 2, tool_results(c)
    assert tool_results(c)[-1]['results'] == [{'index': i, 'status': 'done'} for i in range(3)]
    assert seen[0][0]['X-Ollo-Task-ID'] == admitted['run_id']
    assert replays[0][0] == 202 and replays[0][1]['replayed']
    assert replays[0][1]['run_id'] == admitted['run_id']
    assert result['session_id'] == 'tip' and result['task_id'] == admitted['run_id']
    assert c.binding.requests.get(c.bridge.api, admitted['run_id']) is None
    replay, _ = await submit(c, confirm_text(), key=cid)
    assert replay['replayed'] and replay['run_id'] == admitted['run_id']
    assert not c.model.calls and len(seen) == 1
    next_run, _ = await submit(c, '普通回合', key=str(uuid4()),
        action=('execute_gtd_confirmation', {'confirmation_id': cid}))
    assert next_run['run_id'] != admitted['run_id']
    assert tool_results(c)[-1]['error']['code'] == 'forbidden'
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('tool,args', [
    ('get_gtd_reports', {}),
    ('terminal', {'command': 'echo must-not-run'}),
    ('execute_gtd_confirmation', {'confirmation_id': '40000000-0000-4000-8000-000000000002'}),
])
async def test_confirmation_refuses_other_tools_and_wrong_uuid(canonical, tool, args):
    c = canonical
    cid = example('confirmation')['confirmation_id']
    await submit(c, confirm_text(), key=cid, action=(tool, args))
    result = tool_results(c)[-1]
    assert 'error' in result
    if tool == 'execute_gtd_confirmation':
        assert result['error']['code'] == 'forbidden'
    else:
        assert 'execute_gtd_confirmation' in str(result['error'])
    assert all('/confirmations/' not in path and '/reports' not in path for _, path, _, _ in c.gateway.calls)


@pytest.mark.asyncio
async def test_non_ollo_session_gets_no_capability(canonical):
    c = canonical
    cid = example('confirmation')['confirmation_id']
    await submit(c, confirm_text(), key=cid, session='other',
        action=('execute_gtd_confirmation', {'confirmation_id': cid}))
    assert 'error' in tool_results(c)[-1]
    assert f'confirmation_id={cid}' not in json.dumps(c.model.calls[0]['messages'])
    assert not c.gateway.calls


@pytest.mark.asyncio
async def test_execution_before_http_admission_returns(canonical, monkeypatch):
    """Force the legal FIFO race: a drain sees the commit before HTTP's awaiting caller resumes."""
    from gateway.platforms import api_server_runs
    c = canonical
    cid, seen, operations = install_confirmation(c, monkeypatch)
    original = api_server_runs._admit_run_to_authority
    before_response = []

    async def admit(*args, **kwargs):
        refused = await original(*args, **kwargs)
        launch = args[1]
        authority, ref, _ = launch.admission
        authority._schedule(ref)
        await asyncio.wait_for(asyncio.shield(authority.sessions[ref.session_id].task), 20)
        before_response.append((launch.run_id not in c.bridge.api._active_run_tasks, len(seen)))
        return refused
    monkeypatch.setattr(api_server_runs, '_admit_run_to_authority', admit)
    await submit(c, confirm_text(), key=cid, action=('execute_gtd_confirmation', {'confirmation_id': cid}))
    assert before_response == [(True, 1)]
    assert len(operations.outcomes) == 2, tool_results(c)
    assert not c.binding.requests._pending and not c.binding.requests._runs


@pytest.mark.asyncio
async def test_identity_fences_live_turn_and_expires(canonical, tmp_path, monkeypatch):
    from agent.delegation_context import delegated_child_context
    from gateway.session_api_turn import api_execution
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    c, captured = canonical, []
    cid = example('confirmation')['confirmation_id']

    def inspect():
        _, agent = c.authority.adopted['root']
        ids = (agent.session_id, agent._current_task_id, agent._current_turn_id)
        result = {'ids': ids, 'context': copy_context(), 'run': c.binding.current_run_id(*ids),
                  'bound': c.binding.bound(ids[0]), 'request': c.binding.request_id(*ids)}
        result['wrong_turn'] = c.binding.current_run_id(*ids[:2], 'old-turn')
        result['wrong_task'] = c.binding.current_run_id(ids[0], 'forged-task', ids[2])
        result['wrong_session'] = c.binding.current_run_id('other', *ids[1:])
        with delegated_child_context():
            result['delegate'] = c.binding.current_run_id(*ids)
        profile = set_hermes_home_override(tmp_path / 'other-profile')
        try:
            result['profile'] = c.binding.current_run_id(*ids)
            result['profile_bound'] = c.binding.bound(ids[0])
        finally:
            reset_hermes_home_override(profile)
        token = api_execution.set(None)
        try:
            result['non_api'] = c.binding.current_run_id(*ids)
        finally:
            api_execution.reset(token)
        with monkeypatch.context() as unavailable:
            unavailable.setattr(c.bridge.api, '_ensure_session_db', lambda: None)
            result['unavailable'] = c.binding.current_run_id(*ids)
            result['unavailable_guard'] = c.binding.guard('terminal', *ids)
        captured.append(result)
    c.model.inspect = inspect
    admitted, _ = await submit(c, confirm_text(), key=cid)
    first = captured[0]
    assert first['run'] == admitted['run_id'] and first['request'] == cid and first['bound'] is True
    assert all(first[k] is None for k in (
        'wrong_turn', 'wrong_task', 'wrong_session', 'delegate', 'profile', 'non_api', 'unavailable'))
    assert first['unavailable_guard']['action'] == 'block'
    assert first['profile_bound'] is False
    assert first['context'].run(c.binding.current_run_id, *first['ids']) is None
    await submit(c, '下一回合', key=str(uuid4()))
    assert captured[-1]['ids'][1] == first['ids'][1]
    assert captured[-1]['ids'][2] != first['ids'][2]
    assert captured[-1]['request'] != cid
    assert first['context'].run(c.binding.current_run_id, *first['ids']) is None


@pytest.mark.asyncio
async def test_missing_context_hook_blocks_generic_tool(canonical, monkeypatch):
    c = canonical
    monkeypatch.setitem(c.bridge.manager._hooks, 'pre_llm_call', [])
    await submit(c, confirm_text(), key=str(uuid4()), action=('terminal', {'command': 'echo must-not-run'}))
    assert '缺少可信的 Ollo' in str(tool_results(c)[-1]['error'])


@pytest.mark.asyncio
async def test_authority_store_not_ready_fails_closed(canonical, monkeypatch):
    from gateway.session_api_turn import api_execution
    from tools.approval_context import _approval_session_key
    c = canonical
    assert c.binding.bound('tip') is True
    monkeypatch.setattr(c.runner, 'session_authority', None)
    assert c.bridge.api._ensure_session_db() is None
    assert c.binding.bound('tip') is None
    assert c.binding.guard('terminal', 'tip', 'task', 'turn') is None
    # A caller without authority still cannot mint an Ollo capability, even
    # when it constructs the host's legacy live-agent/approval-key mapping.
    c.bridge.api._active_run_agents['legacy-run'] = SimpleNamespace(
        session_id='tip', _current_task_id='task', _current_turn_id='turn')
    approval = _approval_session_key.set('legacy-run')
    try:
        assert c.binding.current_run_id('tip', 'task', 'turn') is None
        assert c.binding.guard('terminal', 'tip', 'task', 'turn')['action'] == 'block'
    finally:
        _approval_session_key.reset(approval)
        c.bridge.api._active_run_agents.clear()
    token = api_execution.set({'adapter': c.bridge.api})
    try:
        assert c.binding.guard('terminal', 'tip', 'task', 'turn')['action'] == 'block'
    finally:
        api_execution.reset(token)


@pytest.mark.asyncio
async def test_cancelled_queued_confirmation_cannot_replay(canonical, monkeypatch):
    c = canonical
    monkeypatch.setattr(c.authority, '_classic_turn_holds', lambda route: True)
    cid = example('confirmation')['confirmation_id']
    body = {'input': confirm_text(), 'session_id': 'root'}
    headers = {'Idempotency-Key': cid}
    status, admitted = await c.bridge.client.request('POST', '/v1/runs', body=body, headers=headers)
    assert status == 202
    run_id = admitted['run_id']
    status, stopped = await c.bridge.client.request('POST', '/v1/runs/' + run_id + '/stop', body={})
    assert status == 200 and stopped['status'] == 'cancelled', stopped
    task = c.bridge.api._active_run_tasks.get(run_id)
    if task:
        await asyncio.wait_for(task, 10)
    status, replay = await c.bridge.client.request('POST', '/v1/runs', body=body, headers=headers)
    assert status == 202 and replay['replayed'] and replay['run_id'] == run_id
    assert not c.model.calls and c.binding.requests.get(c.bridge.api, run_id) is None


@pytest.mark.asyncio
async def test_recovered_queue_does_not_restore_confirmation_permission(canonical, monkeypatch):
    from importlib import import_module
    from gateway.session_api_turn import recover_api_turns
    from gateway.session_authority import initialize_session_authority
    c = canonical
    monkeypatch.setattr(c.authority, '_classic_turn_holds', lambda route: True)
    cid = example('confirmation')['confirmation_id']
    body, headers = {'input': confirm_text(), 'session_id': 'root'}, {'Idempotency-Key': cid}
    status, accepted = await c.bridge.client.request('POST', '/v1/runs', body=body, headers=headers)
    assert status == 202
    observer = c.bridge.api._active_run_tasks[accepted['run_id']]
    observer.cancel()
    await asyncio.gather(observer, return_exceptions=True)
    for live in c.authority.sessions.values():
        if live.task:
            live.task.cancel()
            await asyncio.gather(live.task, return_exceptions=True)
    # Exercise real epoch recovery on the same temporary store, with fresh
    # plugin request memory. This is not a process-kill/restart acceptance test.
    requests = import_module(c.bridge.plugin.__name__ + '.run_context').RunRequests
    c.binding.requests = requests()
    c.binding.turns.clear()
    c.authority = await initialize_session_authority(c.runner, profile_id=str(c.bridge.home),
        instance_id='ollo-recovered-owner', db=c.bridge.db)
    c.model.action = ('execute_gtd_confirmation', {'confirmation_id': cid})
    recover_api_turns(c.bridge.api)
    await asyncio.wait_for(asyncio.shield(c.authority.sessions['root'].task), 20)
    assert tool_results(c)[-1]['error']['code'] == 'forbidden'
    assert f'confirmation_id={cid}' not in json.dumps(c.model.calls[0]['messages'])
    assert all('/confirmations/' not in path for _, path, _, _ in c.gateway.calls)
    count = len(c.model.calls)
    status, replay = await c.bridge.client.request('POST', '/v1/runs', body=body, headers=headers)
    assert status == 202 and replay['replayed'] and replay['run_id'] == accepted['run_id']
    assert len(c.model.calls) == count
    assert c.binding.requests.get(c.bridge.api, accepted['run_id']) is None
