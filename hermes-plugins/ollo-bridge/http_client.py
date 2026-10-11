"""One fixed loopback destination, no redirects/proxies and bounded response bodies."""
import asyncio
import time

import aiohttp

from .protocol import BridgeError, dumps, loads


def upstream_error(status, data):
    # Keep only a contract code; peer text can contain credentials or user data.
    # Invalid JSON/unknown codes are a local 503, never a new code with an old 4xx.
    try:
        payload = loads(data)
    except BridgeError:
        return BridgeError()
    code = payload.get('code') if isinstance(payload, dict) else None
    return BridgeError(code if isinstance(code, str) else 'dependency_unavailable', status)


class GatewayClient:
    def __init__(self, config):
        self.config = config

    async def request(self, method, path, *, body=None, headers=None, timeout=2):
        if not self.config:
            raise BridgeError('not_configured')
        if not path.startswith('/internal/v1/') or '://' in path or '\\' in path:
            raise BridgeError('invalid_request', 400, False)
        request_headers = {'Authorization': 'Bearer ' + self.config.key, **(headers or {})}
        try:
            async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=timeout),
                                             json_serialize=dumps) as client:
                async with client.request(method, self.config.gateway_url + path, json=body,
                                          headers=request_headers, allow_redirects=False) as response:
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        data.extend(chunk)
                        if len(data) > 16000000:
                            raise BridgeError()
                    if response.status not in {200, 201, 202}:
                        raise upstream_error(response.status, data)
                    try:
                        return loads(data)
                    except BridgeError as exc:
                        raise BridgeError() from exc
        except asyncio.TimeoutError as exc:
            raise BridgeError("read_timeout", 504, True) from exc
        except aiohttp.ClientError as exc:
            raise BridgeError() from exc


class Budget:
    def __init__(self, seconds):
        self.deadline = time.monotonic() + seconds

    def remaining(self, cap=None):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise BridgeError('read_timeout', 504, True)
        return min(remaining, cap) if cap is not None else remaining
