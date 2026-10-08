"""Every legacy/new route reaches the same authorization and durable state."""
import time
import uuid

import pytest

from conftest import load_plugin_module


@pytest.mark.asyncio
async def test_location_aliases_share_grants_requests_and_lease_lifecycle(runtime, isolated_home):
    module = load_plugin_module('location')
    legacy = isolated_home / 'plugin-data/hermes-plus-location'
    old_store = module.DeviceStore(legacy / 'latest.sqlite')
    device = str(uuid.uuid4())
    auth = old_store.authorize(device, 'root', 'Phone', 'iOS')
    with runtime(('location',)) as r:
        r.db.create_session('root', 'api_server')
        store = module.DeviceStore(isolated_home / 'plugin-data/ollo-location/latest.sqlite')
        token = auth['device_token']
        for namespace in ('ollo', 'hermes-plus'):
            base = '/v1/' + namespace + '/location'
            assert (await r.client.request('GET', base, token=token))[0] == 401
            assert (await r.client.request('GET', base + '/device/state'))[0] == 403
            assert (await r.client.request('GET', base))[1]['on_demand'] is True
            assert (await r.client.request('GET', base + '/device/state', token=token))[1]['grant_id'] == auth['grant_id']
            request = store.request('root', max_age=0)
            assert (await r.client.request('POST', base + '/device/results', token=token,
                body={'grant_id':auth['grant_id'],'request_id':request['id'],'status':'unavailable'}))[0] == 200
            assert store.result(request['id'], 'root')['status'] == 'unavailable'
            assert (await r.client.request('POST', base + '/device/push', token=token,
                body={'grant_id':auth['grant_id'],'apns_token':'aabb','environment':'sandbox'}))[0] == 200
            point = {'latitude':30., 'longitude':120., 'accuracy':5., 'timestamp':time.time()}
            assert (await r.client.request('PUT', base + '/device/observation', token=token,
                body={'grant_id':auth['grant_id'],'sequence':1 if namespace == 'ollo' else 2,'point':point}))[0] == 200
            other = '/v1/' + ('hermes-plus' if namespace == 'ollo' else 'ollo') + '/location'
            assert (await r.client.request('GET', other + '/device/state', token=token))[1]['latest'] == point
            status, lease = await r.client.request('POST', base, body={'device_id':device,'session_id':'root','duration':120})
            assert status == 200
            assert (await r.client.request('PUT', other + '/' + lease['id'], body={'sequence':1,'point':point}))[0] == 200
            assert (await r.client.request('DELETE', base + '/' + lease['id']))[0] == 200
            assert (await r.client.request('PUT', other + '/' + lease['id'], body={'sequence':2,'point':point}))[0] == 410
        assert not legacy.exists()
        assert (await r.client.request('DELETE', '/v1/hermes-plus/location/devices/' + device))[0] == 200
        assert (await r.client.request('GET', '/v1/ollo/location/device/state', token=token))[0] == 403
        for namespace in ('ollo', 'hermes-plus'):
            status, new = await r.client.request('POST', '/v1/' + namespace + '/location/devices',
                body={'device_id':device,'session_id':'root','display_name':'Phone','platform':'iOS'})
            assert status == 200 and new['grant_id'] != auth['grant_id']
