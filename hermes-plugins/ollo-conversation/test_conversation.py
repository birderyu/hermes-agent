"""Session binding, read-only discovery, and profile boundaries through real imports."""
import json

import pytest

from conftest import load_plugin_module


@pytest.mark.asyncio
@pytest.mark.parametrize('directory', ['ollo', 'hermes-plus'])
async def test_discovery_auth_config_and_read_only_contract(runtime, isolated_home, directory):
    config_dir = isolated_home / directory
    config_dir.mkdir()
    config = {'session_id': 'existing-root', 'name': 'Ollo 测试'}
    (config_dir / 'conversation.json').write_text(json.dumps(config))
    with runtime() as r:
        # Even with no matching DB session, discovery returns configuration only.
        # It must neither open SessionDB via the adapter nor mint a session.
        def no_session_access():
            pytest.fail('Discovery must not open the session database')
        r.api._ensure_session_db = no_session_access
        for prefix in ('ollo', 'hermes-plus'):
            path = '/v1/' + prefix + '/conversation'
            assert (await r.client.request('GET', path, token=None))[0] == 401
            assert (await r.client.request('GET', path, token='wrong'))[0] == 401
            assert await r.client.request('GET', path) == (200, config)
            assert r.db.get_session(config['session_id']) is None
        r.api._api_key = ''
        assert (await r.client.request('GET', '/v1/ollo/conversation'))[0] == 503


@pytest.mark.asyncio
@pytest.mark.parametrize('raw', [None, '{bad', '[]', '{}', '{"session_id":""}', '{"session_id":"../x"}', '{"session_id":"root","name":3}'])
async def test_missing_or_invalid_config_is_503_and_injects_nothing(runtime, isolated_home, raw):
    if raw is not None:
        directory = isolated_home / 'ollo'
        directory.mkdir()
        (directory / 'conversation.json').write_text(raw)
    with runtime() as r:
        assert (await r.client.request('GET', '/v1/ollo/conversation', token='wrong'))[0] == 401
        assert (await r.client.request('GET', '/v1/ollo/conversation'))[0] == 503
        assert r.manager.invoke_hook('pre_llm_call', session_id='root', platform='api_server') == []


def test_config_directory_precedence_and_legacy_default_name(isolated_home):
    plugin = load_plugin_module('conversation')
    legacy = isolated_home / 'hermes-plus'
    legacy.mkdir()
    (legacy / 'conversation.json').write_text('{"session_id":"legacy"}')
    assert plugin.read_config(isolated_home) == {'session_id': 'legacy', 'name': 'Ollo'}
    new = isolated_home / 'ollo'
    new.mkdir()
    assert plugin.read_config(isolated_home) is None
    (new / 'conversation.json').write_text('{"session_id":"new","name":"Configured name"}')
    assert plugin.read_config(isolated_home) == {'session_id': 'new', 'name': 'Configured name'}


@pytest.mark.asyncio
async def test_only_ollo_root_and_compression_continuations_receive_conventions(runtime, isolated_home):
    directory = isolated_home / 'ollo'
    directory.mkdir()
    (directory / 'conversation.json').write_text('{"session_id":"root"}')
    with runtime() as r:
        r.db.create_session('root', 'api_server', system_prompt='stable system prompt')
        r.db.append_message('root', 'user', 'hello')
        r.db.end_session('root', 'compression')
        r.db.create_session('middle', 'api_server', parent_session_id='root')
        r.db.end_session('middle', 'compression')
        r.db.create_session('tip', 'api_server', parent_session_id='middle')
        r.db.append_message('tip', 'assistant', 'existing response')
        for fork, marker in (('branch', '_branched_from'), ('delegate', '_delegate_from'), ('reset', '_reset_from')):
            r.db.create_session(fork, 'api_server', parent_session_id='root', model_config={marker: 'root'})
        r.db.create_session('other', 'api_server')
        r.db.create_session('imessage', 'photon')
        before = r.db.get_messages_as_conversation('tip')
        hook = lambda sid, platform='api_server': r.manager.invoke_hook('pre_llm_call', session_id=sid, platform=platform)
        expected = r.manager._plugins['ollo-conversation'].module.CONVENTIONS
        for sid in ('root', 'middle', 'tip'):
            assert hook(sid) == [{'context': expected}]
            for channel in ('photon', 'imessage', 'telegram', 'cli', '', None):
                assert hook(sid, channel) == []
        for sid in ('branch', 'delegate', 'reset', 'other', 'imessage', 'missing', None):
            assert hook(sid) == []
        assert hook('tip') == [{'context': expected}]
        assert r.db.get_messages_as_conversation('tip') == before
        assert r.db.get_session('root')['system_prompt'] == 'stable system prompt'
        assert '自称 Ollo' in expected
        assert 'ollo-mood' not in expected
        assert 'happy' not in expected and 'concerned' not in expected


@pytest.mark.asyncio
async def test_profile_scope_a_b_a_does_not_reuse_other_profiles_binding(runtime, isolated_home, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agent import auxiliary_client
    from agent.secret_scope import set_multiplex_active
    from hermes_cli import plugins
    from hermes_constants import get_hermes_home, set_hermes_home_override, reset_hermes_home_override
    homes = [isolated_home, tmp_path / 'second-home']
    for index, home in enumerate(homes):
        (home / 'ollo').mkdir(parents=True)
        (home / 'ollo/conversation.json').write_text(json.dumps({'session_id': 'root', 'name': str(index)}))
    set_multiplex_active(True)
    try:
        with runtime(home=homes[0]) as a, runtime(home=homes[1]) as b:
            managers = {r.home: r.manager for r in (a, b)}
            monkeypatch.setattr(plugins, 'get_plugin_manager', lambda: managers[get_hermes_home()])
            calls = []
            def classify(**kwargs):
                calls.append(get_hermes_home())
                assert auxiliary_client._get_auxiliary_task_config(kwargs['task'])['timeout'] == 3
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='concerned'))])
            monkeypatch.setattr(auxiliary_client, 'call_llm', classify)
            for r in (a, b):
                r.db.create_session('root', 'api_server')
            for r, other in ((a, b), (b, a), (a, b)):
                token = set_hermes_home_override(r.home)
                try:
                    assert (await r.client.request('GET', '/v1/ollo/conversation'))[1]['name'] == str(homes.index(r.home))
                    assert r.manager.invoke_hook('pre_llm_call', session_id='root', platform='api_server',
                        turn_id='turn', user_message='发烧怎么办？')
                    assert other.manager.invoke_hook('pre_llm_call', session_id='root', platform='api_server') == []
                    result = r.manager.invoke_hook('transform_llm_output', response_text='回复正文。',
                        session_id='root', platform='api_server', turn_id='turn')
                    assert len(result) == 1 and '"mood":"concerned"' in result[0]
                    assert other.manager.invoke_hook('transform_llm_output', response_text='其他渠道原文。',
                        session_id='root', platform='api_server', turn_id='turn') == []
                finally:
                    reset_hermes_home_override(token)
            assert calls == [homes[0], homes[1], homes[0]]
    finally:
        set_multiplex_active(False)


@pytest.mark.asyncio
async def test_all_three_plugins_coexist_without_injecting_mood_into_other_sessions(runtime, isolated_home):
    directory = isolated_home / 'ollo'
    directory.mkdir()
    (directory / 'conversation.json').write_text('{"session_id":"root","name":"Ollo"}')
    (directory / 'inbox.json').write_text('{"session_id":"root","jobs":{"job":"Report"}}')
    with runtime(('conversation', 'inbox', 'location')) as r:
        r.db.create_session('root', 'api_server')
        r.db.create_session('other', 'api_server')
        for namespace in ('ollo', 'hermes-plus'):
            for path in ('conversation', 'inbox?session_id=root', 'location'):
                assert (await r.client.request('GET', '/v1/' + namespace + '/' + path))[0] == 200
        expected = r.manager._plugins['ollo-conversation'].module.CONVENTIONS
        ollo = r.manager.invoke_hook('pre_llm_call', session_id='root', platform='api_server')
        assert sum(item.get('context') == expected for item in ollo) == 1
        for sid, platform in (('other', 'api_server'), ('root', 'photon'), ('root', 'cli')):
            results = r.manager.invoke_hook('pre_llm_call', session_id=sid, platform=platform)
            assert all('ollo-mood' not in item.get('context', '') for item in results)
