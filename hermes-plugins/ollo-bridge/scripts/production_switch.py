"""Offline GTD cutover. Default: read-only plan; see the dated custom record.

Run in the Hermes environment with an explicit HERMES_HOME. Do not import
config.read_config, cron.jobs.load_jobs, or plugin registration here: the first
reads a key; the second can repair/write a store even during a purported plan.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timedelta
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from production_switch_io import (SwitchError, atomic_write, digest, json_bytes,
    load_backup, mutation_lock, prepare_backup, read_json, recover, require,
    restore_tree, safe_path, snapshot)


PLUGIN = Path(__file__).resolve().parent.parent
JOBS = (
    ('d83281b75902', '36c04d45e58c', 'GTD-今日安排-工作日', '0 8 * * 1-5', 'agenda', '今日安排'),
    ('8211bb91402a', '4eabdf7d938b', 'GTD-今日安排-周末', '30 9 * * 6,0', 'agenda', '今日安排'),
    ('12d6987e974e', '188ed50c0816', 'GTD-清理收件箱-工作日', '0 21 * * 1-5', 'inbox', '清理收件箱'),
    ('f9a5dd70ea34', 'ec742022406f', 'GTD-每周回顾-周日', '0 21 * * 0', 'review', '每周回顾'),
)
SKILL = 'skills/gtd/SKILL.md'
TEST_SKILL = 'skills/gtd-ollo-test/SKILL.md'


def apple_module():
    spec = importlib.util.spec_from_file_location('switch_apple_names', PLUGIN / 'apple_config.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidates():
    docs = PLUGIN / 'docs'
    for filename in ('cron-prompts-candidate.md', 'gtd-skill-candidate.md', 'gtd-skill-update-candidate.md'):
        require(safe_path(docs / filename).is_file(), 'candidate_missing')
    prompts = re.findall(r'^````text\n(.*?)\n````$',
                         (docs / 'cron-prompts-candidate.md').read_text(), re.M | re.S)
    require(len(prompts) == 4, 'candidate_prompt_count')
    return prompts


def check_production_skill(text):
    skill_header(text, 'gtd')
    require('重点' in text and '网关' in text and 'focus_confirmed' in text and
            '仅供测试' not in text and 'gtd-ollo-test' not in text, 'production_skill_drift')


def skill_header(text, expected):
    match = re.match(r'\A---\r?\n(.*?)\r?\n---\r?\n', text, re.S)
    require(match is not None, 'skill_frontmatter_missing')
    fields = match.group(1)
    names = re.findall(r'^name: *(.+)$', fields, re.M)
    require(names == [expected], 'skill_name_mismatch')
    description = re.search(r'^description: *(.+(?:\n[ \t]+[^\n]*)*)', fields, re.M)
    require(description is not None, 'skill_description_missing')
    return match, description


def production_skill(original, tested):
    _, description = skill_header(original, 'gtd')
    header, test_description = skill_header(tested, 'gtd-ollo-test')
    fields = header.group(1)
    fields = fields[:test_description.start()] + description.group(0) + fields[test_description.end():]
    fields = re.sub(r'^name: *gtd-ollo-test$', 'name: gtd', fields, flags=re.M)
    body = tested[header.end():]
    # No rewriting of business rules: remove only this literal label and the test slug.
    body = body.replace('gtd-ollo-test', 'gtd').replace('（仅供测试）', '').replace('仅供测试', '')
    result = '---\n' + fields + '\n---\n' + body
    require('重点' in body and '网关' in body and 'focus_confirmed' in body,
            'tested_skill_missing_A7_review_required')
    require('Ollo 测试·' not in result, 'tested_skill_has_physical_test_names')
    return result.encode('utf-8')


def check_timezone(home):
    # Read only the top-level timezone scalar; never deserialize/emit the other settings.
    with safe_path(home / 'config.yaml').open() as stream:
        zones = [line.rstrip('\r\n') for line in stream if line.startswith('timezone:')]
    require(len(zones) == 1, 'timezone_must_be_Asia_Shanghai')
    scalar = zones[0].partition(':')[2].split('#', 1)[0].strip()
    require(scalar in ('Asia/Shanghai', '"Asia/Shanghai"', "'Asia/Shanghai'"), 'timezone_must_be_Asia_Shanghai')
    require(os.environ.get('HERMES_TIMEZONE', 'Asia/Shanghai') == 'Asia/Shanghai', 'timezone_env_conflict')


def check_bridge(bridge, production):
    require(isinstance(bridge, dict) and bridge.keys() <= {
        'session_id', 'name', 'gateway_url', 'helper_path', 'jobs', 'agenda_jobs', 'apple', 'collection'},
        'bridge_shape')
    require(isinstance(bridge.get('session_id'), str) and
            re.fullmatch(r'[A-Za-z0-9_.-]{1,256}', bridge['session_id']), 'bridge_session')
    url = urlsplit(bridge.get('gateway_url', ''))
    require(url.scheme == 'http' and url.hostname in {'127.0.0.1', '::1'} and url.port and
            not url.username and not url.password and url.path in {'', '/'} and
            not url.query and not url.fragment, 'bridge_gateway_url')
    require(isinstance(bridge.get('name', 'Ollo'), str) and
            1 <= len(bridge.get('name', 'Ollo').strip()) <= 100, 'bridge_name')
    apple = apple_module()
    expected = apple.names() if production else {
        key: {name: 'Ollo 测试·' + name for name in values}
        for key, values in apple.names().items()}
    require(apple.names(bridge.get('apple')) == expected, 'apple_mapping_drift')
    require(not production or 'apple' not in bridge, 'production_apple_override')
    require(apple.scopes(bridge.get('collection')) == apple.scopes(), 'collection_scope_drift')
    index = 0 if production else 1
    ids = [row[index] for row in JOBS]
    require(set(bridge.get('jobs', {})) == set(ids), 'bridge_jobs_drift')
    for row in JOBS:
        entry = bridge['jobs'][row[index]]
        require(isinstance(entry, dict) and set(entry) == {'kind', 'title'} and
                entry['kind'] == row[4] and isinstance(entry['title'], str) and
                1 <= len(entry['title'].strip()) <= 256, 'bridge_job_entry')
    require(bridge.get('agenda_jobs') == dict(zip(('weekday', 'weekend'), ids[:2])), 'agenda_jobs_drift')
    helper = bridge.get('helper_path', '')
    require(isinstance(helper, str) and Path(helper).is_absolute(), 'helper_path_required')
    require(safe_path(helper).is_file() and os.access(helper, os.X_OK), 'helper_missing_or_not_executable')


def check_job(job, row, *, tested=False, production=False):
    jid = row[1] if tested else row[0]
    prefix = 'Ollo测试-' if tested else ''
    require(job.get('name') == prefix + row[2], jid + ':name_drift')
    schedule = job.get('schedule', {})
    require(schedule.get('kind') == 'cron' and schedule.get('expr') == row[3], jid + ':schedule_drift')
    require(job.get('enabled') is True and job.get('state', 'scheduled') == 'scheduled', jid + ':not_enabled')
    for field in ('paused_at', 'paused_reason', 'pending_slot', 'run_claim', 'fire_claim', 'manual_run_at'):
        require(not job.get(field), jid + ':active_or_pending:' + field)
    for field in ('no_agent', 'context_from', 'workdir', 'origin', 'monitor_script', 'monitor_url', 'base_url', 'interpreter'):
        require(not job.get(field), jid + ':unexpected:' + field)
    require(isinstance(job.get('repeat'), dict) and job['repeat'].get('times') is None, jid + ':repeat_drift')
    require(isinstance(job.get('prompt'), str) and bool(job['prompt'].strip()), jid + ':prompt_missing')
    require(isinstance(job.get('next_run_at'), str) and
            datetime.fromisoformat(job['next_run_at']).tzinfo is not None, jid + ':next_run_invalid')
    is_ollo = tested or production
    skills = ['gtd-ollo-test' if tested else 'gtd', 'hermes-plus-gtd'] if is_ollo else ['gtd']
    expected = {'deliver': 'ollo:main' if is_ollo else 'photon', 'skills': skills,
                'script': 'ollo_collect_' + row[4] + '.py' if is_ollo else None,
                'enabled_toolsets': [] if is_ollo else None,
                'attach_to_session': False if is_ollo else None}
    for field, value in expected.items():
        require(job.get(field) == value and (value is not False or job.get(field) is False), jid + ':' + field + '_drift')
    require(job.get('skill') in (None, skills[0]), jid + ':legacy_skill_drift')
    require(job.get('failure_deliver') in (None, '', expected['deliver']), jid + ':failure_deliver_drift')


def check_scripts(home):
    for filename in ('ollo_collect.py', 'ollo_collect_agenda.py', 'ollo_collect_inbox.py', 'ollo_collect_review.py'):
        require(safe_path(home / 'scripts' / filename).read_bytes() == (PLUGIN / 'scripts' / filename).read_bytes(),
                'installed_script_drift:' + filename)
    for filename in ('collector.py', 'apple_config.py'):
        require(safe_path(home / 'plugins/ollo-bridge' / filename).read_bytes() == (PLUGIN / filename).read_bytes(),
                'installed_collector_drift:' + filename)


def change(jid, field, old, new):
    if old != new:
        if field == 'prompt':
            old, new = digest(old.encode()), digest(new.encode())
        print(json.dumps({'id': jid, 'field': field, 'before': old, 'after': new}, ensure_ascii=False))


def parse_prompt_drift(values):
    accepted = {}
    test_ids = {row[1] for row in JOBS}
    for value in values:
        jid, separator, sha256 = value.partition('=')
        require(separator and jid in test_ids and re.fullmatch(r'[0-9a-f]{64}', sha256),
                'accept_prompt_drift_requires_test_ID_and_lowercase_SHA256')
        require(jid not in accepted or accepted[jid] == sha256, 'conflicting_prompt_drift_acceptance')
        accepted[jid] = sha256
    return accepted


def check_prompt(jid, prompt, candidate, allowances):
    # Comparison still ignores surrounding whitespace; consent binds the exact stored text.
    current_hash, candidate_hash = digest(prompt.encode()), digest(candidate.strip().encode())
    mismatch = prompt.strip() != candidate.strip()
    accepted = mismatch and allowances.get(jid) == current_hash['sha256']
    print(json.dumps({'id': jid, 'field': 'prompt_vs_candidate', 'current': current_hash,
                      'candidate': candidate_hash, 'accepted_drift': accepted}))
    if jid in allowances:
        require(allowances[jid] == current_hash['sha256'], jid + ':prompt_acceptance_hash_mismatch')
    require(not mismatch or accepted, jid + ':prompt_candidate_mismatch')
    if accepted:
        return {'current_sha256': current_hash['sha256'], 'candidate_sha256': candidate_hash['sha256']}
    return None


def build_plan(home, accept_prompt_drift=None):
    allowances, accepted_drift = accept_prompt_drift or {}, {}
    check_timezone(home)
    prompts = candidates()
    before = snapshot(home)
    store = read_json(home / 'cron/jobs.json')
    require(isinstance(store, dict) and isinstance(store.get('jobs'), list), 'jobs_store_shape')
    rows = store['jobs']
    require(all(isinstance(job, dict) and isinstance(job.get('id'), str) for job in rows), 'job_shape')
    by_id = {job['id']: job for job in rows}
    require(len(by_id) == len(rows), 'duplicate_job_id')
    real_ids, test_ids = {row[0] for row in JOBS}, {row[1] for row in JOBS}
    require(real_ids <= by_id.keys(), 'production_job_missing')
    production = not (test_ids & by_id.keys())
    require(production or test_ids <= by_id.keys(), 'test_job_missing_or_partial_switch')
    relevant = [by_id[jid] for jid in real_ids | (set() if production else test_ids)]
    models = {job.get('model') for job in relevant}
    require(len(models) == 1 and all(isinstance(model, str) and model for model in models), 'model_drift')
    require(len({job.get('provider') for job in relevant}) == 1, 'provider_drift')
    bridge = read_json(home / 'ollo/bridge.json')
    check_bridge(bridge, production)
    check_scripts(home)
    skill_header(safe_path(home / 'skills/hermes-plus-gtd/SKILL.md').read_text(), 'hermes-plus-gtd')
    after, updated = dict(before), deepcopy(store)
    for index, row in enumerate(JOBS):
        current = by_id[row[0]]
        check_job(current, row, production=production)
        print(json.dumps({'id': row[0], 'preserve': {'name': row[2], 'cron': row[3],
            'model': current['model'], 'provider': current.get('provider'),
            'next_run_at': datetime.fromisoformat(current['next_run_at']).isoformat()}}, ensure_ascii=False))
        if not production:
            tested = by_id[row[1]]
            check_job(tested, row, tested=True)
            accepted = check_prompt(row[1], tested['prompt'], prompts[index], allowances)
            if accepted:
                accepted_drift[row[1]] = accepted
        target = {'deliver': 'ollo:main', 'skills': ['gtd', 'hermes-plus-gtd'], 'skill': 'gtd',
                  'script': 'ollo_collect_' + row[4] + '.py', 'enabled_toolsets': [],
                  'attach_to_session': False, 'prompt': prompts[index].strip()}
        if current.get('failure_deliver'):
            target['failure_deliver'] = 'ollo:main'
        for field, value in target.items():
            if production:
                require(current.get(field) == value, row[0] + ':production_drift:' + field)
            change(row[0], field, current.get(field), value)
        for job in updated['jobs']:
            if job['id'] == row[0]:
                job.update(target)
    if production:
        require(TEST_SKILL not in before and not (home / 'skills/gtd-ollo-test').exists(), 'test_skill_remains')
        check_production_skill(before[SKILL].decode())
        print('state=production; changes=0; external_gateway_not_checked')
        return before, before, accepted_drift
    require(SKILL in before and TEST_SKILL in before, 'skill_missing')
    final_skill = production_skill(before[SKILL].decode(), before[TEST_SKILL].decode())
    for name in list(after):
        if name == 'skills/gtd' or name.startswith('skills/gtd/') or name.startswith('skills/gtd-ollo-test'):
            del after[name]
    for name, data in before.items():
        if name == 'skills/gtd-ollo-test' or name.startswith('skills/gtd-ollo-test/'):
            after[name.replace('skills/gtd-ollo-test', 'skills/gtd', 1)] = data
    after[SKILL] = final_skill
    print(json.dumps({'id': 'gtd', 'field': 'skill_content',
        'before': digest(before[SKILL]), 'tested': digest(before[TEST_SKILL]), 'after': digest(final_skill)}))
    updated['jobs'] = [job for job in updated['jobs'] if job['id'] not in test_ids]
    for jid in sorted(test_ids):
        change(jid, 'exists', True, False)
    target_bridge = deepcopy(bridge)
    target_bridge['jobs'] = {row[0]: deepcopy(bridge['jobs'][row[1]]) for row in JOBS}
    target_bridge['agenda_jobs'] = {'weekday': JOBS[0][0], 'weekend': JOBS[1][0]}
    del target_bridge['apple']
    for field in ('jobs', 'agenda_jobs'):
        # Titles may be operator text: only print job IDs and kinds, never arbitrary titles.
        old, new = bridge[field], target_bridge[field]
        if field == 'jobs':
            old, new = ({k: v['kind'] for k, v in mapping.items()} for mapping in (old, new))
        change('bridge', field, old, new)
    change('bridge', 'apple', bridge['apple'], None)
    after['cron/jobs.json'], after['ollo/bridge.json'] = json_bytes(updated), json_bytes(target_bridge)
    print('state=ready; remove=skills/gtd-ollo-test; preserve=skills/hermes-plus-gtd,config.yaml; external_gateway_not_checked')
    return before, after, accepted_drift


def check_quiesced(home, acknowledged):
    require(acknowledged, 'requires_--quiesced_after_stopping_all_writers')
    pid_file = safe_path(home / 'gateway.pid')
    if pid_file.exists():
        record = read_json(pid_file)
        pid = record.get('pid') if isinstance(record, dict) else record
        require(isinstance(pid, int) and pid > 0, 'gateway_pid_unverifiable')
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass  # Stale record, not a claim of process identity.
        else:
            raise SwitchError('gateway_pid_still_live')
    path = safe_path(home / 'cron/deliveries.db')
    if path.exists():
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
            count = db.execute("SELECT count(*) FROM deliveries WHERE status IN ('pending','delivering','unknown')").fetchone()[0]
        require(count == 0, 'delivery_queue_not_drained')


def check_window(home, now=None):
    now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    for hour, minute in ((8, 0), (9, 30), (21, 0)):
        slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        require(abs((now - slot).total_seconds()) > 1800, 'outside_30_minute_switch_window_required')
    for job in read_json(home / 'cron/jobs.json')['jobs']:
        if job['id'] not in {jid for row in JOBS for jid in row[:2]}:
            continue
        value = job.get('next_run_at')
        require(isinstance(value, str), 'next_run_missing')
        upcoming = datetime.fromisoformat(value)
        require(upcoming.tzinfo is not None and upcoming > now + timedelta(minutes=30),
                'next_run_due_or_too_close')


def apply(home, backup, quiesced, accept_prompt_drift=None):
    check_quiesced(home, quiesced)
    with mutation_lock(home):
        before, after, accepted_drift = build_plan(home, accept_prompt_drift)
        if before == after:
            if backup.exists():
                load_backup(home, backup)
            print('apply=noop')
            return
        check_window(home)
        manifest = prepare_backup(home, backup, before, after, accepted_prompt_drift=accepted_drift)
        require(snapshot(home) == before, 'inputs_changed_after_backup')
        try:
            restore_tree(home, after, before, manifest['modes_after'], backup)
            require(snapshot(home) == after, 'apply_verification_failed')
            manifest['status'] = 'applied'
            atomic_write(backup / 'manifest.json', json_bytes(manifest))
        except (Exception, KeyboardInterrupt):
            recover(home, backup)
            raise
        print('apply=verified; start_services_only_after_external_gateway_whitelist_is_ready')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'apply', 'rollback'), nargs='?', default='plan')
    parser.add_argument('--backup-dir', type=Path)
    parser.add_argument('--accept-prompt-drift', action='append', default=[], metavar='TEST_ID=SHA256',
                        help='plan/apply only; repeat to accept drift at this exact current prompt UTF-8 SHA-256')
    parser.add_argument('--quiesced', action='store_true', help='Operator verified both gateways, workers and all writers are stopped/drained')
    args = parser.parse_args(argv)
    try:
        require(args.command != 'rollback' or not args.accept_prompt_drift, 'prompt_drift_only_for_plan_or_apply')
        allowances = parse_prompt_drift(args.accept_prompt_drift)
        value = os.environ.get('HERMES_HOME')
        require(bool(value) and Path(value).is_absolute(), 'explicit_absolute_HERMES_HOME_required')
        home = safe_path(value)
        require(home.is_dir(), 'home_missing')
        if args.command == 'plan':
            build_plan(home, allowances)
        else:
            require(args.backup_dir is not None and args.backup_dir.is_absolute(), 'absolute_backup_dir_required')
            backup = safe_path(args.backup_dir)
            if args.command == 'apply':
                apply(home, backup, args.quiesced, allowances)
            else:
                check_quiesced(home, args.quiesced)
                with mutation_lock(home):
                    recover(home, backup)
                print('rollback=verified; config.yaml=unchanged; external_gateway_restore_required')
        return 0
    except SwitchError as exc:
        print('STOP: ' + str(exc), file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error):
        # Exception payloads can contain JSON, filenames or settings. Never echo them.
        print('STOP: unreadable_or_invalid_input; keep_services_stopped_and_review_backup', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
