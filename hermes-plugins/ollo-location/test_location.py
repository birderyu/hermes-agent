import importlib.util
from pathlib import Path
import tempfile
import unittest
import uuid
import time
import types
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('location_plugin',Path(__file__).with_name('__init__.py'))
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

class LocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=m.LocationStore(Path(self.tmp.name)/'location.sqlite')
        self.device=str(uuid.uuid4())
        self.lease=self.store.start(self.device,'session',900,now=1000)['id']
        self.point={'latitude':30.,'longitude':120.,'accuracy':10.,'timestamp':1000.}
    def test_latest_only_ordering_and_scope(self):
        self.store.update(self.lease,2,self.point,now=1000)
        self.store.update(self.lease,1,{**self.point,'latitude':31.},now=1000)
        self.assertEqual(self.store.latest('session',now=1000)['latitude'],30.)
        self.assertIsNone(self.store.latest('other',now=1000))
        self.assertEqual(self.store.latest('compressed',resolve=lambda s:'compressed',now=1000)['latitude'],30.)
    def test_stop_blocks_late_upload_and_clears_point(self):
        self.store.update(self.lease,1,self.point,now=1000)
        self.store.stop(self.lease)
        with self.assertRaises(LookupError): self.store.update(self.lease,2,self.point,now=1000)
        self.assertIsNone(self.store.latest('session',now=1000))
    def test_expiry_and_stale_location(self):
        self.store.update(self.lease,1,self.point,now=1000)
        self.assertIsNone(self.store.latest('session',now=1121))
        with self.assertRaises(LookupError): self.store.update(self.lease,2,{**self.point,'timestamp':1901},now=1901)
    def test_new_lease_invalidates_old_and_old_stop_does_not_stop_new(self):
        new=self.store.start(self.device,'session',900,now=1001)['id']
        with self.assertRaises(LookupError): self.store.update(self.lease,1,self.point,now=1001)
        self.store.stop(self.lease)
        self.store.update(new,1,self.point,now=1001)
        self.assertIsNotNone(self.store.latest('session',now=1001))
    def test_rejects_bad_coordinates_and_timestamps(self):
        for changes in [{'latitude':91},{'longitude':float('nan')},{'accuracy':-1},{'timestamp':800},{'timestamp':1100}]:
            with self.assertRaises(ValueError): self.store.update(self.lease,1,{**self.point,**changes},now=1000)
    def test_persisted_lease_survives_restart_but_cannot_extend_expiry(self):
        self.store.update(self.lease,1,self.point,now=1000)
        restored=m.LocationStore(self.store.path)
        self.assertIsNotNone(restored.latest('session',now=1001))
        self.assertIsNone(restored.latest('session',now=1901))

    def test_hook_injects_only_matching_api_conversation_and_revokes_context(self):
        hooks={}; factories={}
        ctx=types.SimpleNamespace(register_hook=lambda name,fn:hooks.update({name:fn}),
                                  register_platform_handler=lambda name,fn:factories.update({name:fn}))
        with patch.dict('sys.modules', {'hermes_constants':types.SimpleNamespace(get_hermes_home=lambda:Path(self.tmp.name))}):
            m.register(ctx)
        router=types.SimpleNamespace(**{method:lambda *args:None for method in ['add_get','add_post','add_put','add_delete']})
        adapter=types.SimpleNamespace(_ensure_session_db=lambda:types.SimpleNamespace(resolve_resume_session_id=lambda s:s))
        with patch.dict('sys.modules', {'aiohttp':types.SimpleNamespace(web=None)}):
            factories['api_server'](types.SimpleNamespace(router=router),adapter)
        store=m.LocationStore(Path(self.tmp.name)/'plugin-data/ollo-location/latest.sqlite')
        lease=store.start(self.device,'api-test',900)['id']
        store.update(lease,1,{**self.point,'timestamp':time.time()})
        hook=hooks['pre_llm_call']
        self.assertIn('latitude',hook(platform='api_server',session_id='api-test')['context'])
        self.assertNotIn('latitude',hook(platform='api_server',session_id='other')['context'])
        self.assertIsNone(hook(platform='telegram',session_id='api-test'))
        store.stop(lease)
        self.assertNotIn('latitude',hook(platform='api_server',session_id='api-test')['context'])

if __name__=='__main__': unittest.main()
