"""Run with Hermes' Python environment; only a temporary home and loopback HTTP.

No model calls, real reminders, cron executions, pushes, or live gateway writes.
"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import uuid


async def main():
    with tempfile.TemporaryDirectory(prefix='ollo-inbox-integration-') as folder:
        os.environ['HERMES_HOME'] = folder
        for name in ('KEY_PATH', 'KEY_ID', 'TEAM_ID', 'TOPIC'):
            os.environ.pop('OLLO_APNS_' + name, None)
            os.environ.pop('HERMES_PLUS_APNS_' + name, None)
        home = Path(folder)
        (home / 'ollo').mkdir()
        (home / 'ollo/inbox.json').write_text(json.dumps({'session_id': 'main', 'jobs': {'fixture-job': '今日安排'}}))
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        from gateway.platform_registry import platform_registry, PlatformEntry
        from gateway.config import PlatformConfig
        from hermes_constants import get_hermes_home
        assert get_hermes_home() == home, 'Refusing non-isolated Hermes home'

        class Context:
            hooks = {}
            def register_platform(self, name, label, factory, check, **kwargs):
                platform_registry.register(PlatformEntry(name=name, label=label, adapter_factory=factory, check_fn=check, **kwargs))
                self.factory = factory
            def register_platform_handler(self, platform, callback):
                self.wire = callback
            def register_hook(self, name, callback):
                self.hooks[name] = callback
            def register_tool(self, **kwargs):
                self.tool = kwargs['handler']

        spec = importlib.util.spec_from_file_location('ollo_inbox_sandbox', Path(__file__).with_name('__init__.py'))
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        ctx = Context()
        plugin.register(ctx)
        adapter = ctx.factory(PlatformConfig())
        assert await adapter.connect()
        meta = {'job_id': 'fixture-job', 'execution_id': uuid.uuid4().hex}
        delivered = await adapter.send('main', '隔离测试报告；不包含真实事项。', metadata=meta)
        assert delivered.success
        repeated = await adapter.send('main', '隔离测试报告；不包含真实事项。', metadata=meta)
        assert repeated.message_id == delivered.message_id
        assert not (await adapter.send('main', 'missing identity', metadata={'job_id': 'fixture-job'})).success
        db = types.SimpleNamespace(get_session=lambda sid: {'id': sid} if sid in ('main', 'tip', 'foreign') else None,
            resolve_resume_session_id=lambda sid: 'tip' if sid == 'main' else sid)
        def check_auth(request):
            if request.headers.get('Authorization') != 'Bearer isolated-fixture':
                return web.json_response({'error': 'Unauthorized'}, status=401)
            return None
        api = types.SimpleNamespace(_ensure_session_db=lambda: db, _expected_api_key=lambda: 'isolated-fixture', _check_auth=check_auth)
        app = web.Application()
        ctx.wire(app, api)
        headers = {'Authorization': 'Bearer isolated-fixture'}
        async with TestClient(TestServer(app)) as client:
            response = await client.get('/v1/ollo/inbox/reports?session_id=main')
            assert response.status == 401
            response = await client.get('/v1/ollo/inbox/reports?session_id=foreign', headers=headers)
            assert response.status == 403
            response = await client.get('/v1/ollo/inbox/reports?session_id=tip', headers=headers)
            data = await response.json()
            assert response.status == 200 and len(data['reports']) == 1
            assert data['reports'][0]['id'] == delivered.message_id
            response = await client.get('/v1/ollo/inbox/reports?session_id=main&after=1', headers=headers)
            assert (await response.json())['reports'] == []
            response = await client.get('/v1/ollo/inbox/reports?session_id=main&after=-1', headers=headers)
            assert response.status == 400
            response = await client.get('/v1/ollo/inbox/reports/' + delivered.message_id + '?session_id=main', headers=headers)
            assert response.status == 200 and (await response.json())['body'].startswith('隔离测试报告')
            response = await client.put('/v1/ollo/inbox/devices/' + str(uuid.uuid4()), headers=headers,
                json={'session_id': 'main', 'token': 'aa11', 'environment': 'sandbox'})
            assert response.status == 503
            hook = ctx.hooks['pre_llm_call']
            assert hook(platform='api_server', session_id='foreign') is None
            assert '隔离测试报告' in hook(platform='api_server', session_id='tip')['context']
            assert len(json.loads(ctx.tool({}, session_id='tip'))['reports']) == 1
            print('PASS: real Hermes adapter/registry, durable retry, aiohttp auth/scope/cursor/report lookup, compressed-session model context. No live data accessed.')
        await adapter.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
