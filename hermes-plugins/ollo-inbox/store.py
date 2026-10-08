"""An inbox of reports, not a second task database. SQLite is the delivery authority."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid


def identifier(value):
    if not isinstance(value, str):
        raise ValueError('Invalid identifier')
    return str(uuid.UUID(value))


class InboxStore:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS reports (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    session TEXT NOT NULL, job TEXT NOT NULL, execution TEXT NOT NULL,
                    title TEXT NOT NULL, body TEXT NOT NULL, created_at REAL NOT NULL,
                    UNIQUE(session, job, execution));
                CREATE INDEX IF NOT EXISTS reports_session ON reports(session, sequence);
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY, session TEXT NOT NULL, token TEXT, environment TEXT,
                    enabled INTEGER NOT NULL, generation INTEGER NOT NULL, registered REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS pushes (
                    report TEXT NOT NULL, device TEXT NOT NULL, generation INTEGER NOT NULL,
                    status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL, read_at REAL,
                    PRIMARY KEY(report, device, generation));
            ''')
        os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def report(row):
        return {k: row[k] for k in ('id', 'sequence', 'title', 'body', 'created_at')}

    def deliver(self, session, job, execution, title, body):
        execution = identifier(execution)
        if not isinstance(body, str) or not body.strip() or len(body.encode()) > 128_000:
            raise ValueError('Invalid report')
        ident = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([session, job, execution])))
        now = self.clock()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM reports WHERE id=?', (ident,)).fetchone()
            if old:
                if old['body'] != body or old['title'] != title:
                    raise ValueError('Delivery identity conflict')
                return self.report(old)
            db.execute('INSERT INTO reports(id,session,job,execution,title,body,created_at) VALUES(?,?,?,?,?,?,?)',
                       (ident, session, job, execution, title, body, now))
            db.execute('''INSERT INTO pushes(report,device,generation,status,next_attempt)
                       SELECT ?,id,generation,'pending',? FROM devices WHERE session=? AND enabled=1''',
                       (ident, now, session))
            return self.report(db.execute('SELECT * FROM reports WHERE id=?', (ident,)).fetchone())

    def list(self, session, after=0, limit=100, before=None):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid cursor')
        if before is not None and (type(before) is not int or before < 1):
            raise ValueError('Invalid cursor')
        with self.connect() as db:
            if before is not None:
                rows = db.execute('SELECT * FROM reports WHERE session=? AND sequence<? ORDER BY sequence DESC LIMIT ?',
                                  (session, before, limit)).fetchall()
                return [self.report(r) for r in reversed(rows)]
            return [self.report(r) for r in db.execute(
                'SELECT * FROM reports WHERE session=? AND sequence>? ORDER BY sequence LIMIT ?', (session, after, limit))]

    def get(self, session, ident):
        with self.connect() as db:
            row = db.execute('SELECT * FROM reports WHERE id=? AND session=?', (identifier(ident), session)).fetchone()
            if row is None:
                raise LookupError('Unknown report')
            return self.report(row)

    def register(self, device, session, token, environment):
        device = identifier(device)
        if (not isinstance(token, str) or not re.fullmatch(r'[0-9a-fA-F]{2,512}', token)
                or len(token) % 2 or environment not in ('sandbox', 'production')):
            raise ValueError('Invalid push registration')
        token = token.lower()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM devices WHERE id=?', (device,)).fetchone()
            if old and old['session'] != session:
                raise PermissionError('Device belongs to another conversation')
            if old and old['enabled'] and (old['token'], old['environment']) == (token, environment):
                return
            generation = old['generation'] + 1 if old else 1
            db.execute("UPDATE pushes SET status='cancelled' WHERE device=? AND status IN ('pending','sending')", (device,))
            db.execute('INSERT OR REPLACE INTO devices VALUES(?,?,?,?,1,?,?)',
                       (device, session, token, environment, generation, self.clock()))

    def revoke(self, device, session):
        device = identifier(device)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT session FROM devices WHERE id=?', (device,)).fetchone()
            if row and row['session'] != session:
                raise PermissionError('Device belongs to another conversation')
            db.execute('UPDATE devices SET enabled=0,token=NULL,generation=generation+1 WHERE id=?', (device,))
            db.execute("UPDATE pushes SET status='cancelled' WHERE device=? AND status IN ('pending','sending')", (device,))

    def read(self, session, ident, device):
        self.get(session, ident)
        with self.connect() as db:
            db.execute("UPDATE pushes SET read_at=?,status='read' WHERE report=? AND device=?",
                       (self.clock(), identifier(ident), identifier(device)))

    def claim(self):
        now = self.clock()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE pushes SET status='expired' WHERE status IN ('pending','sending') AND report IN (SELECT id FROM reports WHERE created_at<?)", (now - 86400,))
            row = db.execute('''SELECT p.*,d.token,d.environment,d.registered FROM pushes p
                       JOIN devices d ON d.id=p.device AND d.generation=p.generation AND d.enabled=1
                       WHERE p.status IN ('pending','sending') AND p.next_attempt<=? AND p.attempts<5
                       ORDER BY p.next_attempt LIMIT 1''', (now,)).fetchone()
            if row is None:
                return None
            item = dict(row)
            item['attempts'] += 1
            db.execute("UPDATE pushes SET status='sending',attempts=?,next_attempt=? WHERE report=? AND device=? AND generation=?",
                       (item['attempts'], now + 60, item['report'], item['device'], item['generation']))
            return item

    def finish(self, item, result):
        transient = result.status in ('transport_error', 'retry_later', 'credentials_unavailable', 'not_configured')
        state = 'pending' if transient and item['attempts'] < 5 else result.status
        with self.connect() as db:
            db.execute("UPDATE pushes SET status=?,next_attempt=? WHERE report=? AND device=? AND generation=? AND status='sending' AND attempts=?",
                       (state, self.clock() + min(900, 30 * 2 ** item['attempts']), item['report'], item['device'], item['generation'], item['attempts']))
            if result.invalidate_token:
                # A stale APNs response must not revoke a newly rotated token.
                db.execute('UPDATE devices SET enabled=0,token=NULL WHERE id=? AND generation=? AND token=? AND registered<=?',
                           (item['device'], item['generation'], item['token'], result.invalidated_at or item['registered']))
