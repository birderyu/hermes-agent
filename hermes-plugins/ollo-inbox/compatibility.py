"""One explicit, reviewable Hermes compatibility patch. Never runs at plugin import.

Adds the existing durable cron execution ID to live-adapter delivery metadata.
Fails closed if upstream has changed the known statement. Backup before --apply.
"""
import argparse
from pathlib import Path

OLD = 'route_metadata = {"job_id": job["id"], "notify": t.notify_delivery}'
NEW = 'route_metadata = {"job_id": job["id"], "execution_id": job.get("execution_id"), "notify": t.notify_delivery}'


def patched(source):
    if NEW in source and OLD not in source:
        return source
    if source.count(OLD) != 1:
        raise ValueError('Unsupported Hermes delivery source; review before applying')
    return source.replace(OLD, NEW)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('hermes_repo', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    path = args.hermes_repo / 'cron/scheduler_delivery.py'
    before = path.read_text()
    after = patched(before)
    compile(after, str(path), 'exec')
    if args.apply and after != before:
        backup = path.with_suffix('.py.hermes-plus-inbox.bak')
        with backup.open('x') as stream:
            stream.write(before)
        path.write_text(after)
    print('already compatible' if after == before else 'patch applied' if args.apply else 'patch ready; source unchanged')
