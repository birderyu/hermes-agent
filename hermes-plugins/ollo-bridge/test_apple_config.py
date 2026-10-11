import asyncio
from importlib import import_module
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest_plugins = ['ollo_bridge_test_support']


def test_defaults_and_partial_bijections(service):
    module = import_module(service.base + '.apple_config')
    result = module.names({'lists': {'收件箱': 'Ollo 测试·收件箱'}, 'calendars': {'工作': 'Ollo 测试·工作'}})
    assert result['lists'] == {'收件箱': 'Ollo 测试·收件箱', '工作': '工作', '生活': '生活', '家庭': '家庭', '等待': '等待', '长期': '长期'}
    assert result['calendars'] == {'工作': 'Ollo 测试·工作', '生活': '生活', '家庭': '家庭'}


@pytest.mark.asyncio
@pytest.mark.parametrize('apple', [
    {'lists': {'工作': '生活'}}, {'calendars': {'工作': '生活'}},
    {'lists': {'工作': '同名', '家庭': '同名'}}, {'lists': {'未知': '测试'}},
    {'lists': {'工作': ''}}, {'lists': {'工作': 1}}, {'unexpected': {}},
    {'lists': {'工作': 'caf\u00e9', '家庭': 'cafe\u0301'}},
])
async def test_invalid_mapping_disables_startup(runtime, bridge_config, isolated_home, apple):
    from gateway.config import PlatformConfig
    from gateway.platform_registry import platform_registry
    path = isolated_home / 'ollo/bridge.json'
    path.write_text(json.dumps({**bridge_config, 'apple': apple}), encoding='utf-8')
    with runtime(('bridge',)):
        adapter = platform_registry.get('ollo').adapter_factory(PlatformConfig())
        assert await adapter.connect() is False


@pytest.mark.parametrize('scope', [
    {'agenda': {'lists': []}}, {'inbox': {'lists': ['unknown']}},
    {'review': {'calendars': ['工作', '工作']}}, {'other': {}},
    {'agenda': {'max_items': 501}},
])
def test_invalid_scopes_are_rejected(service, scope):
    module = import_module(service.base + '.apple_config')
    with pytest.raises(ValueError):
        module.scopes(scope)


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['collect', 'items', 'capture', 'apply', 'capture_lookup'])
async def test_native_request_carries_operator_mapping_not_caller_override(service, monkeypatch, method):
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'{}', b'')), wait=AsyncMock(return_value=0))
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    backend = service.apple_module.AppleBackend('/synthetic/helper', {'lists': {'收件箱': 'Ollo 测试·收件箱'}})
    await backend.call({'method': method, 'apple': {'lists': {'收件箱': 'wrong'}}}, timeout=5)
    sent = json.loads(process.communicate.call_args.args[0])
    assert sent['method'] == method and sent['apple'] == backend.apple
    assert sent['apple']['lists']['收件箱'] == 'Ollo 测试·收件箱'
