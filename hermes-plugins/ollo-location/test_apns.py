"""APNs unit tests use synthetic signing inputs and a mocked HTTP/2 transport."""

import asyncio
import base64
import importlib.util
import io
import json
from pathlib import Path
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch


spec = importlib.util.spec_from_file_location('location_apns', Path(__file__).with_name('apns.py'))
apns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apns)

TOKEN = 'ab' * 32
REQUEST_ID = '4b160ceb-6eea-4907-8619-9b8c77ff1661'
CONFIG = {'key_path': '/private/test-only/never-read.p8', 'key_id': 'TESTKEY123',
          'team_id': 'TESTTEAM12', 'topic': 'com.birderyu.ollo'}


class APNsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 1000
        self.provider = apns.APNsProvider(**CONFIG, clock=lambda: self.now)
        self.response = types.SimpleNamespace(status_code=200, http_version='HTTP/2',
                                              json=lambda: {})
        self.client = types.SimpleNamespace(post=AsyncMock(return_value=self.response),
                                            aclose=AsyncMock())
        self.client_factory = Mock(return_value=self.client)
        self.signing_calls = []

        class Curve:
            pass

        class PrivateKey:
            curve = Curve()

        self.private_key = PrivateKey()
        self.load_key = Mock(return_value=self.private_key)

        def encode(payload, key, algorithm, headers):
            self.signing_calls.append((payload, key, algorithm, headers))
            def part(value):
                return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
            return part({'alg': algorithm, **headers}) + '.' + part(payload) + '.synthetic-signature'

        modules = {
            'httpx': types.SimpleNamespace(AsyncClient=self.client_factory),
            'jwt': types.SimpleNamespace(encode=encode),
            'cryptography.hazmat.primitives.serialization': types.SimpleNamespace(load_pem_private_key=self.load_key),
            'cryptography.hazmat.primitives.asymmetric.ec': types.SimpleNamespace(EllipticCurvePrivateKey=PrivateKey, SECP256R1=Curve),
        }
        self.importer = self.enterContext(patch.object(apns.importlib, 'import_module', side_effect=modules.__getitem__))
        # No actual .p8 file is opened by these tests.
        self.open_key = self.enterContext(patch.object(Path, 'open', side_effect=lambda *a, **k: io.BytesIO(b'SYNTHETIC TEST KEY')))

    async def send(self, **changes):
        values = {'token': TOKEN, 'environment': 'sandbox', 'request_id': REQUEST_ID, 'deadline': self.now + 15}
        values.update(changes)
        return await self.provider.send_location_hint(**values)

    async def test_unconfigured_is_safe_without_importing_dependencies_or_reading_keys(self):
        provider = apns.APNsProvider.from_env({})
        self.assertEqual(provider.status(), 'not_configured')
        self.assertEqual((await provider.send_location_hint(TOKEN, 'sandbox', REQUEST_ID, 1015)).status,
                         'not_configured')
        self.importer.assert_not_called()
        self.open_key.assert_not_called()

    async def test_partial_or_invalid_configuration_fails_closed(self):
        for changes in ({'key_id': None}, {'key_path': 'relative.p8'}, {'key_id': 'bad'},
                        {'team_id': 'bad'}, {'topic': 'unsafe\nheader'}, {'topic': '*.app'}):
            with self.subTest(changes=changes):
                provider = apns.APNsProvider(**{**CONFIG, **changes})
                self.assertEqual(provider.status(), 'invalid_configuration')
                self.assertEqual((await provider.send_location_hint(TOKEN, 'sandbox', REQUEST_ID, 1015)).status,
                                 'invalid_configuration')
        self.importer.assert_not_called()

    async def test_environment_configuration_names(self):
        environ = {'OLLO_APNS_' + key.upper(): value for key, value in CONFIG.items()}
        provider = apns.APNsProvider.from_env(environ)
        self.assertEqual(provider.status(), 'ready')
        self.open_key.assert_not_called()

    async def test_http2_request_contains_only_background_hint(self):
        result = await self.send(token=TOKEN.upper())
        self.assertEqual(result.status, 'accepted')
        self.client_factory.assert_called_once_with(http2=True, http1=False, timeout=5,
                                                    trust_env=False, follow_redirects=False)
        args, kwargs = self.client.post.call_args
        self.assertEqual(args[0], 'https://api.sandbox.push.apple.com/3/device/' + TOKEN)
        headers = kwargs['headers']
        self.assertEqual(headers['apns-topic'], CONFIG['topic'])
        self.assertEqual(headers['apns-push-type'], 'background')
        self.assertEqual(headers['apns-priority'], '5')
        self.assertEqual(headers['apns-expiration'], '1015')
        self.assertEqual(headers['apns-collapse-id'], 'hermes-plus-location')
        self.assertEqual(kwargs['json'], {'aps': {'content-available': 1},
                                          'hermes_plus': {'capability': 'location.read', 'request_id': REQUEST_ID}})
        self.assertEqual(kwargs['timeout'], 5)
        self.assertEqual(self.signing_calls[0], ({'iss': 'TESTTEAM12', 'iat': 1000}, self.private_key,
                                                'ES256', {'kid': 'TESTKEY123'}))
        encoded_header, encoded_payload, _ = headers['authorization'].removeprefix('bearer ').split('.')
        def decode(value):
            return json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
        self.assertEqual(decode(encoded_header), {'alg': 'ES256', 'kid': 'TESTKEY123'})
        self.assertEqual(decode(encoded_payload), {'iss': 'TESTTEAM12', 'iat': 1000})
        self.assertNotIn('SYNTHETIC', repr(result))

    async def test_production_uses_only_fixed_apple_host(self):
        self.assertEqual((await self.send(environment='production')).status, 'accepted')
        self.assertEqual(self.client.post.call_args.args[0], 'https://api.push.apple.com/3/device/' + TOKEN)

    async def test_invalid_inputs_never_read_credentials_or_send(self):
        cases = [{'token': 'abc'}, {'token': 'ab/../secret'}, {'token': 'aa' * 257},
                 {'token': None}, {'environment': 'development'}, {'environment': 'https://elsewhere'},
                 {'environment': {}}, {'request_id': 'bad'}, {'deadline': True},
                 {'deadline': float('nan')}, {'deadline': float('inf')}]
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertEqual((await self.send(**changes)).status, 'invalid_request')
        self.client.post.assert_not_called()
        self.open_key.assert_not_called()
        self.assertTrue(apns.valid_device_token('ab' * 48))

    async def test_expired_request_does_not_send(self):
        self.assertEqual((await self.send(deadline=1000)).status, 'expired')
        self.importer.assert_not_called()

    async def test_expiry_during_preparation_does_not_send(self):
        with patch.object(self.provider, '_provider_token', side_effect=lambda now: setattr(self, 'now', 1016) or 'jwt'):
            self.assertEqual((await self.send(deadline=1015)).status, 'expired')
        self.client.post.assert_not_called()

    async def test_dependencies_missing_does_not_raise_or_touch_key(self):
        self.importer.side_effect = ImportError('dependency not installed')
        self.assertEqual((await self.send()).status, 'dependencies_unavailable')
        self.open_key.assert_not_called()

    async def test_key_failure_hides_path_and_error_text(self):
        self.open_key.side_effect = OSError('sensitive-path-and-content')
        result = await self.send()
        self.assertEqual(result.status, 'credentials_unavailable')
        self.assertNotIn('sensitive', repr(result))
        self.client.post.assert_not_called()

    async def test_wrong_curve_and_oversized_keys_fail_before_network(self):
        self.load_key.return_value = object()
        self.assertEqual((await self.send()).status, 'credentials_unavailable')
        self.open_key.side_effect = lambda *a, **k: io.BytesIO(b'x' * 16385)
        self.assertEqual((await self.send()).status, 'credentials_unavailable')
        self.client.post.assert_not_called()

    async def test_throttles_even_failed_attempts_and_normalizes_case(self):
        self.client.post.side_effect = RuntimeError('secret-token-in-url')
        self.assertEqual((await self.send()).status, 'transport_error')
        self.now += 1199
        self.assertEqual((await self.send(token=TOKEN.upper())).status, 'throttled')
        self.now += 1
        self.client.post.side_effect = None
        self.assertEqual((await self.send()).status, 'accepted')
        self.assertEqual(self.client.post.await_count, 2)
        self.assertNotIn(TOKEN, repr(self.provider._last_hints))

    async def test_token_reused_then_refreshed_after_50_minutes(self):
        await self.send()
        self.now += 1200
        await self.send()
        self.assertEqual(len(self.signing_calls), 1)
        self.now += 1800
        await self.send()
        self.assertEqual(len(self.signing_calls), 2)
        self.assertEqual(self.signing_calls[-1][0]['iat'], 4000)

    async def test_revoked_device_token_is_reported_without_revoking_permission(self):
        self.response.status_code = 410
        self.response.json = lambda: {'reason': 'Unregistered', 'timestamp': 999000}
        result = await self.send()
        self.assertEqual(result, apns.APNsResult('rejected', 'Unregistered', True, 999.0))

    async def test_wrong_topic_does_not_invalidate_device_token(self):
        self.response.status_code = 400
        self.response.json = lambda: {'reason': 'DeviceTokenNotForTopic'}
        result = await self.send()
        self.assertEqual(result, apns.APNsResult('rejected', 'DeviceTokenNotForTopic'))

    async def test_temporary_failure_and_untrusted_reason_are_bounded(self):
        self.response.status_code = 503
        self.response.json = lambda: {'reason': 'ServiceUnavailable'}
        self.assertEqual(await self.send(), apns.APNsResult('retry_later', 'ServiceUnavailable'))
        self.now += 1200
        self.response.status_code = 400
        self.response.json = lambda: {'reason': 'secret-arbitrary-server-body'}
        self.assertEqual(await self.send(), apns.APNsResult('rejected'))

    async def test_redirect_or_http1_response_is_not_accepted(self):
        self.response.status_code = 307
        self.assertEqual((await self.send()).status, 'rejected')
        self.now += 1200
        self.response.status_code = 200
        self.response.http_version = 'HTTP/1.1'
        self.assertEqual((await self.send()).status, 'transport_error')

    async def test_request_timeout_is_bounded_by_pending_deadline(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(1)
        self.client.post.side_effect = slow
        self.assertEqual((await self.send(deadline=1000.01)).status, 'transport_error')
        self.assertAlmostEqual(self.client.post.call_args.kwargs['timeout'], 0.01)

    async def test_close_releases_client_and_cached_jwt(self):
        await self.send()
        await self.provider.aclose()
        self.client.aclose.assert_awaited_once()
        self.assertIsNone(self.provider._client)
        self.assertIsNone(self.provider._jwt)


if __name__ == '__main__':
    unittest.main()
