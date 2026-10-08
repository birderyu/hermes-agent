"""No live location, model, gateway, credential, or push service is accessed."""
import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import types
import unittest
import uuid
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('device_location_plugin', Path(__file__).with_name('__init__.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class DeviceStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = m.DeviceStore(Path(self.tmp.name) / 'location.sqlite')
        self.device = str(uuid.uuid4())
        self.auth = self.store.authorize(self.device, 'api', 'My iPhone', 'iOS', now=1000)
        self.token = self.auth['device_token']
        self.grant = self.auth['grant_id']
        self.point = dict(latitude=30., longitude=120., accuracy=10., timestamp=1000.)

    def observe(self, seq=1, now=1000, request=None, point=None):
        self.store.observe(self.token, self.grant, seq, point or self.point, request, now=now)

    def test_long_authorization_and_short_data_lifetime_are_independent(self):
        self.observe()
        self.assertEqual(self.store.request('api', now=1001)['status'], 'ok')
        later = self.store.state(self.token, now=9000)
        self.assertTrue(later['authorized'])
        self.assertIsNone(later['latest'])
        self.assertEqual(self.store.request('api', now=9000)['status'], 'pending')

    def test_cache_returns_source_and_accuracy(self):
        self.observe()
        result = self.store.request('api', now=1001)
        self.assertTrue(result['cached'])
        self.assertEqual(result['device_id'], self.device)
        self.assertEqual(result['point']['accuracy'], 10)
        self.assertEqual(result['age_seconds'], 1)

    def test_zero_age_requests_new_sample_and_accepts_after_request(self):
        self.observe()
        req = self.store.request('api', max_age=0, now=1001)
        with self.assertRaises(ValueError):
            self.observe(2, 1001, req['id'])
        self.observe(2, 1002, req['id'], {**self.point, 'timestamp': 1001.1})
        self.assertEqual(self.store.result(req['id'], 'api', now=1002)['status'], 'ok')

    def test_revoke_clears_cached_and_request_data_and_blocks_late_delivery(self):
        self.observe()
        req = self.store.request('api', max_age=0, now=1001)
        self.store.revoke(self.device)
        with self.assertRaises(PermissionError):
            self.observe(2, 1002, req['id'], {**self.point, 'timestamp': 1002})
        with self.assertRaises(PermissionError):
            self.store.state(self.token, now=1002)
        self.assertEqual(self.store.result(req['id'], 'api', now=1002)['status'], 'not_authorized')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT active,point,token_hash,push_token FROM devices').fetchone(), (0, None, None, None))
            self.assertIsNone(db.execute('SELECT point FROM device_requests').fetchone()[0])

    def test_grant_rotation_blocks_old_token_and_pending_request(self):
        req = self.store.request('api', now=1001)
        renewed = self.store.authorize(self.device, 'api', 'My iPhone', 'iOS', now=1002)
        self.assertNotEqual(renewed['grant_id'], self.grant)
        with self.assertRaises(PermissionError):
            self.store.state(self.token, now=1002)
        with self.assertRaises(PermissionError):
            self.store.observe(renewed['device_token'], self.grant, 1, self.point, now=1002)
        self.assertEqual(self.store.result(req['id'], 'api', now=1002)['status'], 'not_authorized')

    def test_credentials_are_hashed_and_persisted(self):
        raw = Path(self.store.path).read_bytes()
        self.assertNotIn(self.token.encode(), raw)
        restored = m.DeviceStore(self.store.path)
        self.assertEqual(restored.state(self.token, now=1001)['grant_id'], self.grant)
        with self.assertRaises(PermissionError):
            restored.state('x' * 43)

    def test_sequence_order_and_timestamp_order_do_not_regress(self):
        self.observe(2)
        self.observe(1, point={**self.point, 'latitude': 31})
        self.observe(3, point={**self.point, 'timestamp': 999, 'latitude': 32})
        self.assertEqual(self.store.state(self.token, now=1001)['latest']['latitude'], 30.)

    def test_completed_request_is_idempotent_and_does_not_replace_result(self):
        req = self.store.request('api', now=1001)
        self.observe(1, 1002, req['id'], {**self.point, 'timestamp': 1002})
        self.observe(1, 1002, req['id'], {**self.point, 'timestamp': 1002, 'latitude': 31})
        self.assertEqual(self.store.result(req['id'], 'api', now=1002)['point']['latitude'], 30.)

    def test_consumed_sequence_cannot_finish_new_request(self):
        self.observe(2)
        req = self.store.request('api', max_age=0, now=1001)
        with self.assertRaises(ValueError):
            self.observe(2, 1002, req['id'], {**self.point, 'timestamp': 1002})
        self.assertEqual(self.store.result(req['id'], 'api', now=1002)['status'], 'pending')

    def test_timeout_does_not_revoke_grant(self):
        req = self.store.request('api', timeout=1, now=1001)
        self.assertEqual(self.store.result(req['id'], 'api', now=1002)['status'], 'timeout')
        with self.assertRaises(LookupError):
            self.observe(1, 1002, req['id'], {**self.point, 'timestamp': 1002})
        self.assertTrue(self.store.state(self.token, now=1002)['authorized'])

    def test_result_rechecks_requested_freshness_before_returning(self):
        req = self.store.request('api', max_age=1, now=1000)
        self.observe(1, 1000.5, req['id'], {**self.point, 'timestamp': 1000.5})
        self.assertEqual(self.store.result(req['id'], 'api', now=1001)['status'], 'ok')
        self.assertEqual(self.store.result(req['id'], 'api', now=1002)['status'], 'timeout')

    def test_failure_is_reported_without_waiting_or_changing_chat(self):
        req = self.store.request('api', now=1001)
        self.store.failure(self.token, self.grant, req['id'], 'permission_denied', now=1002)
        self.store.failure(self.token, self.grant, req['id'], 'permission_denied', now=1002)
        self.assertEqual(self.store.result(req['id'], 'api', now=1002), {'status': 'permission_denied'})
        self.assertTrue(self.store.state(self.token, now=1002)['authorized'])

    def test_requests_are_deduplicated_and_scoped_to_device_and_session(self):
        one = self.store.request('api', now=1001, dedupe='runtime-call')
        two = self.store.request('api', now=1001, dedupe='runtime-call')
        self.assertEqual(one['id'], two['id'])
        self.assertEqual(self.store.request('other', now=1001)['status'], 'not_authorized')
        self.assertEqual(self.store.result(one['id'], 'other', now=1001)['status'], 'not_authorized')
        other = self.store.authorize(str(uuid.uuid4()), 'other', 'Other iPhone', 'iOS', now=1001)
        with self.assertRaises(LookupError):
            self.store.failure(other['device_token'], other['grant_id'], one['id'], 'timeout', now=1001)

    def test_compression_resume_follows_same_conversation(self):
        resolver = lambda s: 'compressed' if s == 'api' else s
        req = self.store.request('compressed', resolve=resolver, now=1001)
        self.assertEqual(req['status'], 'pending')
        self.assertEqual(self.store.request('other', resolve=resolver, now=1001)['status'], 'not_authorized')

    def test_two_phones_require_explicit_selection(self):
        self.store.authorize(str(uuid.uuid4()), 'api', 'Second iPhone', 'iOS', now=1001)
        self.assertEqual(self.store.request('api', now=1002)['status'], 'device_selection_required')

    def test_watch_is_fallback_but_ipad_is_not_assumed_carried(self):
        self.store.revoke(self.device)
        self.store.authorize(str(uuid.uuid4()), 'api', 'iPad', 'iPadOS', now=1001)
        self.assertEqual(self.store.request('api', now=1002)['status'], 'not_authorized')
        self.store.authorize(str(uuid.uuid4()), 'api', 'Watch', 'watchOS', now=1001)
        self.assertEqual(self.store.request('api', now=1002)['status'], 'pending')

    def test_short_sharing_and_long_grants_do_not_revoke_each_other(self):
        lease = self.store.start(self.device, 'api', 900, now=1000)
        self.store.update(lease['id'], 1, self.point, now=1000)
        self.store.authorize(self.device, 'api', 'My iPhone', 'iOS', now=1001)
        self.assertIsNotNone(self.store.latest('api', now=1001))
        self.store.revoke(self.device)
        self.assertIsNotNone(self.store.latest('api', now=1001))

    def test_push_token_reregistration_survives_stale_invalidation(self):
        req = self.store.request('api', now=1001)
        self.store.register_push(self.token, self.grant, 'aabb', 'sandbox', now=1001)
        self.assertEqual(self.store.push_target(req['id'], now=1001)['push_token'], 'aabb')
        self.store.register_push(self.token, self.grant, 'ccdd', 'sandbox', now=1002)
        self.store.invalidate_push(self.device, self.grant, 'aabb', 1001, 1001)
        self.assertEqual(self.store.push_target(req['id'], now=1002)['push_token'], 'ccdd')
        self.store.invalidate_push(self.device, self.grant, 'ccdd', 1002, 1002)
        self.assertIsNone(self.store.push_target(req['id'], now=1002))
        self.assertTrue(self.store.state(self.token, now=1002)['authorized'])

    def test_invalid_bounds_and_points_are_rejected(self):
        for age, timeout in [(-1, 15), (121, 15), (True, 15), (0, 16), (0, float('nan'))]:
            with self.assertRaises(ValueError):
                self.store.request('api', age, timeout, now=1001)
        for change in [dict(latitude=91), dict(timestamp=0), dict(accuracy=-1), dict(longitude=float('inf'))]:
            with self.assertRaises(ValueError):
                self.observe(point={**self.point, **change})


class PluginContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.routes, self.hooks, self.tools, self.factories = {}, {}, {}, {}
        ctx = types.SimpleNamespace(
            register_hook=lambda name, fn: self.hooks.update({name: fn}),
            register_platform_handler=lambda name, fn: self.factories.update({name: fn}),
            register_tool=lambda **kw: self.tools.update({kw['name']: kw}))
        with patch.dict('sys.modules', {'hermes_constants': types.SimpleNamespace(get_hermes_home=lambda: Path(self.tmp.name))}):
            m.register(ctx)
        router = types.SimpleNamespace(**{method: (lambda path, fn, verb=method: self.routes.update({(verb, path): fn})) for method in ('add_get','add_post','add_put','add_delete')})
        self.web = types.SimpleNamespace(json_response=lambda value, status=200: {'status': status, 'body': value})
        db = types.SimpleNamespace(get_session=lambda s: object() if s in ('api', 'compressed') else None,
                                   resolve_resume_session_id=lambda s: 'compressed' if s == 'api' else s)
        adapter = types.SimpleNamespace(_ensure_session_db=lambda: db, _expected_api_key=lambda: 'owner',
            _check_auth=lambda req: None if req.headers.get('Authorization') == 'Bearer owner' else self.web.json_response({'error':'unauthorized'}, status=401))
        with patch.dict('sys.modules', {'aiohttp': types.SimpleNamespace(web=self.web)}):
            self.factories['api_server'](types.SimpleNamespace(router=router), adapter)
        self.store = m.DeviceStore(Path(self.tmp.name) / 'plugin-data/ollo-location/latest.sqlite')
        self.device = str(uuid.uuid4())
        self.auth = self.store.authorize(self.device, 'api', 'My iPhone', 'iOS')
        self.prefix = '/v1/ollo/location'

    def invoke(self, args, **kwargs):
        return asyncio.run(self.tools['get_user_location']['handler'](args, **kwargs))

    def call(self, verb, suffix, token, body=None, match=None, route=None):
        async def parsed(): return body
        req = types.SimpleNamespace(method=verb.upper(), path=self.prefix+suffix,
            headers={'Authorization': 'Bearer '+token}, match_info=match or {},
            content_length=len(json.dumps(body)) if body is not None else 0, json=parsed)
        fn = self.routes[('add_'+verb, self.prefix+(route or suffix))]
        return asyncio.run(fn(req))

    def test_device_bearer_does_not_need_or_gain_owner_privileges(self):
        self.assertEqual(self.call('get','/device/state',self.auth['device_token'])['status'], 200)
        self.assertEqual(self.call('get','',self.auth['device_token'])['status'], 401)
        self.assertEqual(self.call('get','/device/state','owner')['status'], 403)
        self.assertEqual(self.call('get','', 'owner')['body']['version'], 1)
        self.assertTrue(self.call('get','', 'owner')['body']['on_demand'])

    def test_registration_requires_owner_and_existing_session(self):
        body = dict(device_id=str(uuid.uuid4()), session_id='wrong', display_name='Phone', platform='iOS')
        self.assertEqual(self.call('post','/devices','owner',body)['status'], 404)
        body['session_id'] = 'api'
        self.assertEqual(self.call('post','/devices',self.auth['device_token'],body)['status'], 401)
        response = self.call('post','/devices','owner',body)
        self.assertEqual(response['status'],200)
        self.assertIn('grant_id',response['body'])

    def test_observation_and_failure_json_contract(self):
        req = self.store.request('api',max_age=0)
        body = dict(grant_id=self.auth['grant_id'], sequence=1,
                    point=dict(latitude=30.,longitude=120.,accuracy=10.,timestamp=time.time()),request_id=req['id'])
        self.assertEqual(self.call('put','/device/observation',self.auth['device_token'],body)['body'],{'accepted':True})
        self.assertEqual(self.store.result(req['id'],'api')['status'],'ok')
        state = self.call('get','/device/state',self.auth['device_token'])['body']
        self.assertEqual(state['grant_id'],self.auth['grant_id'])
        self.assertEqual(state['pending_requests'],[])

    def test_hook_advertises_tool_without_coordinates_and_blocks_other_platform(self):
        self.store.observe(self.auth['device_token'],self.auth['grant_id'],1,dict(latitude=30.,longitude=120.,accuracy=10.,timestamp=time.time()))
        hook = self.hooks['pre_llm_call']
        result = hook(platform='api_server',session_id='compressed')
        self.assertIn('get_user_location',result['context'])
        self.assertNotIn('latitude',result['context'])
        self.assertIsNone(hook(platform='telegram',session_id='api'))
        tool = self.invoke
        self.assertEqual(json.loads(tool({},session_id='api'))['status'],'not_authorized')
        self.assertEqual(json.loads(tool({},session_id='compressed'))['status'],'ok')
        self.assertEqual(json.loads(tool({'session_id':'compressed'},session_id='other'))['status'],'not_authorized')
        hook(platform='telegram',session_id='compressed')
        self.assertEqual(json.loads(tool({},session_id='compressed'))['status'],'not_authorized')

    def test_waiting_tool_does_not_block_observation_thread(self):
        self.hooks['pre_llm_call'](platform='api_server',session_id='api')
        output = []
        worker = threading.Thread(target=lambda: output.append(json.loads(self.invoke({'timeout_seconds':2,'max_age_seconds':0},session_id='api'))))
        worker.start()
        deadline = time.monotonic()+1
        requests = []
        while time.monotonic()<deadline:
            requests = self.store.state(self.auth['device_token'])['pending_requests']
            if requests: break
            time.sleep(.01)
        self.assertTrue(requests)
        self.store.observe(self.auth['device_token'],self.auth['grant_id'],1,dict(latitude=30.,longitude=120.,accuracy=10.,timestamp=time.time()),requests[0]['id'])
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(output[0]['status'],'ok')

    def test_async_handler_times_out_without_blocking_event_loop(self):
        self.hooks['pre_llm_call'](platform='api_server',session_id='api')
        async def invoke():
            task = asyncio.create_task(self.tools['get_user_location']['handler']({'timeout_seconds':.05},session_id='api'))
            await asyncio.sleep(.01)
            self.assertFalse(task.done())
            return json.loads(await task)
        self.assertEqual(asyncio.run(invoke())['status'],'timeout')

if __name__ == '__main__':
    unittest.main()

# Real aiohttp contract tests run in the documented isolated test environment.
try:
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
except ImportError:
    web = None


@unittest.skipIf(web is None, 'Install aiohttp to run the real HTTP contract tests')
class DeviceHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hooks, self.tools, factories = {}, {}, {}
        ctx = types.SimpleNamespace(register_hook=lambda n, f: self.hooks.update({n: f}),
                                    register_platform_handler=lambda n, f: factories.update({n:f}),
                                    register_tool=lambda **kw: self.tools.update({kw['name']:kw}))
        with patch.dict('sys.modules', {'hermes_constants': types.SimpleNamespace(get_hermes_home=lambda:Path(self.tmp.name))}):
            m.register(ctx)
        app = web.Application()
        db = types.SimpleNamespace(get_session=lambda s: object() if s == 'api' else None, resolve_resume_session_id=lambda s:s)
        adapter = types.SimpleNamespace(_ensure_session_db=lambda:db, _expected_api_key=lambda:'owner',
            _check_auth=lambda req: None if req.headers.get('Authorization') == 'Bearer owner' else web.json_response({'error':'Unauthorized'},status=401))
        factories['api_server'](app,adapter)
        self.client = TestClient(TestServer(app))
        self.addAsyncCleanup(self.client.close)
        try:
            await self.client.start_server()
        except PermissionError:
            self.skipTest('Sandbox forbids loopback sockets; router contracts run in test_compatibility.py')
        self.prefix = '/v1/ollo/location'
        self.owner = {'Authorization':'Bearer owner'}
        self.device = str(uuid.uuid4())
        response = await self.client.post(self.prefix+'/devices',headers=self.owner,
            json=dict(device_id=self.device,session_id='api',display_name='Test iPhone',platform='iOS'))
        self.assertEqual(response.status,200)
        self.auth = await response.json()
        self.headers = {'Authorization':'Bearer '+self.auth['device_token']}
        self.store = m.DeviceStore(Path(self.tmp.name)/'plugin-data/ollo-location/latest.sqlite')

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def test_real_http_pending_observation_and_tool_result(self):
        self.hooks['pre_llm_call'](platform='api_server',session_id='api')
        tool_task = asyncio.create_task(self.tools['get_user_location']['handler'](
            {'max_age_seconds':0,'timeout_seconds':2},session_id='api'))
        pending = []
        for _ in range(50):
            response = await self.client.get(self.prefix+'/device/state',headers=self.headers)
            state = await response.json()
            pending = state['pending_requests']
            if pending: break
            await asyncio.sleep(.01)
        self.assertTrue(pending)
        point = dict(latitude=30.,longitude=120.,accuracy=25.,timestamp=time.time())
        response = await self.client.put(self.prefix+'/device/observation',headers=self.headers,
            json=dict(grant_id=self.auth['grant_id'],sequence=1,point=point,request_id=pending[0]['id']))
        self.assertEqual(response.status,200)
        self.assertEqual(await response.json(),{'accepted':True})
        result = json.loads(await tool_task)
        self.assertEqual(result['status'],'ok')
        self.assertEqual(result['device_id'],self.device)
        self.assertEqual(result['point'],point)
        self.assertFalse(result['cached'])

    async def test_real_http_auth_failure_revoke_and_late_result(self):
        response = await self.client.get(self.prefix,headers=self.headers)
        self.assertEqual(response.status,401)
        response = await self.client.get(self.prefix+'/device/state',headers=self.owner)
        self.assertEqual(response.status,403)
        req = self.store.request('api')
        response = await self.client.post(self.prefix+'/device/results',headers=self.headers,
            json=dict(grant_id=self.auth['grant_id'],request_id=req['id'],status='background_unavailable'))
        self.assertEqual(response.status,200)
        self.assertEqual(self.store.result(req['id'],'api')['status'],'background_unavailable')
        response = await self.client.post(self.prefix+'/device/push',headers=self.headers,
            json=dict(grant_id=self.auth['grant_id'],apns_token='aabb',environment='sandbox'))
        self.assertEqual(response.status,200)
        response = await self.client.delete(self.prefix+'/devices/'+self.device,headers=self.owner)
        self.assertEqual(response.status,200)
        response = await self.client.get(self.prefix+'/device/state',headers=self.headers)
        self.assertEqual(response.status,403)
        response = await self.client.put(self.prefix+'/device/observation',headers=self.headers,
            json=dict(grant_id=self.auth['grant_id'],sequence=1,point=dict(latitude=30.,longitude=120.,accuracy=10.,timestamp=time.time())))
        self.assertEqual(response.status,403)

    async def test_real_http_timeout_is_410_without_revoking_device(self):
        req = self.store.request('api',timeout=.01)
        await asyncio.sleep(.03)
        response = await self.client.post(self.prefix+'/device/results',headers=self.headers,
            json=dict(grant_id=self.auth['grant_id'],request_id=req['id'],status='timeout'))
        self.assertEqual(response.status,410)
        response = await self.client.get(self.prefix+'/device/state',headers=self.headers)
        self.assertEqual(response.status,200)
