"""Field byte limits are 413, independently of body and character limits."""
import json
from unittest.mock import AsyncMock

import pytest

pytest.register_assert_rewrite('ollo_bridge_test_support')
from ollo_bridge_test_support import example, KEY

pytest_plugins = ['ollo_bridge_test_support']


def request_payload(operation):
    payload = example(operation)
    if operation == 'interpret':
        payload['item']['choices']['arrangements'][0]['changes'] = [
            {'op': 'update', 'item': 'fixture-reminder-1', 'title': '更新标题'},
            {'op': 'merge', 'item': 'fixture-reminder-1', 'into': 'fixture-reminder-2', 'title': '合并标题'},
            {'op': 'create_reminder', 'title': '新建标题', 'list': '工作'},
        ]
    return payload


def set_field(payload, path, value):
    parent = payload
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value


BYTE_FIELDS = [
    ('capture', ('request', 'text')),
    ('interpret', ('request', 'text')),
    ('interpret', ('request', 'current_suggestion', 'title')),
    ('interpret', ('item', 'title')),
    ('interpret', ('item', 'suggestion', 'title')),
    ('interpret', ('item', 'findings', 0, 'title')),
    ('interpret', ('item', 'findings', 1, 'title')),
    ('interpret', ('item', 'findings', 3, 'change', 'title')),
    ('interpret', ('item', 'choices', 'titles', 0)),
    ('interpret', ('item', 'choices', 'arrangements', 0, 'changes', 0, 'title')),
    ('interpret', ('item', 'choices', 'arrangements', 0, 'changes', 1, 'title')),
    ('interpret', ('item', 'choices', 'arrangements', 0, 'changes', 2, 'title')),
    ('interpret', ('item', 'choices', 'extras', 0, 'change', 'title')),
]


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,path', BYTE_FIELDS)
@pytest.mark.parametrize('text', ['x' * 4097, '中' * 1365 + 'ab'], ids=['also_over_maxLength', 'only_over_bytes'])
async def test_request_field_byte_limits_before_any_dependency(bridge, gateway, monkeypatch, operation, path, text):
    payload = request_payload(operation)
    set_field(payload, path, text)
    assert len(text.encode('utf-8')) == 4097
    # RouterClient uses default JSON encoding; even that body fits the endpoint.
    assert len(json.dumps(payload).encode()) < {'capture': 16384, 'interpret': 4194304}[operation]
    helper = AsyncMock()
    monkeypatch.setattr(bridge.plugin.AppleBackend, 'call', helper)
    status, body = await bridge.client.request('POST', '/v1/ollo-bridge/schedule/' + operation, token=KEY, body=payload)
    assert status == 413
    assert body == {'code': 'payload_too_large', 'message': 'payload_too_large', 'retryable': False}
    assert gateway.calls == []  # No report read or write claim for invalid input.
    helper.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,path,value', [
    ('capture', ('request', 'source'), 's' * 65),
    ('capture', ('request', 'text'), []),
    ('items', ('ids', 0), 'x' * 513),
    ('items', ('ids', 0), '😀' * 513),
    ('items', ('ids',), [f'item-{i}' for i in range(101)]),
    ('items', ('report_id',), 'not-a-uuid'),
    ('interpret', ('request', 'item_id'), 'x' * 513),
    ('interpret', ('allowed_item_ids', 0), 'x' * 513),
    ('interpret', ('item', 'id'), 'x' * 513),
    ('interpret', ('item', 'notes'), 'x' * 4097),
    ('interpret', ('item', 'list'), 'x' * 129),
    ('interpret', ('request', 'current_suggestion', 'waiting_for'), 'x' * 129),
    ('interpret', ('item', 'findings', 0, 'type'), 'unsupported'),
    ('interpret', ('request', 'current_suggestion'), {}),
], ids=['source_length', 'capture_text_type', 'item_id_ascii_length', 'item_id_unicode_length',
        'item_count', 'report_uuid', 'interpret_item_id_length', 'allowed_id_length',
        'gtd_item_id_length', 'notes_length', 'list_length', 'waiting_for_length',
        'finding_discriminator', 'missing_suggestion_fields'])
async def test_non_byte_schema_errors_stay_400(bridge, gateway, operation, path, value):
    # ItemID is limited in characters, not x-max-utf8-bytes; don't invent a
    # byte policy for it, source, notes, list, or waiting_for.
    payload = request_payload(operation)
    set_field(payload, path, value)
    assert len(json.dumps(payload).encode()) < {'capture': 16384, 'items': 655360, 'interpret': 4194304}[operation]
    status, body = await bridge.client.request('POST', '/v1/ollo-bridge/schedule/' + operation, token=KEY, body=payload)
    assert status == 400
    assert body == {'code': 'invalid_request', 'message': 'invalid_request', 'retryable': False}
    assert gateway.calls == []


@pytest.mark.parametrize('combinator', ['oneOf', 'anyOf', 'allOf', 'nested'])
@pytest.mark.parametrize('value,status', [('12345', 413), ('😀a', 413), ([], 400), ('😀', 200)],
                         ids=['also_over_maxLength', 'only_over_bytes', 'wrong_type', 'at_limit'])
def test_composed_schema_uses_inner_byte_error(service, monkeypatch, combinator, value, status):
    leaf = {'type': 'string', 'maxLength': 4, 'x-max-utf8-bytes': 4}
    composed = ({'allOf': [{'oneOf': [{'type': 'null'}, {'anyOf': [{'type': 'integer'}, leaf]}]}]}
                if combinator == 'nested' else {combinator: [leaf]})
    # Supply a schema, not fabricated ValidationErrors: real jsonschema must
    # create and nest the errors that production classifies.
    monkeypatch.setitem(service.protocol._SCHEMA['$defs'], 'ByteLimitFixture', composed)
    if status == 200:
        assert service.protocol.validate('ByteLimitFixture', value) == value
    else:
        with pytest.raises(service.protocol.BridgeError) as caught:
            service.protocol.validate('ByteLimitFixture', value)
        code = 'payload_too_large' if status == 413 else 'invalid_request'
        assert (caught.value.status, caught.value.code, caught.value.retryable) == (status, code, False)


@pytest.mark.parametrize('combinator', ['oneOf', 'anyOf'])
def test_valid_alternative_does_not_inherit_failed_branch_byte_limit(service, monkeypatch, combinator):
    value = '😀a'
    composed = {combinator: [{'type': 'string', 'x-max-utf8-bytes': 4}, {'const': value}]}
    monkeypatch.setitem(service.protocol._SCHEMA['$defs'], 'ByteLimitFixture', composed)
    assert service.protocol.validate('ByteLimitFixture', value) == value


def test_later_byte_error_takes_precedence_over_other_field_errors(service):
    payload = example('capture')
    payload['operation_id'] = 'invalid-uuid'
    payload['request']['text'] = 'x' * 4097
    with pytest.raises(service.protocol.BridgeError) as caught:
        service.protocol.validate('B_BridgeCaptureRequest', payload)
    assert (caught.value.status, caught.value.code, caught.value.retryable) == (413, 'payload_too_large', False)
