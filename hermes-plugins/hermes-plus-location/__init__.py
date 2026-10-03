"""Opt-in latest-location context. No transcript writes or model calls on updates."""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import uuid
import asyncio
import hashlib
import secrets
import threading

FAILURES = frozenset({'permission_denied', 'location_disabled', 'timeout',
                      'background_unavailable', 'unavailable', 'cancelled'})


def checked_point(point, now):
    if not isinstance(point, dict):
        raise ValueError('Invalid point')
    result = {k: point[k] for k in ('latitude', 'longitude', 'accuracy', 'timestamp')}
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in result.values()):
        raise ValueError('Invalid point')
    if not (-90 <= result['latitude'] <= 90 and -180 <= result['longitude'] <= 180
            and 0 <= result['accuracy'] <= 10000 and now - 120 <= result['timestamp'] <= now + 10):
        raise ValueError('Stale or invalid point')
    return result


class LocationStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS leases (id TEXT PRIMARY KEY, device TEXT, session TEXT, expires REAL, active INTEGER, seq INTEGER DEFAULT -1, point TEXT)')
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def start(self, device, session, duration, now=None):
        now = time.time() if now is None else now
        uuid.UUID(device)
        if not isinstance(session, str) or not session or len(session) > 256 or duration not in (120, 900, 3600):
            raise ValueError('Invalid lease')
        ident = str(uuid.uuid4())
        with self.connect() as db:
            db.execute('DELETE FROM leases WHERE expires < ?', (now - 3600,))
            db.execute('UPDATE leases SET active=0, point=NULL WHERE device=?', (device,))
            db.execute('INSERT INTO leases(id,device,session,expires,active) VALUES(?,?,?,?,1)', (ident,device,session,now+duration))
        return {'id':ident, 'expires_at':now+duration}

    def update(self, ident, seq, point, now=None):
        now = time.time() if now is None else now
        if type(seq) is not int or seq < 0 or not isinstance(point, dict):
            raise ValueError('Invalid update')
        fields = ('latitude', 'longitude', 'accuracy', 'timestamp')
        p = {k: point[k] for k in fields}
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in p.values()):
            raise ValueError('Invalid coordinate')
        if not (-90 <= p['latitude'] <= 90 and -180 <= p['longitude'] <= 180 and 0 <= p['accuracy'] <= 10000 and now-120 <= p['timestamp'] <= now+10):
            raise ValueError('Stale or invalid coordinate')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT active,expires,seq FROM leases WHERE id=?', (ident,)).fetchone()
            if not row or not row[0] or row[1] <= now:
                raise LookupError('Sharing ended')
            if seq > row[2]:
                db.execute('UPDATE leases SET seq=?,point=? WHERE id=?', (seq,json.dumps(p),ident))

    def stop(self, ident):
        with self.connect() as db:
            db.execute('UPDATE leases SET active=0,point=NULL WHERE id=?', (ident,))

    def latest(self, session, resolve=lambda s:s, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            # Expired locations are removed even if the phone cannot send a stop request.
            db.execute('UPDATE leases SET active=0,point=NULL WHERE expires<=?', (now,))
            rows = db.execute('SELECT session,point,expires FROM leases WHERE active=1 AND point IS NOT NULL').fetchall()
        values=[]
        for source, raw, expires in rows:
            if source != session and resolve(source) != session:
                continue
            p=json.loads(raw)
            if now-120 <= p['timestamp'] <= now+10:
                values.append({**p, 'expires_at': expires, 'source': '用户主动共享的 iPhone 位置'})
        return max(values, key=lambda p:p['timestamp']) if values else None


class DeviceStore(LocationStore):
    """Revocable device grants; latest-only samples and independent short requests."""
    def __init__(self, path):
        super().__init__(path)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS devices (
                  id TEXT PRIMARY KEY, grant_id TEXT, token_hash TEXT, session TEXT,
                  name TEXT, platform TEXT, active INTEGER, seq INTEGER DEFAULT 0,
                  point TEXT, registered REAL, push_token TEXT, push_environment TEXT,
                  push_registered REAL);
                CREATE TABLE IF NOT EXISTS device_requests (
                  id TEXT PRIMARY KEY, device TEXT, grant_id TEXT, session TEXT,
                  requested REAL, expires REAL, max_age INTEGER, status TEXT,
                  point TEXT, dedupe TEXT UNIQUE);
            ''')

    @staticmethod
    def _expire(db, now):
        db.execute("UPDATE device_requests SET status='timeout',point=NULL WHERE status='pending' AND expires<=?", (now,))
        # Coordinates are never retained as a history; the grant itself does not expire.
        db.execute("UPDATE devices SET point=NULL WHERE point IS NOT NULL AND CAST(json_extract(point,'$.timestamp') AS REAL)<?", (now - 120,))
        db.execute("UPDATE device_requests SET point=NULL WHERE point IS NOT NULL AND CAST(json_extract(point,'$.timestamp') AS REAL)<?", (now - 120,))
        db.execute('DELETE FROM device_requests WHERE expires<?', (now - 3600,))

    @staticmethod
    def _hash(token):
        if not isinstance(token, str) or not 32 <= len(token) <= 4096:
            raise PermissionError('Invalid device credential')
        return hashlib.sha256(token.encode()).hexdigest()

    def _authorized(self, db, token, grant_id=None):
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT * FROM devices WHERE token_hash=? AND active=1', (self._hash(token),)).fetchone()
        if row is None or (grant_id is not None and row['grant_id'] != grant_id):
            raise PermissionError('Device authorization ended')
        return row

    def authorize(self, device, session, name, platform, now=None):
        now = time.time() if now is None else now
        device = str(uuid.UUID(device))
        if not isinstance(session, str) or not session or len(session) > 256:
            raise ValueError('Invalid session')
        if (not isinstance(name, str) or not 1 <= len(name) <= 128
                or any(ord(c) < 32 or ord(c) == 127 for c in name)
                or platform not in ('iOS', 'watchOS', 'iPadOS', 'macOS')):
            raise ValueError('Invalid device')
        token, grant = secrets.token_urlsafe(32), str(uuid.uuid4())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE device_requests SET status='cancelled',point=NULL WHERE device=?", (device,))
            db.execute('INSERT OR REPLACE INTO devices(id,grant_id,token_hash,session,name,platform,active,seq,registered) VALUES(?,?,?,?,?,?,1,0,?)',
                       (device, grant, self._hash(token), session, name, platform, now))
        return {'device_id': device, 'grant_id': grant, 'device_token': token, 'authorized': True}

    def revoke(self, device):
        device = str(uuid.UUID(device))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE devices SET active=0,token_hash=NULL,point=NULL,push_token=NULL,push_environment=NULL WHERE id=?', (device,))
            db.execute("UPDATE device_requests SET status='cancelled',point=NULL WHERE device=?", (device,))

    @staticmethod
    def _request_json(row):
        return {'id': row['id'], 'requested_at': row['requested'],
                'expires_at': row['expires'], 'max_age_seconds': row['max_age']}

    def state(self, token, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            self._expire(db, now)
            device = self._authorized(db, token)
            requests = db.execute("SELECT * FROM device_requests WHERE device=? AND grant_id=? AND status='pending' ORDER BY requested LIMIT 100",
                                  (device['id'], device['grant_id'])).fetchall()
            return {'device_id': device['id'], 'grant_id': device['grant_id'], 'authorized': True,
                    'latest': json.loads(device['point']) if device['point'] else None,
                    'pending_requests': [self._request_json(row) for row in requests]}

    def observe(self, token, grant, sequence, point, request_id=None, now=None):
        now = time.time() if now is None else now
        point = checked_point(point, now)
        if type(sequence) is not int or sequence < 1 or sequence > 2**63 - 1:
            raise ValueError('Invalid sequence')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._expire(db, now)
            device = self._authorized(db, token, grant)
            req = None
            if request_id is not None:
                req = db.execute('SELECT * FROM device_requests WHERE id=? AND device=? AND grant_id=?',
                                 (request_id, device['id'], grant)).fetchone()
                if req is None or req['expires'] <= now or req['status'] not in ('pending', 'ok'):
                    raise LookupError('Request ended')
                if (req['max_age'] == 0 and point['timestamp'] < req['requested']) or (
                        req['max_age'] > 0 and now - point['timestamp'] > req['max_age']):
                    raise ValueError('Requested freshness not met')
            # Duplicate/out-of-order delivery is acknowledged, but cannot replace a newer observation.
            if sequence > device['seq']:
                old = json.loads(device['point']) if device['point'] else None
                if old is None or point['timestamp'] >= old['timestamp']:
                    db.execute('UPDATE devices SET seq=?,point=? WHERE id=?', (sequence, json.dumps(point), device['id']))
                else:
                    db.execute('UPDATE devices SET seq=? WHERE id=?', (sequence, device['id']))
            if req is not None and req['status'] == 'pending':
                # A replayed lower sequence must not complete a different pending request.
                if sequence <= device['seq']:
                    raise ValueError('Sequence already consumed')
                db.execute("UPDATE device_requests SET status='ok',point=? WHERE id=?", (json.dumps(point), request_id))

    def failure(self, token, grant, request_id, status, now=None):
        now = time.time() if now is None else now
        if status not in FAILURES:
            raise ValueError('Invalid result')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._expire(db, now)
            device = self._authorized(db, token, grant)
            req = db.execute('SELECT * FROM device_requests WHERE id=? AND device=? AND grant_id=?',
                             (request_id, device['id'], grant)).fetchone()
            if req is None or req['expires'] <= now:
                raise LookupError('Request ended')
            if req['status'] == 'pending':
                db.execute('UPDATE device_requests SET status=?,point=NULL WHERE id=?', (status, request_id))
            elif req['status'] != status:
                raise LookupError('Request ended')

    def register_push(self, token, grant, push_token, environment, now=None):
        now = time.time() if now is None else now
        if (not isinstance(push_token, str) or not 2 <= len(push_token) <= 512 or len(push_token) % 2
                or any(c not in '0123456789abcdefABCDEF' for c in push_token)
                or environment not in ('sandbox', 'production')):
            raise ValueError('Invalid push registration')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            device = self._authorized(db, token, grant)
            db.execute('UPDATE devices SET push_token=?,push_environment=?,push_registered=? WHERE id=?',
                       (push_token.lower(), environment, now, device['id']))

    def push_target(self, request_id, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT d.*, r.expires FROM devices d JOIN device_requests r ON r.device=d.id AND r.grant_id=d.grant_id WHERE r.id=? AND d.active=1 AND r.status='pending' AND r.expires>?", (request_id, now)).fetchone()
            return dict(row) if row and row['push_token'] else None

    def invalidate_push(self, device, grant, token, registered, invalidated_at):
        with self.connect() as db:
            db.execute('UPDATE devices SET push_token=NULL,push_environment=NULL WHERE id=? AND grant_id=? AND push_token=? AND push_registered=? AND push_registered<=?',
                       (device, grant, token, registered, invalidated_at if invalidated_at is not None else registered))

    def eligible(self, session, resolve=lambda value: value):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT * FROM devices WHERE active=1 ORDER BY registered DESC,id').fetchall()
        return [dict(row) for row in rows if row['session'] == session or resolve(row['session']) == session]

    def request(self, session, max_age=120, timeout=15, resolve=lambda value: value, dedupe=None, now=None):
        now = time.time() if now is None else now
        if type(max_age) is not int or not 0 <= max_age <= 120 or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 15:
            raise ValueError('Invalid request bounds')
        devices = self.eligible(session, resolve)
        # Never silently pick whichever device happened to upload last.
        phones = [item for item in devices if item['platform'] == 'iOS']
        candidates = phones or [item for item in devices if item['platform'] == 'watchOS']
        if len(candidates) != 1:
            return {'status': 'device_selection_required' if candidates else 'not_authorized'}
        chosen = candidates[0]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.row_factory = sqlite3.Row
            self._expire(db, now)
            device = db.execute('SELECT * FROM devices WHERE id=? AND grant_id=? AND active=1', (chosen['id'], chosen['grant_id'])).fetchone()
            if device is None:
                return {'status': 'not_authorized'}
            point = json.loads(device['point']) if device['point'] else None
            if max_age > 0 and point and -10 <= now - point['timestamp'] <= max_age:
                return self._location(device, point, now, True)
            # Tool retries use host runtime call identity only, never model-provided IDs.
            key = hashlib.sha256((session + ':' + str(dedupe)).encode()).hexdigest() if dedupe else None
            if key:
                old = db.execute('SELECT * FROM device_requests WHERE dedupe=?', (key,)).fetchone()
                if old and old['grant_id'] == device['grant_id']:
                    return {'status': 'pending', **self._request_json(old)}
                if old:
                    db.execute('DELETE FROM device_requests WHERE id=?', (old['id'],))
            # Collapse concurrent equivalent sampling requests for the same device/grant.
            old = db.execute("SELECT * FROM device_requests WHERE device=? AND grant_id=? AND session=? AND status='pending' AND max_age=? AND expires>? ORDER BY requested LIMIT 1",
                             (device['id'], device['grant_id'], session, max_age, now)).fetchone()
            if old:
                return {'status': 'pending', **self._request_json(old)}
            ident = str(uuid.uuid4())
            db.execute("INSERT INTO device_requests(id,device,grant_id,session,requested,expires,max_age,status,dedupe) VALUES(?,?,?,?,?,?,?,'pending',?)",
                       (ident, device['id'], device['grant_id'], session, now, now + timeout, max_age, key))
            return {'status': 'pending', 'id': ident, 'requested_at': now, 'expires_at': now + timeout, 'max_age_seconds': max_age}

    @staticmethod
    def _location(device, point, now, cached):
        return {'status': 'ok', 'device_id': device['id'], 'device_name': device['name'],
                'platform': device['platform'], 'coordinate_system': 'WGS84',
                'point': point, 'age_seconds': max(0, now - point['timestamp']), 'cached': cached}

    def result(self, request_id, session, resolve=lambda value: value, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._expire(db, now)
            db.row_factory = sqlite3.Row
            req = db.execute('SELECT * FROM device_requests WHERE id=?', (request_id,)).fetchone()
            if req is None or (req['session'] != session and resolve(req['session']) != session):
                return {'status': 'not_authorized'}
            device = db.execute('SELECT * FROM devices WHERE id=? AND grant_id=? AND active=1', (req['device'], req['grant_id'])).fetchone()
            if device is None:
                return {'status': 'not_authorized'}
            if req['status'] == 'ok':
                if not req['point']:
                    return {'status': 'timeout'}
                point = json.loads(req['point'])
                if (req['max_age'] > 0 and now - point['timestamp'] > req['max_age']) or now - point['timestamp'] > 120:
                    return {'status': 'timeout'}
                return self._location(device, point, now, False)
            return {'status': req['status']}


def register(ctx):
    from hermes_constants import get_hermes_home
    store = DeviceStore(get_hermes_home() / 'plugin-data' / 'hermes-plus-location' / 'latest.sqlite')
    adapter_ref, loop_ref = [], []
    trusted_api_sessions = set()
    lock = threading.Lock()
    provider_ref = []

    def resolve(session):
        if not adapter_ref:
            return session
        db = adapter_ref[0]._ensure_session_db()
        return db.resolve_resume_session_id(session) if db else session

    def trusted(session):
        if not isinstance(session, str) or not session:
            return False
        with lock:
            return session in trusted_api_sessions

    async def hint(request_id):
        if not provider_ref:
            return
        target = store.push_target(request_id)
        if not target:
            return
        result = await provider_ref[0].send_location_hint(target['push_token'], target['push_environment'], request_id, target['expires'])
        if result.invalidate_token:
            store.invalidate_push(target['id'], target['grant_id'], target['push_token'], target['push_registered'], result.invalidated_at)

    def blocking_tool(args, *, session_id=None, task_id=None, tool_call_id=None, **kwargs):
        if not trusted(session_id) or kwargs.get('platform', 'api_server') != 'api_server':
            return json.dumps({'status': 'not_authorized'})
        try:
            # Hermes dispatch runs tools in worker threads. Never block an event loop,
            # including the HTTP loop needed to receive the location response.
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            return json.dumps({'status': 'unavailable', 'reason': 'tool_worker_required'})
        try:
            request = store.request(session_id, args.get('max_age_seconds', 120), args.get('timeout_seconds', 15),
                                    resolve=resolve, dedupe=(str(task_id) + ':' + str(tool_call_id)) if tool_call_id else None)
            if request['status'] != 'pending':
                return json.dumps(request, ensure_ascii=False)
            if loop_ref and loop_ref[0].is_running():
                asyncio.run_coroutine_threadsafe(hint(request['id']), loop_ref[0])
            deadline = time.monotonic() + min(args.get('timeout_seconds', 15), 15, max(0, request['expires_at'] - time.time()))
            while True:
                result = store.result(request['id'], session_id, resolve)
                if result['status'] != 'pending':
                    return json.dumps(result, ensure_ascii=False)
                if time.monotonic() >= deadline:
                    return json.dumps({'status': 'timeout'})
                time.sleep(min(.1, max(0, deadline - time.monotonic())))
        except (ValueError, TypeError, KeyError):
            return json.dumps({'status': 'invalid_request'})

    async def tool(args, *, session_id=None, task_id=None, tool_call_id=None, **kwargs):
        # The SDK's async bridge can use its own worker loop. Keep SQLite/waiting
        # off that loop as well as the aiohttp loop which receives device results.
        return await asyncio.to_thread(blocking_tool, args, session_id=session_id,
                                       task_id=task_id, tool_call_id=tool_call_id, **kwargs)

    def wire(app, adapter):
        from aiohttp import web
        adapter_ref[:] = [adapter]
        try:
            loop_ref[:] = [asyncio.get_running_loop()]
        except RuntimeError:
            pass
        # The provider is optional; importing the plugin never reads a signing key.
        if hasattr(app, 'on_cleanup'):
            import importlib.util
            spec = importlib.util.spec_from_file_location('hermes_plus_location_apns', Path(__file__).with_name('apns.py'))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            provider_ref[:] = [module.APNsProvider.from_env()]
            async def cleanup(_app):
                await provider_ref[0].aclose()
            app.on_cleanup.append(cleanup)

        async def handler(request):
            path = request.path
            device_path = path.startswith('/v1/hermes-plus/location/device/')
            if not device_path:
                if not adapter._expected_api_key():
                    return web.json_response({'error': 'Authentication unavailable'}, status=503)
                denied = adapter._check_auth(request)
                if denied is not None:
                    return denied
            bearer = request.headers.get('Authorization', '')
            token = bearer[7:] if bearer.startswith('Bearer ') else ''
            try:
                if device_path and request.method == 'GET':
                    return web.json_response(store.state(token))
                if request.method == 'GET':
                    return web.json_response({'version': 1, 'durations': [120,900,3600], 'device_protocol_version': 1, 'on_demand': hasattr(ctx, 'register_tool')})
                if request.method == 'DELETE':
                    if 'device' in request.match_info:
                        store.revoke(request.match_info['device'])
                        return web.json_response({'revoked': True})
                    store.stop(request.match_info['lease'])
                    return web.json_response({'stopped': True})
                if request.content_length is None or request.content_length > 4096:
                    return web.json_response({'error': 'Invalid body size'}, status=413)
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError('Invalid request')
                if device_path:
                    grant = body['grant_id']
                    if path.endswith('/observation'):
                        store.observe(token, grant, body['sequence'], body['point'], body.get('request_id'))
                    elif path.endswith('/results'):
                        store.failure(token, grant, body['request_id'], body['status'])
                    elif path.endswith('/push'):
                        store.register_push(token, grant, body['apns_token'], body['environment'])
                    return web.json_response({'accepted': True})
                if request.method == 'POST':
                    db = adapter._ensure_session_db()
                    if db is None or db.get_session(body['session_id']) is None:
                        return web.json_response({'error': 'Unknown session'}, status=404)
                    if path.endswith('/devices'):
                        return web.json_response(store.authorize(body['device_id'], body['session_id'], body['display_name'], body['platform']))
                    return web.json_response(store.start(body['device_id'], body['session_id'], body['duration']))
                store.update(request.match_info['lease'], body['sequence'], body['point'])
                return web.json_response({'accepted': True})
            except PermissionError:
                return web.json_response({'error': 'Device authorization ended'}, status=403)
            except (ValueError, TypeError, KeyError):
                return web.json_response({'error': 'Invalid location request'}, status=400)
            except LookupError:
                return web.json_response({'error': 'Request or sharing ended'}, status=410)

        prefix = '/v1/hermes-plus/location'
        app.router.add_get(prefix, handler)
        app.router.add_post(prefix, handler)
        app.router.add_post(prefix + '/devices', handler)
        app.router.add_delete(prefix + '/devices/{device}', handler)
        app.router.add_get(prefix + '/device/state', handler)
        app.router.add_put(prefix + '/device/observation', handler)
        app.router.add_post(prefix + '/device/results', handler)
        app.router.add_post(prefix + '/device/push', handler)
        app.router.add_put(prefix + '/{lease}', handler)
        app.router.add_delete(prefix + '/{lease}', handler)

    def context(**kwargs):
        session = kwargs.get('session_id')
        if kwargs.get('platform') != 'api_server':
            # Fail closed even if a host reuses a previously API-bound session ID
            # on another channel; no model argument can restore this binding.
            with lock:
                trusted_api_sessions.discard(session)
            return None
        if not adapter_ref or not session:
            return None
        db = adapter_ref[0]._ensure_session_db()
        if db is None:
            return None
        with lock:
            trusted_api_sessions.add(session)
        if store.eligible(session, db.resolve_resume_session_id):
            return {'context': '用户已授权按需读取随身设备位置。仅在当前任务需要位置时调用 get_user_location；不得用历史对话坐标代替当前定位。工具可能返回设备离线、权限不足或超时。'}
        point = store.latest(session, resolve=db.resolve_resume_session_id)
        if point:
            return {'context': '用户主动共享的最新设备位置（WGS84）；仅代表采集时刻，精度单位米。不可假定这是持续不变的位置。\n' + json.dumps(point, ensure_ascii=False)}
        return {'context': '目前没有新鲜、有效的设备定位。不要把历史对话中的坐标当作用户当前位置。'}

    ctx.register_platform_handler('api_server', wire)
    ctx.register_hook('pre_llm_call', context)
    if hasattr(ctx, 'register_tool'):
        ctx.register_tool(name='get_user_location', toolset='hermes_plus_location',
                          schema={'name': 'get_user_location', 'description': 'Read the authorized user-carried device location when needed for the current request. Returns the source, capture time and accuracy, or an explicit unavailable status. Does not ask the user to send a chat message.',
                                  'parameters': {'type': 'object', 'properties': {
                                      'max_age_seconds': {'type': 'integer', 'minimum': 0, 'maximum': 120, 'default': 120, 'description': 'Maximum cached age. Zero requires a new sample taken after this request.'},
                                      'timeout_seconds': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 15, 'default': 15}},
                                      'additionalProperties': False}}, handler=tool, is_async=True)
