"""Final-output classification through real plugin dispatch; no provider network."""
import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def ollo(runtime, isolated_home, monkeypatch):
    directory = isolated_home / 'ollo'
    directory.mkdir()
    (directory / 'conversation.json').write_text('{"session_id":"root"}')
    with runtime() as r:
        from hermes_cli import plugins
        monkeypatch.setattr(plugins, 'get_plugin_manager', lambda: r.manager)
        r.db.create_session('root', 'api_server')
        r.db.end_session('root', 'compression')
        r.db.create_session('middle', 'api_server', parent_session_id='root')
        r.db.end_session('middle', 'compression')
        r.db.create_session('tip', 'api_server', parent_session_id='middle')
        r.db.create_session('other', 'api_server')
        for sid, marker in (('branch', '_branched_from'), ('delegate', '_delegate_from'), ('reset', '_reset_from')):
            r.db.create_session(sid, 'api_server', parent_session_id='root', model_config={marker: 'root'})
        yield r


def prepare(r, turn='turn', *, session='root', platform='api_server', user='家里人发烧怎么办？', history=None):
    return r.manager.invoke_hook('pre_llm_call', session_id=session, platform=platform,
        turn_id=turn, user_message=user, conversation_history=history or [])


def transform(r, text, turn='turn', *, session='tip', platform='api_server'):
    results = r.manager.invoke_hook('transform_llm_output', session_id=session, platform=platform,
        turn_id=turn, response_text=text, model='synthetic-main-model')
    return next((result for result in results if isinstance(result, str) and result), text)


@pytest.mark.parametrize('label', ['concerned', 'happy', 'none'])
def test_classifier_owns_the_only_trailing_mood_and_preserves_schedule(ollo, monkeypatch, label):
    from agent import auxiliary_client
    calls = []
    def classify(**kwargs):
        calls.append(kwargs)
        assert auxiliary_client._get_auxiliary_task_config(kwargs['task'])['timeout'] == 3
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=label))])
    monkeypatch.setattr(auxiliary_client, 'call_llm', classify)
    before = '回复正文。\n\n```hermes-plus-gtd-result\n{"ok":true}\n```'
    raw = before + '\n\n```ollo-mood\n{"mood":"happy"}\n```\n\n```ollo-mood\n{"mood":"concerned"}\n```'
    history = [{'role':'user','content':'家里人昨晚发烧。'}, {'role':'assistant','content':'我有些担心。'}]
    prepare(ollo, user='那现在呢？', history=history)
    actual = transform(ollo, raw)
    expected = before if label == 'none' else before + '\n\n```ollo-mood\n' + json.dumps({'mood':label}, separators=(',', ':')) + '\n```'
    assert actual == expected
    assert len(calls) == 1
    request = calls[0]
    assert request['task'] == 'ollo_mood' and request['timeout'] <= 3
    payload = json.loads(request['messages'][1]['content'])
    assert payload['user_message'] == '那现在呢？'
    assert payload['assistant_response'] == before
    assert '发烧' in json.dumps(payload['recent_context'], ensure_ascii=False)
    assert 'ollo-mood' not in prepare(ollo, turn='next')[0]['context']


@pytest.mark.parametrize('outcome', [RuntimeError('private text must not be logged'), TimeoutError('secret timeout'), AttributeError('auxiliary unavailable'), None, 'HAPPY', 'not happy', '{"mood":"happy"}'])
def test_failure_and_invalid_classification_leave_unmarked_body(ollo, monkeypatch, caplog, outcome):
    from agent.plugin_llm import PluginLlm
    def classify(*args, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(text=outcome)
    monkeypatch.setattr(PluginLlm, 'complete', classify)
    prepare(ollo)
    body = '回复本身不受影响。'
    assert transform(ollo, body + '\n\n```ollo-mood\n{"mood":"happy"}\n```') == body
    warnings = [r for r in caplog.records if 'Ollo mood' in r.message]
    assert len(warnings) == 1
    assert 'private text' not in caplog.text and 'secret timeout' not in caplog.text


def test_only_bound_api_turns_classify_and_turn_inputs_do_not_cross(ollo, monkeypatch):
    from agent.plugin_llm import PluginLlm
    calls = []
    def classify(self, messages, **kwargs):
        user = json.loads(messages[1]['content'])['user_message']
        calls.append(user)
        return SimpleNamespace(text='concerned' if user == '发烧' else 'happy')
    monkeypatch.setattr(PluginLlm, 'complete', classify)
    raw = '原文\n\n```ollo-mood\n{"mood":"happy"}\n```'
    for sid, channel in [('other','api_server'), ('branch','api_server'), ('delegate','api_server'),
                         ('reset','api_server'), ('missing','api_server'), ('tip','photon'),
                         ('tip','imessage'), ('tip','cli')]:
        prepare(ollo, session=sid, platform=channel)
        assert transform(ollo, raw, session=sid, platform=channel) == raw
    assert calls == []
    prepare(ollo, turn='sick', user='发烧')
    prepare(ollo, turn='good', user='办成了')
    assert '"mood":"happy"' in transform(ollo, '已完成。', turn='good')
    assert '"mood":"concerned"' in transform(ollo, '请休息。', turn='sick')
    assert calls == ['办成了', '发烧']
    # A completed/cancelled turn must never provide another turn's user input.
    prepare(ollo, turn='cancelled')
    ollo.manager.invoke_hook('on_session_end', session_id='tip', turn_id='cancelled', platform='api_server')
    assert transform(ollo, '保留正文', turn='cancelled') == '保留正文'
    assert calls == ['办成了', '发烧']


def test_total_timeout_returns_body_and_does_not_spawn_more_stuck_classifiers(ollo, monkeypatch, caplog):
    import threading
    import time
    from agent.plugin_llm import PluginLlm
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def stuck(*args, **kwargs):
        calls.append(kwargs)
        started.set()
        try:
            assert release.wait(15)
            return SimpleNamespace(text='happy')
        finally:
            finished.set()
    monkeypatch.setattr(PluginLlm, 'complete', stuck)
    prepare(ollo)
    begin = time.monotonic()
    try:
        assert transform(ollo, '不会等待模型恢复。') == '不会等待模型恢复。'
        elapsed = time.monotonic() - begin
        assert started.is_set() and 2 <= elapsed < 6
        prepare(ollo, turn='another')
        assert transform(ollo, '下一条也正常返回。', turn='another') == '下一条也正常返回。'
        assert len(calls) == 1
        assert 'classification timed out' in caplog.text
        assert 'previous classifier still running' in caplog.text
    finally:
        release.set()
        assert finished.wait(3)


def test_mood_removal_respects_other_fences_and_incomplete_marker_tail():
    from conftest import load_plugin_module
    mood = load_plugin_module('conversation', 'mood.py')
    example = '````text\n```ollo-mood\n{"mood":"happy"}\n```\n````'
    schedule = '```hermes-plus-gtd-result\n{"ok":true}\n```'
    body = example + '\n\n' + schedule
    assert mood.strip_mood(body) == body
    assert mood.strip_mood(body + '\n\n~~~ollo-mood\n{"mood":"happy"}\n~~~') == body
    assert mood.strip_mood(body + '\n\n```ollo-mood\n{"mood":"happy"}') == body
    plain = '正常正文。\n\n'
    assert mood.with_mood(mood.strip_mood(plain), 'none') == plain


@pytest.mark.asyncio
async def test_classified_text_is_stored_replayed_and_returned_by_runs(ollo, monkeypatch):
    import asyncio
    from unittest.mock import MagicMock, patch
    from aiohttp.test_utils import make_mocked_request
    from gateway.platforms import api_server
    from gateway.platforms.api_server_runs import _RunLaunch, _execute_run
    from run_agent import AIAgent
    from agent import auxiliary_client
    calls = []
    def classify(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='concerned'))])
    monkeypatch.setattr(auxiliary_client, 'call_llm', classify)
    monkeypatch.setattr('hermes_cli.lifecycle.invoke_hook', ollo.manager.invoke_hook)
    metadata = lambda *a, **k: {'fake/model': {'context_length': 128000}}
    monkeypatch.setattr('agent.model_metadata.fetch_model_metadata', metadata)
    monkeypatch.setattr('agent.agent_init.fetch_model_metadata', metadata)
    raw = '回复正文。\n\n```hermes-plus-gtd-result\n{"ok":true}\n```'
    def primary(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=raw, tool_calls=None, reasoning=None), finish_reason='stop')],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15), model='fake/model')
    with patch('model_tools.get_tool_definitions', return_value=[]), \
         patch('model_tools.check_toolset_requirements', return_value={}), \
         patch('agent.process_bootstrap.OpenAI'):
        agent = AIAgent(api_key='test-key-1234567890', base_url='https://openrouter.ai/api/v1',
            model='fake/model', quiet_mode=True, skip_context_files=True, skip_memory=True,
            platform='api_server', session_id='tip', session_db=ollo.db)
    agent.client = MagicMock()
    agent.client.chat.completions.create = primary
    monkeypatch.setattr(ollo.api, '_create_agent', lambda **kwargs: agent)
    run_id = 'isolated-mood-run'
    request = make_mocked_request('GET', '/v1/runs/' + run_id,
        headers={'Authorization':'Bearer isolated-owner-key'}, match_info={'run_id':run_id})
    ollo.api._run_owners[run_id] = ollo.api._run_idempotency_scope(request)
    queue = asyncio.Queue()
    ollo.api._run_streams[run_id] = queue
    run = _RunLaunch(owner=ollo.api, run_id=run_id, queue=queue, session_id='tip',
        gateway_session_key=None, declared_selected=False, user_message='家里人发烧怎么办？',
        conversation_history=[], session_history_delivery=True, agent_kwargs={'room_dispatch':None}, request_profile=None,
        browser_control_principal='', browser_control_transport_family='')
    await _execute_run(ollo.api, run, _api_server=api_server)
    response = await ollo.api._handle_get_run(request)
    status = json.loads(response.body)
    assert response.status == 200 and status['status'] == 'completed', status
    expected = raw + '\n\n```ollo-mood\n{"mood":"concerned"}\n```'
    assert status['output'] == expected
    rows = ollo.db.get_messages('tip')
    assert [row['content'] for row in rows if row['role'] == 'assistant'] == [expected]
    replay = ollo.db.get_messages_as_conversation('tip')
    assert replay[-1]['content'] == expected
    assert sum(call['task'] == 'ollo_mood' for call in calls) == 1
    user = next(row for row in rows if row['role'] == 'user')
    assert 'Ollo' in user['api_content'] and 'ollo-mood' not in user['api_content']
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    completed = next(event for event in events if event and event.get('event') == 'run.completed')
    assert completed['output'] == expected
