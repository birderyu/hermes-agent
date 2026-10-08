"""Startup migration for a drained gateway; packaged with each standalone plugin."""
import fcntl
import logging
from pathlib import Path
import shutil
import uuid

logger = logging.getLogger(__name__)


def data_directory(home, kind):
    parent = Path(home) / 'plugin-data'
    target = parent / ('ollo-' + kind)
    legacy = parent / ('hermes-plus-' + kind)
    if target.exists() or not legacy.exists():
        return target
    try:
        # Cron workers may discover the same plugin while the gateway is starting.
        # Serialize the check/copy/rename so they all open the same database.
        with (parent / ('.ollo-' + kind + '-migration.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if target.exists() or not legacy.exists():
                return target
            backup = parent / (legacy.name + '.backup-' + uuid.uuid4().hex)
            partial = backup.with_name(backup.name + '.partial')
            shutil.copytree(legacy, partial, symlinks=True)
            partial.rename(backup)
            legacy.rename(target)
            logger.info('Migrated %s to %s; backup: %s', legacy, target, backup)
            return target
    except (OSError, shutil.Error) as exc:
        # Keep the source on any failure, including a failed/incomplete backup.
        # Never open a fresh empty database merely because migration was denied.
        logger.warning('Ollo %s migration failed (%s); continuing with %s',
                       kind, type(exc).__name__, legacy)
        return legacy
