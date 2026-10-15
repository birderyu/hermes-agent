"""Private, offline transaction storage for the Ollo production switch.

No plugin imports: importing the plugin would read its bridge key. Backups are
ordinary files plus a hash manifest, not executable pickle or shell fragments.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile


ROOTS = ('cron/jobs.json', 'ollo/bridge.json', 'skills/gtd', 'skills/gtd-ollo-test')
FORBIDDEN = {'.env', 'auth.json', 'bridge.key'}


class SwitchError(Exception):
    """Only fixed, non-sensitive diagnostics belong in this exception."""


def require(condition, message):
    if not condition:
        raise SwitchError(message)


def safe_path(path):
    path = Path(path).absolute()
    require('..' not in path.parts, 'parent_traversal_refused')
    for part in (path, *path.parents):
        require(not part.is_symlink(), 'symlink_refused')
    require(not FORBIDDEN.intersection(path.parts), 'secret_path_refused')
    return path


def digest(data):
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    return json.loads(safe_path(path).read_bytes().decode('utf-8-sig'), object_pairs_hook=unique)


def snapshot(home, roots=ROOTS):
    """None records directories, including empty ones. Refuse links/special files."""
    result = {}
    for root in roots:
        path = safe_path(home / root)
        if not path.exists():
            continue
        pending = [path]
        while pending:
            entry = safe_path(pending.pop())
            mode = entry.stat().st_mode
            relative = entry.relative_to(home).as_posix()
            if stat.S_ISDIR(mode):
                result[relative] = None
                pending.extend(entry.iterdir())
            else:
                require(stat.S_ISREG(mode), 'special_file_refused')
                result[relative] = entry.read_bytes()
    return result


def fingerprints(entries):
    return {name: None if data is None else digest(data) for name, data in sorted(entries.items())}


def file_modes(home, entries):
    return {name: stat.S_IMODE(safe_path(home / name).stat().st_mode) for name in entries}


def target_modes(home, entries):
    result = {}
    for name, data in entries.items():
        source = name.replace('skills/gtd/', 'skills/gtd-ollo-test/', 1)
        executable = data is not None and bool(safe_path(home / source).stat().st_mode & 0o100)
        result[name] = 0o700 if data is None or executable else 0o600
    return result


def atomic_write(path, data, *, staging_dir=None):
    path = safe_path(path)
    fd, temporary = tempfile.mkstemp(prefix='.ollo-switch-', dir=staging_dir or path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def restore_tree(home, desired, current, modes, backup):
    """Use the same routine for forward writes and crash recovery."""
    for name in sorted(desired, key=lambda p: (p.count('/'), p)):
        path = safe_path(home / name)
        data = desired[name]
        if data is None:
            path.mkdir(mode=0o700, exist_ok=True)
            path.chmod(0o700)
        elif current.get(name) != data:
            # A SIGKILL can leave a partial temp file. Keep it in the private
            # backup, outside the Skill trees that recovery compares for drift.
            atomic_write(path, data, staging_dir=backup)
    for name in sorted(current.keys() - desired.keys(), key=lambda p: (-p.count('/'), p)):
        path = safe_path(home / name)
        if current[name] is None:
            path.rmdir()
        else:
            path.unlink()
    for name, mode in modes.items():
        safe_path(home / name).chmod(mode)


def prepare_backup(home, backup, before, after, *, accepted_prompt_drift=None):
    backup = safe_path(backup)
    require(not backup.exists(), 'backup_dir_must_be_new')
    require(not any(backup.is_relative_to(home / root) for root in ROOTS), 'backup_inside_target')
    device = safe_path(backup.parent).stat().st_dev
    require(all(safe_path(home / root).stat().st_dev == device for root in ROOTS), 'backup_must_share_filesystem')
    backup.mkdir(mode=0o700)  # Parent must already exist; never chmod an unrelated parent.
    (backup / 'before').mkdir(mode=0o700)
    saved = dict(before)
    saved['config.yaml'] = safe_path(home / 'config.yaml').read_bytes()
    for name, data in sorted(saved.items(), key=lambda pair: (pair[0].count('/'), pair[0])):
        path = backup / 'before' / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if data is None:
            path.mkdir(mode=0o700, exist_ok=True)
        else:
            atomic_write(path, data)
    manifest = {'version': 1, 'home': str(home), 'status': 'prepared',
                'accepted_prompt_drift': accepted_prompt_drift or {},
                'before': fingerprints(saved), 'after': fingerprints(after),
                'modes_before': file_modes(home, before), 'modes_after': target_modes(home, after)}
    atomic_write(backup / 'manifest.json', json_bytes(manifest))
    # No target is touched until the entire backup has been re-read and verified.
    load_backup(home, backup)
    return manifest


def load_backup(home, backup):
    backup = safe_path(backup)
    require(stat.S_IMODE(backup.stat().st_mode) == 0o700, 'backup_permissions')
    require(stat.S_IMODE(safe_path(backup / 'manifest.json').stat().st_mode) == 0o600, 'backup_permissions')
    manifest = read_json(backup / 'manifest.json')
    require(manifest.get('version') == 1 and manifest.get('home') == str(home), 'backup_home_mismatch')
    saved = snapshot(backup / 'before', (*ROOTS, 'config.yaml'))
    require(fingerprints(saved) == manifest['before'], 'backup_hash_mismatch')
    for name in saved:
        mode = stat.S_IMODE((backup / 'before' / name).stat().st_mode)
        require(mode == (0o700 if saved[name] is None else 0o600), 'backup_permissions')
    saved.pop('config.yaml')  # Copy-only: this switch never changes config.yaml.
    return manifest, saved


def recover(home, backup):
    manifest, before = load_backup(home, backup)
    current = snapshot(home)
    current_hashes = fingerprints(current)
    # A crash may leave any subset of old/new files. Unrelated edits are not ours to undo.
    for name, value in current_hashes.items():
        allowed = (manifest['before'].get(name, 'missing'), manifest['after'].get(name, 'missing'))
        require(value in allowed, 'rollback_drift_detected')
    require(set(manifest['modes_before']) == set(before), 'backup_modes_mismatch')
    require(all(isinstance(mode, int) and 0 <= mode <= 0o777 for mode in manifest['modes_before'].values()),
            'backup_modes_invalid')
    require(home.stat().st_dev == backup.stat().st_dev, 'backup_must_share_filesystem')
    restore_tree(home, before, current, manifest['modes_before'], backup)
    require(snapshot(home) == before, 'rollback_verification_failed')
    manifest['status'] = 'rolled_back'
    atomic_write(backup / 'manifest.json', json_bytes(manifest))


@contextmanager
def mutation_lock(home):
    """Match cron.jobs' lock path, but never degrade to unlocked writes."""
    path = safe_path(home / 'cron/.jobs.lock')
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SwitchError('cron_store_busy') from exc
        yield
    finally:
        os.close(fd)
