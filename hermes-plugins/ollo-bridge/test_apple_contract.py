import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytest.register_assert_rewrite('ollo_bridge_test_support')
from ollo_bridge_test_support import example

pytest_plugins = ['ollo_bridge_test_support']


@pytest.mark.asyncio
async def test_missing_originals_skips_whole_dependency_group(service):
    backend = service.apple_module.AppleBackend('/not-executed')
    backend.call = AsyncMock()
    permit = example('claimed-group')['operation']
    permit['originals'].pop('fixture-reminder-2')
    assert await backend.group(permit) == [
        {'index': i, 'status': 'skipped', 'reason': '缺少生成时原值，未执行'} for i in [0, 1]]
    backend.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_is_one_native_call_with_generation_originals(service):
    backend = service.apple_module.AppleBackend('/not-executed')
    backend.call = AsyncMock(return_value={'status': 'skipped', 'reason': '确认前事项已变化'})
    permit = example('claimed-group')['operation']
    before = copy.deepcopy(permit)
    result = await backend.group(permit)
    assert [r['status'] for r in result] == ['skipped', 'skipped']
    assert permit == before
    request = backend.call.call_args.args[0]
    assert request['changes'] == permit['group']['changes']
    assert request['expected'] == {key: permit['originals'][key] for key in ('fixture-reminder-1', 'fixture-reminder-2')}
    backend.call.assert_awaited_once()


@pytest.mark.asyncio
async def test_apple_read_does_not_fake_deleted_items_on_bad_set(service):
    backend = service.apple_module.AppleBackend('/not-executed')
    backend.call = AsyncMock(return_value={'items': []})
    with pytest.raises(service.protocol.BridgeError):
        await backend.items(['a'])
    backend.call.return_value = {'items': [{'id': 'a', 'exists': False, 'completed': False}]}
    assert await backend.items(['a']) == backend.call.return_value


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['timeout', 'pipe', 'invalid_receipt', 'not_executed'])
async def test_native_process_failure_semantics(service, monkeypatch, failure):
    from types import SimpleNamespace
    process = SimpleNamespace(returncode=None, kill=lambda: None, wait=AsyncMock(return_value=1))
    if failure == 'timeout':
        communicate = AsyncMock(side_effect=asyncio.TimeoutError())
    elif failure == 'pipe':
        communicate = AsyncMock(side_effect=BrokenPipeError())
    else:
        process.returncode = 1
        raw = b'{bad' if failure == 'invalid_receipt' else b'{"error":"permission","outcome":"not_executed"}'
        communicate = AsyncMock(return_value=(raw, b'private diagnostic must be ignored'))
    process.communicate = communicate
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    backend = service.apple_module.AppleBackend('/synthetic/helper')
    expected = service.apple_module.NotExecuted if failure == 'not_executed' else service.apple_module.UnknownWrite
    with pytest.raises(expected):
        await backend.capture(example('capture')['request'])
    assert asyncio.create_subprocess_exec.call_args.args == ('/synthetic/helper',)


def test_contract_examples_and_nonfinite_utf8_dates(service):
    names = {'report-delivery': 'BridgeReportDelivery', 'report': 'BridgeReport', 'report-page': 'BridgeReportPage',
             'report-delivery-failure': 'BridgeReportDelivery', 'report-failure': 'BridgeReport',
             'report-receipt-failure': 'BridgeReportReceipt',
             'context': 'BridgeContext', 'context-share': 'BridgeContext', 'location-request': 'BridgeLocationRequest',
             'location-pending': 'BridgeLocationPending', 'location-success': 'BridgeLocationSuccess',
             'wake': 'BridgeWakeRequest', 'capture': 'BridgeCaptureRequest', 'items': 'BridgeItemsRequest',
             'interpret': 'BridgeInterpretRequest', 'confirmation': 'BridgeConfirmation',
             'claimed-group': 'BridgeOperationClaimResponse', 'claimed-capture': 'BridgeOperationClaimResponse',
             'observed-unknown': 'BridgeOperationClaimResponse', 'finish-group-success': 'BridgeOperationFinish',
             'capture-lookup-request': 'BridgeCaptureLookupRequest', 'capture-lookup-found': 'BridgeCaptureLookupResponse',
             'capture-lookup-absent': 'BridgeCaptureLookupResponse', 'capture-lookup-unavailable': 'BridgeCaptureLookupResponse',
             'capture-lookup-in-progress': 'BridgeCaptureLookupResponse'}
    for name, kind in names.items():
        service.protocol.validate('B_' + kind, example(name))
    for text in ('{"x":NaN}', '{"x":1,"x":2}', '{"x":Infinity}'):
        with pytest.raises(service.protocol.BridgeError):
            service.protocol.loads(text)
    with pytest.raises(service.protocol.BridgeError):
        service.protocol.validate('B_BridgeLocationRequest', {'timeout_seconds': float('inf')})
    payload = example('capture')
    payload['request']['text'] = '中' * 1400
    with pytest.raises(service.protocol.BridgeError):
        service.protocol.validate('B_BridgeCaptureRequest', payload)
    payload['request']['text'] = 'valid'
    payload['request']['created_at'] = '2026-02-30T00:00:00+08:00'
    with pytest.raises(service.protocol.BridgeError):
        service.protocol.validate('B_BridgeCaptureRequest', payload)


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['x' * 513, 'x' * 4096, '中' * 1365 + 'a', '😀' * 1024])
async def test_capture_and_items_preserve_full_text_at_utf8_limit(service, text):
    payload = example('capture')
    payload['request']['text'] = text
    service.protocol.validate('B_BridgeCaptureRequest', payload)
    backend = service.apple_module.AppleBackend('/synthetic/helper')
    backend.call = AsyncMock(return_value={'id': 'captured'})
    assert await backend.capture(payload['request']) == 'captured'
    assert backend.call.call_args.args[0]['request']['text'] == text
    row = {'id': 'captured', 'exists': True, 'completed': False, 'title': text}
    backend.call.return_value = {'items': [row]}
    assert await backend.items(['captured']) == {'items': [row]}


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['x' * 4097, '中' * 1365 + 'ab', '😀' * 1024 + 'a'])
async def test_capture_and_items_reject_over_limit_instead_of_truncating(service, text):
    payload = example('capture')
    payload['request']['text'] = text
    with pytest.raises(service.protocol.BridgeError):
        service.protocol.validate('B_BridgeCaptureRequest', payload)
    backend = service.apple_module.AppleBackend('/synthetic/helper')
    backend.call = AsyncMock(return_value={'items': [
        {'id': 'captured', 'exists': True, 'completed': False, 'title': text}]})
    with pytest.raises(service.protocol.BridgeError):
        await backend.items(['captured'])


@pytest.mark.asyncio
async def test_maximum_unicode_item_ids_fit_native_request_budget(service, monkeypatch):
    from types import SimpleNamespace
    ids = ['😀' * 509 + f'{i:03d}' for i in range(100)]
    result = {'items': [{'id': key, 'exists': False, 'completed': False} for key in ids]}
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(
        json.dumps(result, ensure_ascii=False).encode(), b'')), wait=AsyncMock(return_value=0))
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    backend = service.apple_module.AppleBackend('/synthetic/helper')
    assert await backend.items(ids) == result
    wire = process.communicate.call_args.args[0]
    assert len(wire) <= 256000 and json.loads(wire) == {'method': 'items', 'ids': ids, 'apple': backend.apple}


def test_offline_schema_references_and_runtime_roots_resolve(service):
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    def refs(value):
        if isinstance(value, dict):
            if '$ref' in value:
                yield value['$ref']
            for child in value.values():
                yield from refs(child)
        elif isinstance(value, list):
            for child in value:
                yield from refs(child)

    # Public entrypoints invoked directly by code need to exist even when no
    # bridge schema refers to them. check_schema alone does not resolve $refs.
    documents = [service.protocol._SCHEMA]
    documents.extend(service.protocol.schema(name) for name in (
        'P_GTDResult', 'P_GTDSuggestion', 'P_OlloCards', 'P_OlloReportCardsInput', 'P_DayPlan'))
    for document in documents:
        Draft202012Validator.check_schema(document)
        uri = 'urn:ollo:bridge:offline-schema'
        resolver = Registry().with_resource(uri, Resource.from_contents(document)).resolver(uri)
        for ref in refs(document):
            assert ref.startswith('#/$defs/'), f'Non-local schema reference: {ref}'
            assert isinstance(resolver.lookup(ref).contents, (dict, bool)), ref


@pytest.fixture(scope='module')
def native_executable(tmp_path_factory):
    import subprocess
    tmp_path = tmp_path_factory.mktemp('ollo-eventkit')
    executable = tmp_path / 'ollo-eventkit'
    source = Path(__file__).parent / 'native/EventKitBridge.swift'
    # xcrun is the macOS developer-tools entry point, not a PATH guess.
    # Exercise the optimized build while keeping the older assert-based contracts active.
    subprocess.run(['/usr/bin/xcrun', 'swiftc', '-O', '-assert-config', 'Debug', '-module-cache-path', str(tmp_path / 'modules'),
                    str(source), '-Xlinker', '-sectcreate', '-Xlinker', '__TEXT', '-Xlinker', '__info_plist',
                    '-Xlinker', str(source.with_name('Info.plist')), '-o', str(executable)], check=True, capture_output=True, timeout=90)
    return executable


@pytest.mark.platforms('macos')
def test_native_pure_commit_and_originals_contract(native_executable):
    """--self-test never creates EKEventStore or calls permission APIs."""
    import subprocess
    result = subprocess.run([str(native_executable), '--self-test'], check=True, capture_output=True, timeout=10)
    assert json.loads(result.stdout) == {'ok': True}


@pytest.mark.platforms('macos')
def test_native_watchdog_exits_without_parent_timeout_or_eventkit_access(native_executable):
    import signal
    import subprocess
    import time
    # Leave stdin open: the ordinary helper blocks before constructing EventStore.
    # wait() does not close stdin or send any signal. Only the helper's own alarm
    # can terminate this call; the parent timeout is a failing-test safeguard.
    started = time.monotonic()
    with subprocess.Popen([str(native_executable)], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        try:
            assert process.wait(timeout=12) == -signal.SIGALRM
            # Allow process startup / test-runner scheduling overhead around 7s.
            assert 6 <= time.monotonic() - started < 9
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)
