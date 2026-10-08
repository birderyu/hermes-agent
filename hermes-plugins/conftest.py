"""Local plugin tests never discover a user's plugins, credentials or session DB."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))


def load_plugin_module(kind, filename='__init__.py'):
    path = ROOT / ('ollo-' + kind) / filename
    spec = importlib.util.spec_from_file_location('ollo_test_' + kind + '_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    empty = tmp_path / 'empty-bundled'
    empty.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('HERMES_BUNDLED_PLUGINS', str(empty))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    token = set_hermes_home_override(home)
    yield home
    reset_hermes_home_override(token)


class RouterClient:
    """Real aiohttp router, Request parsing and Response; no listening socket."""
    def __init__(self, app):
        self.app = app
        app.freeze()

    async def request(self, method, path, *, token='isolated-owner-key', body=None):
        from aiohttp import StreamReader
        from aiohttp.test_utils import make_mocked_request
        headers = {'Authorization': 'Bearer ' + token} if token is not None else {}
        raw = json.dumps(body).encode() if body is not None else b''
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'Content-Length': str(len(raw))})
        payload = StreamReader(Mock(_reading_paused=False), 2**16)
        payload.feed_data(raw)
        payload.feed_eof()
        request = make_mocked_request(method, path, headers=headers, payload=payload, app=self.app)
        response = await self.app._handle(request)
        return response.status, json.loads(response.body)


@pytest.fixture
def runtime(isolated_home, tmp_path):
    @contextmanager
    def start(names=('conversation',), *, home=None):
        from aiohttp import web
        from gateway.config import PlatformConfig
        from gateway.platforms.api_server import APIServerAdapter
        from hermes_cli.plugins import PluginManager
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        from hermes_state import SessionDB
        selected = home or isolated_home
        selected.mkdir(exist_ok=True)
        token = set_hermes_home_override(selected)
        for kind in names:
            shutil.copytree(ROOT / ('ollo-' + kind), selected / 'plugins' / ('ollo-' + kind),
                            ignore=shutil.ignore_patterns('__pycache__', 'test_*'))
        (selected / 'config.yaml').write_text(json.dumps({'plugins': {'enabled': ['ollo-' + n for n in names]}}))
        manager = PluginManager(scope_key=str(selected))
        db = SessionDB(selected / 'state.db')
        try:
            manager.discover_and_load()
            for kind in names:
                loaded = manager._plugins['ollo-' + kind]
                assert loaded.enabled and not loaded.error, loaded.error
            api = APIServerAdapter(PlatformConfig(extra={'key': 'isolated-owner-key'}))
            api._session_db = db
            app = web.Application()
            for wire, _ in manager.get_platform_handler_factories('api_server'):
                wire(app, api)
            yield SimpleNamespace(manager=manager, db=db, api=api, app=app, client=RouterClient(app), home=selected)
        finally:
            manager.unload()
            db.close()
            reset_hermes_home_override(token)
    return start
