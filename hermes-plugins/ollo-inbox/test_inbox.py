import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import uuid


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store_module = load('inbox_store_tests', 'store.py')
apns = load('inbox_apns_tests', 'apns.py')
plugin = load('inbox_plugin_tests', '__init__.py')
compatibility = load('inbox_compatibility_tests', 'compatibility.py')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1000
        self.store = store_module.InboxStore(Path(self.directory.name) / 'inbox.sqlite', clock=lambda: self.now)
        self.device = str(uuid.uuid4())

    def deliver(self, execution=None, body='收件箱为空。', session='main'):
        return self.store.deliver(session, 'job', execution or str(uuid.uuid4()), '清理收件箱', body)

    def enable(self):
        self.store.register(self.device, 'main', 'aa11', 'sandbox')

    def test_duplicate_concurrent_delivery_and_restart_are_idempotent(self):
        execution = str(uuid.uuid4())
        with ThreadPoolExecutor(max_workers=8) as pool:
            reports = list(pool.map(lambda _: self.deliver(execution), range(16)))
        self.assertEqual(len({r['id'] for r in reports}), 1)
        reloaded = store_module.InboxStore(self.store.path)
        self.assertEqual(len(reloaded.list('main')), 1)
        self.assertEqual(reloaded.deliver('main', 'job', execution, '清理收件箱', '收件箱为空。')['id'], reports[0]['id'])

    def test_missing_execution_or_conflicting_retry_fails_closed(self):
        with self.assertRaises(ValueError):
            self.store.deliver('main', 'job', None, '报告', '正文')
        execution = str(uuid.uuid4())
        self.deliver(execution)
        with self.assertRaises(ValueError):
            self.deliver(execution, body='被覆盖的内容')
        self.assertEqual(self.store.list('main')[0]['body'], '收件箱为空。')

    def test_identical_reports_from_different_executions_are_both_kept(self):
        self.deliver(); self.deliver()
        self.assertEqual(len(self.store.list('main')), 2)

    def test_cursor_pagination_and_conversation_isolation(self):
        first, second = self.deliver(), self.deliver()
        self.deliver(session='other')
        self.assertEqual(self.store.list('main', after=first['sequence']), [second])
        self.assertEqual(self.store.list('main', before=second['sequence']), [first])
        with self.assertRaises(LookupError):
            self.store.get('other', first['id'])
        with self.assertRaises(ValueError):
            self.store.list('main', after=-1)

    def test_enabling_notifications_does_not_alert_old_reports(self):
        self.deliver(); self.enable()
        self.assertIsNone(self.store.claim())
        fresh = self.deliver()
        self.assertEqual(self.store.claim()['report'], fresh['id'])

    def test_revoke_cancels_push_but_preserves_report(self):
        self.enable(); report = self.deliver()
        self.store.revoke(self.device, 'main')
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.get('main', report['id']), report)

    def test_rotation_and_stale_rejection_cannot_invalidate_new_token(self):
        self.enable(); self.deliver(); old = self.store.claim()
        self.now += 1
        self.store.register(self.device, 'main', 'bb22', 'production')
        self.store.finish(old, apns.APNsResult('rejected', 'Unregistered', True, self.now - 1))
        new = self.deliver(); claimed = self.store.claim()
        self.assertEqual(claimed['token'], 'bb22')
        self.assertEqual(claimed['report'], new['id'])

    def test_failed_push_retries_without_recreating_report(self):
        self.enable(); report = self.deliver(); claimed = self.store.claim()
        self.store.finish(claimed, apns.APNsResult('transport_error'))
        self.assertIsNone(self.store.claim())
        self.now += 61
        retry = self.store.claim()
        self.assertEqual(retry['report'], report['id'])
        self.assertEqual(retry['attempts'], 2)
        self.store.finish(retry, apns.APNsResult('accepted'))
        self.now += 100
        self.assertIsNone(self.store.claim())
        self.assertEqual(len(self.store.list('main')), 1)

    def test_crash_lease_expires_and_read_prevents_retry(self):
        self.enable(); report = self.deliver(); self.store.claim()
        self.now += 61
        retry = self.store.claim()
        self.assertEqual(retry['attempts'], 2)
        self.store.read('main', report['id'], self.device)
        self.store.finish(retry, apns.APNsResult('transport_error'))
        self.now += 100
        self.assertIsNone(self.store.claim())

    def test_device_cannot_be_moved_to_another_conversation(self):
        self.enable()
        with self.assertRaises(PermissionError):
            self.store.register(self.device, 'other', 'aa11', 'sandbox')
        with self.assertRaises(PermissionError):
            self.store.revoke(self.device, 'other')


class PushTests(unittest.IsolatedAsyncioTestCase):
    async def test_payload_is_generic_and_uses_alert_headers_and_stable_id(self):
        captured = {}
        async def post(url, **kwargs):
            captured.update(url=url, **kwargs)
            return types.SimpleNamespace(http_version='HTTP/2', status_code=200)
        provider = apns.APNsProvider(key_path='/test/key.p8', key_id='A' * 10, team_id='B' * 10, topic='com.birderyu.ollo')
        provider._http_client = lambda: types.SimpleNamespace(post=post)
        provider._provider_token = lambda _: 'fixture-token'
        report = str(uuid.uuid4())
        result = await provider.send_report('aa11', 'sandbox', report)
        self.assertEqual(result.status, 'accepted')
        self.assertEqual(captured['headers']['apns-push-type'], 'alert')
        self.assertEqual(captured['headers']['apns-collapse-id'], report)
        self.assertEqual(captured['json']['hermes_plus'], {'capability': 'inbox.report', 'report_id': report})
        self.assertNotIn('session', json.dumps(captured['json']))
        self.assertEqual(captured['json']['aps']['alert']['body'], '有新的 GTD 报告，打开查看。')

    async def test_unconfigured_push_does_not_open_key_or_network(self):
        provider = apns.APNsProvider()
        provider._http_client = lambda: self.fail('Unexpected network access')
        self.assertEqual((await provider.send_report('aa11', 'sandbox', str(uuid.uuid4()))).status, 'not_configured')

    async def test_transport_exception_never_exposes_token(self):
        async def post(*args, **kwargs):
            raise RuntimeError('secret-device-token')
        provider = apns.APNsProvider(key_path='/test/key.p8', key_id='A' * 10, team_id='B' * 10, topic='com.birderyu.ollo')
        provider._http_client = lambda: types.SimpleNamespace(post=post)
        provider._provider_token = lambda _: 'secret-jwt'
        self.assertEqual(await provider.send_report('aa11', 'sandbox', str(uuid.uuid4())), apns.APNsResult('transport_error'))


class PluginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        (self.home / 'ollo').mkdir()
        (self.home / 'ollo/inbox.json').write_text(json.dumps({'session_id': 'main', 'jobs': {'job': '今日安排'}}))
        class Base:
            def __init__(self, config, platform):
                pass
        modules = {'hermes_constants': types.SimpleNamespace(get_hermes_home=lambda: self.home),
                   'gateway.platforms.base': types.SimpleNamespace(BasePlatformAdapter=Base, SendResult=lambda **kwargs: types.SimpleNamespace(**kwargs)),
                   'gateway.config': types.SimpleNamespace(Platform=str)}
        class Context:
            hooks, tools = {}, {}
            def register_platform(ctx, name, label, factory, check, **kwargs):
                ctx.factory = factory; ctx.platform = kwargs
            def register_platform_handler(ctx, name, callback):
                ctx.wire = callback
            def register_hook(ctx, name, callback):
                ctx.hooks[name] = callback
            def register_tool(ctx, **kwargs):
                ctx.tools[kwargs['name']] = kwargs['handler']
        self.ctx = Context()
        with patch.dict('sys.modules', modules):
            plugin.register(self.ctx)
            self.adapter = self.ctx.factory({})

    async def test_adapter_requires_configured_job_and_execution_and_retries_once(self):
        denied = await self.adapter.send('main', 'report', metadata={'job_id': 'job'})
        self.assertFalse(denied.success)
        self.assertFalse((await self.adapter.send('other', 'report', metadata={})).success)
        meta = {'job_id': 'job', 'execution_id': uuid.uuid4().hex}
        first = await self.adapter.send('main', 'report', metadata=meta)
        second = await self.adapter.send('main', 'report', metadata=meta)
        self.assertTrue(first.success)
        self.assertEqual(first.message_id, second.message_id)
        self.assertIn('error', await self.ctx.platform['standalone_sender_fn']())

    async def test_context_is_bound_to_api_conversation_and_compression_chain(self):
        class App:
            cleanup_ctx = []
            router = types.SimpleNamespace(add_get=lambda *args: None, add_put=lambda *args: None,
                add_delete=lambda *args: None, add_post=lambda *args: None)
        db = types.SimpleNamespace(get_session=lambda session: {'id': session} if session in ('main','tip','branch') else None,
            resolve_resume_session_id=lambda session: 'tip' if session == 'main' else session)
        with patch.dict('sys.modules', {'aiohttp': types.SimpleNamespace(web=object())}):
            self.ctx.wire(App(), types.SimpleNamespace(_ensure_session_db=lambda: db))
        await self.adapter.send('main', '私有报告', metadata={'job_id': 'job', 'execution_id': uuid.uuid4().hex})
        hook = self.ctx.hooks['pre_llm_call']
        self.assertIsNone(hook(session_id='branch', platform='api_server'))
        self.assertIsNone(hook(session_id='tip', platform='photon'))
        self.assertIn('私有报告', hook(session_id='tip', platform='api_server')['context'])
        self.assertIn('error', self.ctx.tools['get_gtd_reports']({}, session_id='branch'))
        self.assertEqual(len(json.loads(self.ctx.tools['get_gtd_reports']({}, session_id='tip'))['reports']), 1)
        hook(session_id='tip', platform='photon')
        self.assertIn('error', self.ctx.tools['get_gtd_reports']({}, session_id='tip'))


class CompatibilityTests(unittest.TestCase):
    def test_scheduler_envelope_is_removed_without_rewriting_report(self):
        body = '今日安排\n\n报告正文'
        envelope = 'Cronjob Response: GTD-今日安排\n(job_id: job)\n-------------\n\n' + body + '\n\nTo stop or manage this job, send me a new message (e.g. "stop reminder GTD-今日安排").'
        self.assertEqual(plugin.report_body(envelope, 'job'), body)
        self.assertEqual(plugin.report_body(body, 'job'), body)
        self.assertEqual(plugin.report_body(envelope, 'other'), envelope)

    def test_idempotent_patch_and_upstream_drift_rejection(self):
        source = '    ' + compatibility.OLD + '\n'
        patched = compatibility.patched(source)
        self.assertIn('execution_id', patched)
        self.assertEqual(compatibility.patched(patched), patched)
        with self.assertRaises(ValueError):
            compatibility.patched('upstream changed')


if __name__ == '__main__':
    unittest.main()
