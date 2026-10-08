"""Rename contracts: durable migration, config precedence, APNs and route aliases."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import uuid

import pytest

from conftest import load_plugin_module


@pytest.mark.parametrize('kind', ['inbox', 'location'])
def test_migration_preserves_database_and_backup_and_never_overwrites_new_data(isolated_home, kind):
    migration = load_plugin_module(kind, 'migration.py')
    legacy = isolated_home / 'plugin-data' / ('hermes-plus-' + kind)
    if kind == 'inbox':
        module = load_plugin_module(kind, 'store.py')
        store = module.InboxStore(legacy / 'inbox.sqlite')
        record = store.deliver('root', 'job', uuid.uuid4().hex, 'Report', 'unchanged body')
        read = lambda folder: module.InboxStore(folder / 'inbox.sqlite').get('root', record['id'])
    else:
        module = load_plugin_module(kind)
        store = module.DeviceStore(legacy / 'latest.sqlite')
        record = store.authorize(str(uuid.uuid4()), 'root', 'Test phone', 'iOS')
        read = lambda folder: module.DeviceStore(folder / 'latest.sqlite').state(record['device_token'])
    before = read(legacy)
    (legacy / 'extra-state.json').write_text('{"retained": true}')
    with ThreadPoolExecutor(max_workers=2) as workers:
        paths = list(workers.map(lambda _: migration.data_directory(isolated_home, kind), range(2)))
    new = paths[0]
    assert paths[1] == new
    backups = list(legacy.parent.glob(legacy.name + '.backup-*'))
    assert len(backups) == 1 and not backups[0].name.endswith('.partial')
    assert not legacy.exists()
    assert read(new) == read(backups[0]) == before
    assert (new / 'extra-state.json').read_bytes() == (backups[0] / 'extra-state.json').read_bytes()
    assert migration.data_directory(isolated_home, kind) == new
    assert list(legacy.parent.glob(legacy.name + '.backup-*')) == backups
    # A leftover legacy directory must never overwrite an already-used new one.
    legacy.mkdir()
    (legacy / 'extra-state.json').write_text('stale')
    assert migration.data_directory(isolated_home, kind) == new
    assert read(new) == before
    assert (legacy / 'extra-state.json').read_text() == 'stale'


@pytest.mark.parametrize('kind', ['inbox', 'location'])
@pytest.mark.parametrize('failure', ['backup', 'rename'])
def test_migration_failure_keeps_using_legacy_and_logs(isolated_home, monkeypatch, caplog, kind, failure):
    migration = load_plugin_module(kind, 'migration.py')
    legacy = isolated_home / 'plugin-data' / ('hermes-plus-' + kind)
    legacy.mkdir(parents=True)
    (legacy / 'state').write_text('precious')
    rename = Path.rename
    def denied_rename(source, destination):
        if source == legacy:
            raise PermissionError('denied')
        return rename(source, destination)
    def denied_backup(source, destination, **kwargs):
        destination.mkdir()
        (destination / 'state').write_text('incomplete copy')
        raise PermissionError('denied')
    if failure == 'backup':
        monkeypatch.setattr(migration.shutil, 'copytree', denied_backup)
    else:
        monkeypatch.setattr(Path, 'rename', denied_rename)
    assert migration.data_directory(isolated_home, kind) == legacy
    assert (legacy / 'state').read_text() == 'precious'
    assert not (legacy.parent / ('ollo-' + kind)).exists()
    assert 'continuing with' in caplog.text
    complete = [p for p in legacy.parent.glob(legacy.name + '.backup-*') if not p.name.endswith('.partial')]
    if failure == 'rename':
        assert len(complete) == 1 and (complete[0] / 'state').read_text() == 'precious'
    else:
        assert complete == []
        assert len(list(legacy.parent.glob('*.partial'))) == 1


def test_inbox_config_new_directory_wins_without_mixing_files(isolated_home):
    plugin = load_plugin_module('inbox')
    old = isolated_home / 'hermes-plus'
    old.mkdir()
    legacy = {'session_id': 'old', 'jobs': {'job': 'Report'}}
    (old / 'inbox.json').write_text(json.dumps(legacy))
    assert plugin.read_config(isolated_home) == legacy
    new = isolated_home / 'ollo'
    new.mkdir()
    assert plugin.read_config(isolated_home) is None
    current = {**legacy, 'session_id': 'new'}
    (new / 'inbox.json').write_text(json.dumps(current))
    assert plugin.read_config(isolated_home) == current


@pytest.mark.parametrize('kind', ['inbox', 'location'])
def test_apns_new_variables_override_individually_without_empty_value_fallback(kind):
    apns = load_plugin_module(kind, 'apns.py')
    values = {'KEY_PATH': '/synthetic/key.p8', 'KEY_ID': 'A'*10, 'TEAM_ID': 'B'*10, 'TOPIC': 'com.birderyu.ollo'}
    old = {'HERMES_PLUS_APNS_' + key: value for key, value in values.items()}
    new = {'OLLO_APNS_' + key: value for key, value in values.items()}
    assert apns.APNsProvider.from_env(old)._values == apns.APNsProvider.from_env(new)._values
    mixed = {**old, 'OLLO_APNS_KEY_ID': 'C'*10}
    provider = apns.APNsProvider.from_env(mixed)
    assert provider.status() == 'ready' and provider._values[1] == 'C'*10
    assert apns.APNsProvider.from_env({**old, 'OLLO_APNS_KEY_ID': ''}).status() == 'invalid_configuration'


@pytest.mark.asyncio
@pytest.mark.parametrize('push_ready', [False, True])
async def test_inbox_aliases_share_auth_storage_delivery_identity_and_report_bytes(runtime, isolated_home, monkeypatch, push_ready):
    if push_ready:
        for name, value in {'KEY_PATH':'/test/key.p8', 'KEY_ID':'A'*10, 'TEAM_ID':'B'*10, 'TOPIC':'com.birderyu.ollo'}.items():
            monkeypatch.setenv('OLLO_APNS_' + name, value)
    old = isolated_home / 'hermes-plus'
    old.mkdir()
    (old / 'inbox.json').write_text('{"session_id":"root","jobs":{"job":"Report"}}')
    module = load_plugin_module('inbox', 'store.py')
    legacy_dir = isolated_home / 'plugin-data/hermes-plus-inbox'
    seed = module.InboxStore(legacy_dir / 'inbox.sqlite').deliver('root', 'job', uuid.uuid4().hex, 'Report', 'old report')
    with runtime(('inbox',)) as r:
        from gateway.platform_registry import platform_registry
        from gateway.config import PlatformConfig
        r.db.create_session('root', 'api_server')
        r.db.end_session('root', 'compression')
        r.db.create_session('tip', 'api_server', parent_session_id='root')
        r.db.create_session('other', 'api_server')
        delivery = {'job_id': 'job', 'execution_id': uuid.uuid4().hex}
        body = '事情办成了。\n\n```hermes-plus-gtd\n{}\n```\n\n```ollo-mood\n{"mood":"happy"}\n```'
        ids = []
        for name in ('ollo', 'hermes_plus'):
            entry = platform_registry.get(name)
            assert entry.validate_target_ref_fn('main') and not entry.validate_target_ref_fn('other')
            assert entry.parse_target_ref_fn('main') == ('main', None)
            adapter = entry.adapter_factory(PlatformConfig())
            assert adapter.platform.value == name
            assert await adapter.connect()
            result = await adapter.send('main', body, metadata=delivery)
            assert result.success
            ids.append(result.message_id)
            assert not (await adapter.send('main', body, metadata={'job_id': 'job'})).success
            await adapter.disconnect()
        assert ids[0] == ids[1]
        # No app startup/push pump or network runs in this router-only test.
        device = str(uuid.uuid4())
        for prefix in ('ollo', 'hermes-plus'):
            base = '/v1/' + prefix + '/inbox'
            for suffix in ('', '/reports', '/reports/' + ids[0]):
                assert (await r.client.request('GET', base + suffix + '?session_id=root', token='wrong'))[0] == 401
                assert (await r.client.request('GET', base + suffix + '?session_id=other'))[0] == 403
            assert await r.client.request('GET', base + '?session_id=tip') == (200, {'version': 1, 'push_status': 'ready' if push_ready else 'not_configured'})
            status, listing = await r.client.request('GET', base + '/reports?session_id=tip')
            assert status == 200 and [row['id'] for row in listing['reports']] == [seed['id'], ids[0]]
            assert (await r.client.request('GET', base + '/reports/' + ids[0] + '?session_id=tip'))[1]['body'] == body
            assert (await r.client.request('PUT', base + '/devices/' + device,
                    body={'session_id':'root', 'token':'aa11', 'environment':'sandbox'}))[0] == (200 if push_ready else 503)
            assert (await r.client.request('POST', base + '/reports/' + ids[0] + '/read',
                    body={'session_id':'root','device_id':device}))[0] == 200
            assert (await r.client.request('DELETE', base + '/devices/' + device + '?session_id=root'))[0] == 200
        assert not legacy_dir.exists()
        store = module.InboxStore(isolated_home / 'plugin-data/ollo-inbox/inbox.sqlite')
        assert len(store.list('root')) == 2
