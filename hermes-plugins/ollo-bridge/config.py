"""Profile-local, explicit configuration; loading never creates files."""
from dataclasses import dataclass, field
import ipaddress
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from .protocol import loads, require
from .apple_config import names, scopes, knowledge


@dataclass(frozen=True)
class Config:
    session_id: str
    gateway_url: str
    key: str = field(repr=False)
    jobs: dict = field(default_factory=dict)
    agenda_jobs: dict = field(default_factory=dict)
    helper_path: str = ''
    name: str = 'Ollo'
    apple: dict = field(default_factory=names)
    collection: dict = field(default_factory=scopes)
    knowledge: dict = field(default_factory=knowledge)


def read_config(home):
    path = home / 'ollo/bridge.json'
    if not path.exists():
        return None
    value = loads(path.read_bytes())
    require(isinstance(value, dict))
    require(value.keys() <= {'session_id', 'gateway_url', 'jobs', 'agenda_jobs', 'helper_path', 'name', 'apple', 'collection', 'knowledge'})
    apple, collection = names(value.get('apple')), scopes(value.get('collection'))
    require(isinstance(value.get('session_id'), str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,256}', value['session_id']))
    parsed = urlsplit(value.get('gateway_url', ''))
    require(parsed.scheme == 'http' and parsed.hostname in {'127.0.0.1', '::1'})
    require(ipaddress.ip_address(parsed.hostname).is_loopback and parsed.port is not None)
    require(not parsed.username and not parsed.password and parsed.path in {'', '/'} and not parsed.query and not parsed.fragment)
    jobs = value.get('jobs')
    require(isinstance(jobs, dict) and 1 <= len(jobs) <= 32)
    for job, entry in jobs.items():
        require(isinstance(job, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', job))
        require(isinstance(entry, dict) and set(entry) == {'title', 'kind'})
        require(entry['kind'] in {'agenda', 'inbox', 'review'})
        require(isinstance(entry['title'], str) and 1 <= len(entry['title'].strip()) <= 256)
    agenda = value.get('agenda_jobs', {})
    require(isinstance(agenda, dict) and set(agenda) == {'weekday', 'weekend'})
    require(all(job in jobs and jobs[job]['kind'] == 'agenda' for job in agenda.values()))
    helper = value.get('helper_path', '')
    require(isinstance(helper, str) and (not helper or Path(helper).is_absolute()))
    name = value.get('name', 'Ollo')
    require(isinstance(name, str) and 1 <= len(name.strip()) <= 100)
    key_file = home / 'ollo/bridge.key'
    require(stat.S_IMODE(key_file.stat().st_mode) & 0o077 == 0, 'not_configured', 503)
    key = key_file.read_text(encoding='utf-8').strip()
    require(32 <= len(key) <= 512 and all(33 <= ord(c) <= 126 for c in key), 'not_configured', 503)
    return Config(value['session_id'], value['gateway_url'].rstrip('/'), key, jobs, agenda, helper, name, apple, collection,
                  knowledge(value.get('knowledge')))
