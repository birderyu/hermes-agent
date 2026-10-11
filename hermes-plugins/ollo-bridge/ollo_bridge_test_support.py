"""Synthetic gateway over the real HTTP router/parser, without a listening socket."""
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import Mock
from uuid import uuid4

import pytest

FIXTURES = Path(__file__).parent / 'fixtures'
KEY = 'isolated-bridge-key-00000000000000000000'


def example(name):
    return json.loads((FIXTURES / (name + '.json')).read_text(encoding='utf-8'))


@pytest.fixture
def bridge_config(isolated_home):
    directory = isolated_home / 'ollo'
    directory.mkdir()
    value = {'session_id': 'root', 'gateway_url': 'http://127.0.0.1:8765',
             'jobs': {'fixture-agenda-job': {'title': '今日安排', 'kind': 'agenda'},
                      'weekday': {'title': '今日安排', 'kind': 'agenda'},
                      'inbox': {'title': '收件箱整理', 'kind': 'inbox'}},
             'agenda_jobs': {'weekday': 'weekday', 'weekend': 'fixture-agenda-job'},
             'helper_path': '/synthetic/ollo-eventkit'}
    (directory / 'bridge.json').write_text(json.dumps(value), encoding='utf-8')
    (directory / 'bridge.key').write_text(KEY, encoding='utf-8')
    (directory / 'bridge.key').chmod(0o600)
    return value


@pytest.fixture
def bridge(runtime, bridge_config):
    with runtime(('bridge',), host_runs=True) as r:
        r.plugin = r.manager._plugins['ollo-bridge'].module
        r.db.create_session('root', 'api_server')
        r.db.end_session('root', 'compression')
        r.db.create_session('tip', 'api_server', parent_session_id='root')
        for name, marker in (('branch', '_branched_from'), ('reset', '_reset_from'), ('delegate', '_delegate_from')):
            r.db.create_session(name, 'api_server', parent_session_id='root', model_config={marker: 'root'})
        r.db.create_session('other', 'api_server')
        yield r


class HTTPGateway:
    def __init__(self):
        from aiohttp import web
        self.web = web
        self.app = web.Application()
        self.app.router.add_route('*', '/{path:.*}', self.handle)
        self.app.freeze()
        self.routes, self.calls, self.request_bytes = {}, [], []

    async def handle(self, request):
        body = await request.json() if request.can_read_body else None
        self.calls.append((request.method, request.path_qs, body, dict(request.headers)))
        assert request.headers.get('Authorization') == 'Bearer ' + KEY
        handler = self.routes.get((request.method, request.path))
        if handler is None:
            return self.web.json_response({'code': 'not_found', 'retryable': False}, status=404)
        if callable(handler):
            return await handler(request, body)
        status, value = handler
        return self.web.json_response(value, status=status)

    def install(self, monkeypatch):
        import aiohttp
        gateway = self
        class ResponseContext:
            def __init__(self, method, url, kwargs, serialize):
                self.method, self.url, self.kwargs, self.serialize = method, url, kwargs, serialize

            async def __aenter__(self):
                from aiohttp.test_utils import make_mocked_request
                assert self.kwargs['allow_redirects'] is False
                parsed = urlsplit(self.url)
                assert parsed.scheme == 'http' and parsed.netloc == '127.0.0.1:8765'
                body = self.kwargs.get('json')
                data = self.serialize(body).encode('utf-8') if body is not None else b''
                gateway.request_bytes.append(data)
                headers = dict(self.kwargs['headers'])
                if body is not None:
                    headers.update({'Content-Type': 'application/json', 'Content-Length': str(len(data))})
                reader = aiohttp.StreamReader(Mock(_reading_paused=False), 2**16)
                reader.feed_data(data); reader.feed_eof()
                request = make_mocked_request(self.method, parsed.path + ('?' + parsed.query if parsed.query else ''),
                                              headers=headers, payload=reader, app=gateway.app)
                result = await gateway.app._handle(request)
                content = aiohttp.StreamReader(Mock(_reading_paused=False), 2**16)
                content.feed_data(result.body); content.feed_eof()
                return SimpleNamespace(status=result.status, content=content)

            async def __aexit__(self, *args):
                return False

        class Session:
            def __init__(self, *, trust_env, timeout, json_serialize):
                assert trust_env is False and 0 < timeout.total <= 15
                self.serialize = json_serialize
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            def request(self, method, url, **kwargs):
                return ResponseContext(method, url, kwargs, self.serialize)
        monkeypatch.setattr(aiohttp, 'ClientSession', Session)


@pytest.fixture
def gateway(monkeypatch):
    value = HTTPGateway()
    value.install(monkeypatch)
    value.routes[('GET', '/internal/v1/context')] = (200, example('context'))
    return value


@pytest.fixture
def service(bridge):
    from importlib import import_module
    base = bridge.plugin.__name__
    config = bridge.plugin.read_config(bridge.home)
    backend = SimpleNamespace(items=Mock(), capture=Mock(), group=Mock())
    llm = SimpleNamespace(acomplete_structured=Mock())
    return SimpleNamespace(api=bridge.plugin.Schedule(config, bridge.plugin.GatewayClient(config), backend, llm),
                           apple=backend, llm=llm, protocol=import_module(base + '.protocol'),
                           apple_module=import_module(base + '.apple'), base=base)


class Operations:
    """Gateway stand-in owns durable claims. Recreated bridge services share it."""
    def __init__(self, gateway):
        self.gateway, self.permits, self.states, self.outcomes = gateway, {}, {}, {}
        self.finishes = []

    def add(self, operation_id, permit):
        self.permits[operation_id] = permit
        self.gateway.routes[('POST', f'/internal/v1/schedule/operations/{operation_id}/claim')] = self.claim
        self.gateway.routes[('POST', f'/internal/v1/schedule/operations/{operation_id}/finish')] = self.finish

    async def claim(self, request, body):
        operation = request.path.split('/')[-2]
        if operation in self.outcomes:
            outcome = self.outcomes[operation]
            return self.gateway.web.json_response({'status': outcome['status'], 'outcome': outcome})
        if operation in self.states:
            return self.gateway.web.json_response({'status': 'executing', 'outcome': None})
        attempt = str(uuid4())
        self.states[operation] = (attempt, body['runtime'])
        return self.gateway.web.json_response({'status': 'claimed', 'attempt_id': attempt, 'operation': self.permits[operation]})

    async def finish(self, request, body):
        operation = request.path.split('/')[-2]
        assert self.states[operation] == (body['attempt_id'], body['runtime'])
        if operation in self.outcomes:
            assert self.outcomes[operation] == body['outcome']
        self.outcomes[operation] = body['outcome']
        self.finishes.append(body)
        return self.gateway.web.json_response({'accepted': True})
