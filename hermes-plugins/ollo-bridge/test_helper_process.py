import asyncio
import json
from unittest.mock import AsyncMock

import pytest

pytest.register_assert_rewrite('ollo_bridge_test_support')
from ollo_bridge_test_support import example, KEY, Operations

pytest_plugins = ['ollo_bridge_test_support']


class Process:
    def __init__(self, response=None, failure=None):
        self.returncode = None
        self.response, self.failure = response, failure
        self.killed, self.waited = False, False
        self.requests = []

    async def communicate(self, data):
        self.requests.append(json.loads(data))
        if self.failure:
            raise self.failure
        self.returncode = 0
        return json.dumps(self.response).encode(), b''

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        self.waited = True
        return self.returncode


@pytest.mark.asyncio
async def test_timeout_kills_reaps_and_drops_active_request(service, monkeypatch):
    process = Process(failure=asyncio.TimeoutError())
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    backend = service.apple_module.AppleBackend('/synthetic/helper')
    payload = example('capture')
    with pytest.raises(service.apple_module.UnknownWrite):
        await backend.capture(payload['request'])
    assert process.killed and process.waited
    assert not backend.processes.is_active(payload['request']['request_id'])
    # No execution history blocks the gateway-authorized reconciliation read.
    process = Process(response=example('capture-lookup-absent'))
    asyncio.create_subprocess_exec.return_value = process
    assert await backend.capture_lookup(payload['request']['request_id']) == example('capture-lookup-absent')
    assert process.requests == [{'method': 'capture_lookup', 'request_id': payload['request']['request_id'], 'apple': backend.apple}]


@pytest.mark.asyncio
async def test_cancelled_spawn_stays_in_progress_until_late_child_is_reaped(service, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    process = Process(response={'id': 'unused'})
    async def launch(*args, **kwargs):
        entered.set()
        await release.wait()
        return process
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', launch)
    backend = service.apple_module.AppleBackend('/synthetic/helper')
    payload = example('capture')
    request_id = payload['request']['request_id']
    task = asyncio.create_task(backend.capture(payload['request']))
    await entered.wait()
    assert await backend.capture_lookup(request_id) == example('capture-lookup-in-progress')
    task.cancel()
    with pytest.raises(service.apple_module.UnknownWrite):
        await task
    # Cancellation has returned, but the retained reaper still owns the spawn.
    assert await backend.capture_lookup(request_id) == example('capture-lookup-in-progress')
    release.set()
    await asyncio.gather(*backend.processes.cleanup)
    assert process.killed and process.waited and not process.requests
    assert not backend.processes.is_active(request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['found', 'absent', 'unavailable'])
async def test_lookup_wire_results(service, monkeypatch, kind):
    result = example('capture-lookup-' + kind)
    process = Process(response=result)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    backend = service.apple_module.AppleBackend('/synthetic/helper')
    request_id = example('capture')['request']['request_id']
    assert await backend.capture_lookup(request_id) == result
    assert process.requests == [{'method': 'capture_lookup', 'request_id': request_id, 'apple': backend.apple}]


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['permission', 'timeout', 'invalid'])
async def test_incomplete_lookup_never_returns_absent(service, monkeypatch, failure):
    process = Process(response={'error': 'private', 'outcome': 'not_executed'})
    if failure == 'timeout':
        process.failure = asyncio.TimeoutError()
    elif failure == 'invalid':
        process.response = {'status': 'absent'}  # No completed-scan timestamp.
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    backend = service.apple_module.AppleBackend('/synthetic/helper')
    assert await backend.capture_lookup(example('capture')['request']['request_id']) == {
        'status': 'unavailable', 'reason': 'reminder_read_unavailable'}
    if failure == 'timeout':
        assert process.killed and process.waited


@pytest.mark.asyncio
async def test_capture_carries_request_identity_without_note_or_title_marker(service, monkeypatch):
    process = Process(response={'id': 'captured'})
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    backend = service.apple_module.AppleBackend('/synthetic/helper')
    payload = example('capture')
    assert await backend.capture(payload['request']) == 'captured'
    assert process.requests == [{'method': 'capture', 'request': payload['request'], 'apple': backend.apple}]
    # The native --self-test verifies URL construction, original title and notes.


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['found', 'absent', 'unavailable', 'permission'])
async def test_lookup_http_after_restart_queries_without_execution_history(bridge, gateway, monkeypatch, kind):
    # A newly registered plugin has no capture/attempt history. Gateway already
    # checked expiry + 30s; no extra claim/permit call is part of lookup.
    result = (example('capture-lookup-' + kind) if kind != 'permission'
              else {'error': 'permission', 'outcome': 'not_executed'})
    process = Process(response=result)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    payload = example('capture-lookup-request')
    status, body = await bridge.client.request('POST', '/v1/ollo-bridge/schedule/capture/lookup', token=KEY, body=payload)
    assert status == 200
    assert body == (result if kind != 'permission' else {'status': 'unavailable', 'reason': 'reminder_read_unavailable'})
    assert process.requests == [{'method': 'capture_lookup', 'request_id': payload['request_id'],
                                 'apple': bridge.plugin.read_config(bridge.home).apple}]
    assert process.waited and gateway.calls == []


@pytest.mark.asyncio
async def test_lookup_http_in_progress_is_scoped_to_request_and_ends_after_reap(bridge, gateway, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    process = Process(response={'id': 'captured'})
    communicate = process.communicate
    async def hold_capture(data):
        entered.set()
        await release.wait()
        return await communicate(data)
    process.communicate = hold_capture
    lookup_process = Process(response=example('capture-lookup-absent'))
    found = Process(response=example('capture-lookup-found'))
    launch = AsyncMock(side_effect=[process, lookup_process, found])
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', launch)
    payload = example('capture')
    ops = Operations(gateway)
    ops.add(payload['operation_id'], example('claimed-capture')['operation'])
    capture = asyncio.create_task(bridge.client.request('POST', '/v1/ollo-bridge/schedule/capture', token=KEY, body=payload))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        lookup = example('capture-lookup-request')
        # Even a different attempt for this request must wait for the helper.
        lookup['attempt'] = '60000000-0000-4000-8000-000000000009'
        assert await bridge.client.request('POST', '/v1/ollo-bridge/schedule/capture/lookup', token=KEY, body=lookup) == (
            200, example('capture-lookup-in-progress'))
        launch.assert_awaited_once()
        other = {**lookup, 'request_id': '70000000-0000-4000-8000-000000000009'}
        assert await bridge.client.request('POST', '/v1/ollo-bridge/schedule/capture/lookup', token=KEY, body=other) == (
            200, example('capture-lookup-absent'))
    finally:
        release.set()
        result = await capture
    assert result == (200, {'item_id': 'captured'}) and process.waited
    assert await bridge.client.request('POST', '/v1/ollo-bridge/schedule/capture/lookup', token=KEY, body=lookup) == (
        200, example('capture-lookup-found'))
    assert launch.await_count == 3


@pytest.mark.asyncio
async def test_lookup_http_auth_schema_and_four_kib_limit(bridge, gateway, monkeypatch):
    launch = AsyncMock()
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', launch)
    path, payload = '/v1/ollo-bridge/schedule/capture/lookup', example('capture-lookup-request')
    for token in (None, 'wrong', 'isolated-owner-key'):
        status, body = await bridge.client.request('POST', path, token=token, body=payload)
        assert status == 401 and body['code'] == 'unauthorized'
    for invalid in ({**payload, 'attempt': 1}, {**payload, 'expires_at': '2030-01-01T00:00:00Z'}):
        status, body = await bridge.client.request('POST', path, token=KEY, body=invalid)
        assert status == 400 and body['code'] == 'invalid_request'
    status, body = await bridge.client.request('POST', path, token=KEY, body={**payload, 'padding': 'x' * 4096})
    assert status == 413 and body['code'] == 'payload_too_large'
    launch.assert_not_awaited()
    assert gateway.calls == []
