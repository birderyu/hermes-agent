"""Optional APNs hints for the persisted device request queue.

The caller must save the request before invoking this module. An accepted push
only means APNs accepted a hint; it does not mean the phone ran or supplied a
location. No coordinates or device credentials belong in the push payload.

Configuration (all four are required): OLLO_APNS_KEY_PATH (an absolute
path to a private, external .p8 file), OLLO_APNS_KEY_ID,
OLLO_APNS_TEAM_ID, and OLLO_APNS_TOPIC (the signed app bundle ID).
Optional dependencies are httpx[http2] and PyJWT[crypto]. Nothing is imported,
read from a key file, or sent to APNs merely by importing this module.
"""

import asyncio
import hashlib
import importlib
import math
import os
from pathlib import Path
import re
import time
from typing import NamedTuple
import uuid


ENDPOINTS = {
    'sandbox': 'https://api.sandbox.push.apple.com',
    'production': 'https://api.push.apple.com',
}
MIN_HINT_INTERVAL = 1200  # Apple recommends no more than 2–3 background pushes/hour.
TOKEN_LIFETIME = 3000  # Refresh after 50 minutes, within Apple's 20–60 minute window.
_CONFIG_NAMES = ('KEY_PATH', 'KEY_ID', 'TEAM_ID', 'TOPIC')
_SAFE_REASONS = frozenset({
    'BadDeviceToken', 'DeviceTokenNotForTopic', 'Unregistered', 'BadTopic',
    'TopicDisallowed', 'ExpiredProviderToken', 'InvalidProviderToken',
    'MissingProviderToken', 'TooManyProviderTokenUpdates', 'TooManyRequests',
    'InternalServerError', 'ServiceUnavailable', 'Shutdown', 'BadPriority',
    'BadExpirationDate', 'BadCollapseId', 'PayloadEmpty', 'PayloadTooLarge',
    'MissingTopic', 'Forbidden', 'BadPath', 'MethodNotAllowed',
})


class APNsResult(NamedTuple):
    status: str
    reason: str | None = None
    invalidate_token: bool = False
    invalidated_at: float | None = None


def valid_device_token(value):
    """Device token sizes may change; do not assume Apple's current byte count."""
    return (isinstance(value, str) and 2 <= len(value) <= 512
            and len(value) % 2 == 0 and re.fullmatch(r'[0-9a-fA-F]+', value) is not None)


class APNsProvider:
    """One provider per API server event loop; call aclose() on app cleanup."""

    def __init__(self, *, key_path=None, key_id=None, team_id=None, topic=None,
                 clock=time.time):
        self._values = (key_path, key_id, team_id, topic)
        self._clock = clock
        self._jwt = None
        self._jwt_issued_at = 0
        self._client = None
        self._last_hints = {}

    @classmethod
    def from_env(cls, environ=None):
        environ = os.environ if environ is None else environ
        values = {name.lower(): environ.get('OLLO_APNS_' + name, environ.get('HERMES_PLUS_APNS_' + name))
                  for name in _CONFIG_NAMES}
        return cls(**values)

    def status(self):
        """Configuration status only; 'ready' does not claim key/network validity."""
        if not any(self._values):
            return 'not_configured'
        if not all(isinstance(value, str) and value for value in self._values):
            return 'invalid_configuration'
        key_path, key_id, team_id, topic = self._values
        if (not Path(key_path).is_absolute()
                or not re.fullmatch(r'[A-Z0-9]{10}', key_id)
                or not re.fullmatch(r'[A-Z0-9]{10}', team_id)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,254}', topic)):
            return 'invalid_configuration'
        return 'ready'

    def _provider_token(self, now):
        if self._jwt is not None and 0 <= now - self._jwt_issued_at < TOKEN_LIFETIME:
            return self._jwt
        jwt = importlib.import_module('jwt')
        serialization = importlib.import_module('cryptography.hazmat.primitives.serialization')
        ec = importlib.import_module('cryptography.hazmat.primitives.asymmetric.ec')
        key_path, key_id, team_id, _ = self._values
        # This is the only key-file access. Never include its contents, filesystem
        # error text, signed JWT, or APNs request headers in a result or log.
        with Path(key_path).open('rb') as source:
            key_bytes = source.read(16385)
        if len(key_bytes) > 16384:
            raise ValueError('Invalid signing key')
        key = serialization.load_pem_private_key(key_bytes, password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError('Invalid signing key')
        token = jwt.encode({'iss': team_id, 'iat': int(now)}, key,
                           algorithm='ES256', headers={'kid': key_id})
        if not isinstance(token, str):
            raise ValueError('Invalid signing result')
        self._jwt, self._jwt_issued_at = token, now
        return token

    def _http_client(self):
        if self._client is None:
            httpx = importlib.import_module('httpx')
            # Never follow a redirect with the provider credential or use an
            # environment-specified proxy. Endpoints are fixed Apple hosts.
            self._client = httpx.AsyncClient(http2=True, http1=False, timeout=5,
                                             trust_env=False, follow_redirects=False)
        return self._client

    async def send_location_hint(self, token, environment, request_id, deadline):
        state = self.status()
        if state != 'ready':
            return APNsResult(state)
        if (not valid_device_token(token) or not isinstance(environment, str)
                or environment not in ENDPOINTS):
            return APNsResult('invalid_request')
        try:
            request_id = str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            return APNsResult('invalid_request')
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            return APNsResult('invalid_request')
        now = self._clock()
        if deadline <= now:
            return APNsResult('expired')
        # Hash the throttle key so the provider does not retain device tokens.
        hint_key = hashlib.sha256((environment + ':' + token.lower()).encode()).digest()
        self._last_hints = {key: last for key, last in self._last_hints.items()
                            if now - last < MIN_HINT_INTERVAL}
        if hint_key in self._last_hints:
            return APNsResult('throttled')
        try:
            client = self._http_client()
            provider_token = self._provider_token(now)
        except ImportError:
            return APNsResult('dependencies_unavailable')
        except Exception:
            return APNsResult('credentials_unavailable')
        remaining = deadline - self._clock()
        if remaining <= 0:
            return APNsResult('expired')
        # Reserve before the network await, including failures/cancellation, so
        # simultaneous tools cannot generate bursts. Queue polling is unaffected.
        self._last_hints[hint_key] = now
        headers = {
            'authorization': 'bearer ' + provider_token,
            'apns-topic': self._values[3],
            'apns-push-type': 'background',
            'apns-priority': '5',
            'apns-expiration': str(int(deadline)),
            'apns-collapse-id': 'hermes-plus-location',
        }
        payload = {'aps': {'content-available': 1},
                   'hermes_plus': {'capability': 'location.read', 'request_id': request_id}}
        try:
            timeout = min(5, remaining)
            response = await asyncio.wait_for(
                client.post(ENDPOINTS[environment] + '/3/device/' + token.lower(),
                            headers=headers, json=payload, timeout=timeout),
                timeout=timeout)
            if response.http_version != 'HTTP/2':
                return APNsResult('transport_error')
            if response.status_code == 200:
                return APNsResult('accepted')
            body = response.json()
            reason = body.get('reason') if isinstance(body, dict) else None
            # Do not expose arbitrary remote text (which can contain secrets).
            reason = reason if reason in _SAFE_REASONS else None
            invalidated = body.get('timestamp') if isinstance(body, dict) else None
            if type(invalidated) not in (int, float) or not math.isfinite(invalidated):
                invalidated = None
            else:
                invalidated /= 1000  # APNs sends Unregistered timestamps in ms.
            invalid = reason in ('BadDeviceToken', 'Unregistered')
            if response.status_code in (408, 429, 500, 503):
                return APNsResult('retry_later', reason)
            return APNsResult('rejected', reason, invalid, invalidated if invalid else None)
        except Exception:
            # Request URLs contain device tokens. Return a stable state instead
            # of an exception string, response body, or request/response dump.
            return APNsResult('transport_error')

    async def aclose(self):
        client, self._client = self._client, None
        self._jwt = None
        if client is not None:
            await client.aclose()
