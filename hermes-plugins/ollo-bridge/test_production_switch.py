"""Offline cutover behavior with synthetic state; no EventKit or live home."""
from copy import deepcopy
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).parent / 'scripts/production_switch.py'
spec = importlib.util.spec_from_file_location('ollo_production_switch', SCRIPT)
switch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(switch)
io = sys.modules['production_switch_io']


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(io.json_bytes(value))


@pytest.fixture
def installation(tmp_path, monkeypatch):
    home = tmp_path / 'synthetic-home'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.delenv('HERMES_TIMEZONE', raising=False)
    # Clock is data, not a host/platform substitution. Keep fixtures away from cron slots.
    window = switch.check_window
    monkeypatch.setattr(switch, 'check_window', lambda root: window(root,
        datetime.fromisoformat('2026-10-14T12:00:00+08:00')))
    (home / 'config.yaml').write_text('timezone: Asia/Shanghai\nprivate_setting: SYNTHETIC_CONFIG\n')
    helper = home / 'helper'
    helper.write_text('not executed by the switch')
    helper.chmod(0o700)
    prompts = switch.candidates()
    rows = []
    for index, row in enumerate(switch.JOBS):
        for tested in (False, True):
            rows.append({'id': row[int(tested)], 'name': ('Ollo测试-' if tested else '') + row[2],
                'schedule': {'kind': 'cron', 'expr': row[3], 'display': row[3]},
                'schedule_display': row[3], 'model': 'synthetic-model', 'provider': 'synthetic-provider',
                'prompt': prompts[index] if tested else 'SYNTHETIC_PRIVATE_REMINDER',
                'skills': ['gtd-ollo-test', 'hermes-plus-gtd'] if tested else ['gtd'],
                'skill': 'gtd-ollo-test' if tested else 'gtd',
                'deliver': 'ollo:main' if tested else 'photon',
                'script': 'ollo_collect_' + row[4] + '.py' if tested else None,
                'enabled_toolsets': [] if tested else None,
                'attach_to_session': False if tested else None, 'enabled': True, 'state': 'scheduled',
                'next_run_at': '2026-10-15T08:00:00+08:00', 'last_run_at': '2026-10-14T08:00:00+08:00',
                'repeat': {'times': None, 'completed': 19}, 'last_status': 'ok',
                'failure_deliver': 'ollo:main' if tested else 'photon'})
    rows.append({'id': 'unrelated', 'prompt': 'UNRELATED_PRIVATE_TEXT', 'enabled': False})
    save(home / 'cron/jobs.json', {'jobs': rows, 'updated_at': 'synthetic', 'extension': {'keep': True}})
    save(home / 'ollo/bridge.json', {'session_id': 'synthetic-session', 'name': 'Ollo',
        'gateway_url': 'http://127.0.0.1:8765', 'helper_path': str(helper),
        'jobs': {row[1]: {'title': row[5], 'kind': row[4]} for row in switch.JOBS},
        'agenda_jobs': {'weekday': switch.JOBS[0][1], 'weekend': switch.JOBS[1][1]},
        'apple': {key: {name: 'Ollo 测试·' + name for name in names}
                  for key, names in switch.apple_module().names().items()}})
    for name in ('gtd', 'gtd-ollo-test', 'hermes-plus-gtd'):
        target = home / 'skills' / name
        target.mkdir(parents=True)
        (target / 'SKILL.md').write_text(
            '---\nname: ' + name + '\ndescription: ' + ('Original GTD description' if name == 'gtd' else 'Test description') +
            '\n---\n# GTD（仅供测试）\n今日重点只存网关。focus_confirmed\nSYNTHETIC_SKILL_BODY\n')
        (target / 'examples').mkdir()
        (target / 'examples/sample.md').write_text('SYNTHETIC_COMPANION_' + name)
        (target / 'examples/action.sh').write_text('#!/bin/sh\nexit 0\n')
        (target / 'examples/action.sh').chmod(0o700)
    for name in ('ollo_collect.py', 'ollo_collect_agenda.py', 'ollo_collect_inbox.py', 'ollo_collect_review.py'):
        target = home / 'scripts' / name
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(SCRIPT.parent / name, target)
    for name in ('collector.py', 'apple_config.py'):
        target = home / 'plugins/ollo-bridge' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SCRIPT.parent.parent / name, target)
    # Missing secrets are intentional: a read attempt would fail this complete workflow.
    return home, tmp_path / 'backup'


def test_plan_is_readonly_and_redacted(installation, capsys):
    home, _ = installation
    before = io.snapshot(home, (*io.ROOTS, 'config.yaml', 'skills/hermes-plus-gtd'))
    assert switch.main([]) == 0
    assert before == io.snapshot(home, (*io.ROOTS, 'config.yaml', 'skills/hermes-plus-gtd'))
    assert not (home / 'cron/.jobs.lock').exists()
    output = capsys.readouterr().out
    assert 'sha256' in output and 'prompt_vs_candidate' in output
    assert all(secret not in output for secret in (
        'SYNTHETIC_PRIVATE_REMINDER', 'SYNTHETIC_CONFIG', 'SYNTHETIC_SKILL_BODY', 'UNRELATED_PRIVATE_TEXT'))


def drift_agenda_prompts(home):
    store = io.read_json(home / 'cron/jobs.json')
    flags, records = [], {}
    for index, row in enumerate(switch.JOBS[:2]):
        job = next(job for job in store['jobs'] if job['id'] == row[1])
        candidate = job['prompt']
        # Include whitespace to prove that consent hashes the full stored UTF-8 text.
        job['prompt'] = '\n SYNTHETIC_PRIVATE_旧规则_' + str(index) + '\n' + candidate + '\n '
        sha256 = io.digest(job['prompt'].encode())['sha256']
        flags.extend(['--accept-prompt-drift', row[1] + '=' + sha256])
        records[row[1]] = {'current_sha256': sha256,
                          'candidate_sha256': io.digest(candidate.strip().encode())['sha256']}
    save(home / 'cron/jobs.json', store)
    return flags, records


def test_prompt_drift_consent_is_audited_and_candidate_is_installed(installation, capsys):
    home, backup = installation
    flags, records = drift_agenda_prompts(home)
    before = io.snapshot(home)
    assert switch.main(['plan', *flags]) == 0
    assert io.snapshot(home) == before and not (home / 'cron/.jobs.lock').exists()
    output = capsys.readouterr().out
    comparisons = [json.loads(line) for line in output.splitlines() if 'prompt_vs_candidate' in line]
    assert len(comparisons) == 4
    assert {item['id']: {'current_sha256': item['current']['sha256'],
                        'candidate_sha256': item['candidate']['sha256']}
            for item in comparisons if item['accepted_drift']} == records
    assert 'SYNTHETIC_PRIVATE_旧规则' not in output
    args = ['apply', '--backup-dir', str(backup), '--quiesced', *flags]
    assert switch.main(args) == 0
    store = io.read_json(home / 'cron/jobs.json')
    for row, candidate in zip(switch.JOBS, switch.candidates()):
        job = next(job for job in store['jobs'] if job['id'] == row[0])
        assert job['prompt'] == candidate.strip()
    manifest = io.read_json(backup / 'manifest.json')
    assert manifest['accepted_prompt_drift'] == records
    after = io.snapshot(home)
    assert switch.main(args) == 0
    assert io.snapshot(home) == after and io.read_json(backup / 'manifest.json') == manifest
    assert switch.main(['plan', *flags]) == 0
    assert switch.main(['rollback', '--backup-dir', str(backup), '--quiesced']) == 0
    assert io.snapshot(home) == before
    assert io.read_json(backup / 'manifest.json')['accepted_prompt_drift'] == records
    assert 'SYNTHETIC_PRIVATE_旧规则' not in capsys.readouterr().out


@pytest.mark.parametrize('command', ['plan', 'apply'])
@pytest.mark.parametrize('fault,error', [
    ('no_allowance', '36c04d45e58c:prompt_candidate_mismatch'),
    ('missing_second', '4eabdf7d938b:prompt_candidate_mismatch'),
    ('wrong_hash', '36c04d45e58c:prompt_acceptance_hash_mismatch'),
    ('normalized_hash', '36c04d45e58c:prompt_acceptance_hash_mismatch'),
    ('prompt_changed', '36c04d45e58c:prompt_acceptance_hash_mismatch'),
    ('other_field', '4eabdf7d938b:not_enabled'),
])
def test_prompt_consent_does_not_bypass_current_hash_or_other_checks(installation, capsys, command, fault, error):
    home, backup = installation
    flags, _ = drift_agenda_prompts(home)
    # A prior approved plan is never a cached authorization for later content.
    assert switch.main(['plan', *flags]) == 0
    capsys.readouterr()
    store = io.read_json(home / 'cron/jobs.json')
    normalized = io.digest(store['jobs'][1]['prompt'].strip().encode())['sha256']
    actions = {
        'no_allowance': flags.clear,
        'missing_second': lambda: flags.__delitem__(slice(2, None)),
        'wrong_hash': lambda: flags.__setitem__(1, switch.JOBS[0][1] + '=' + '0' * 64),
        'normalized_hash': lambda: flags.__setitem__(1, switch.JOBS[0][1] + '=' + normalized),
        'prompt_changed': lambda: store['jobs'][1].update(prompt='SYNTHETIC_PRIVATE_LATER_EDIT'),
        'other_field': lambda: store['jobs'][3].update(enabled=False),
    }
    actions[fault]()
    save(home / 'cron/jobs.json', store)
    before = io.snapshot(home)
    assert switch.main([command, '--backup-dir', str(backup), '--quiesced', *flags]) == 2
    output = capsys.readouterr()
    assert 'STOP: ' + error in output.err and 'SYNTHETIC_PRIVATE' not in output.out
    assert io.snapshot(home) == before and not backup.exists()


@pytest.mark.parametrize('value', ['d83281b75902=' + 'a' * 64, 'unknown=' + 'a' * 64,
    '36c04d45e58c=not-a-hash', '36c04d45e58c', '36c04d45e58c=' + 'a' * 63])
def test_prompt_consent_rejects_invalid_id_or_hash(installation, value):
    home, _ = installation
    before = io.snapshot(home)
    assert switch.main(['--accept-prompt-drift', value]) == 2
    assert io.snapshot(home) == before


def test_matching_prompt_requires_supplied_hash_but_is_not_recorded_as_drift(installation, capsys):
    home, backup = installation
    tested = io.read_json(home / 'cron/jobs.json')['jobs'][1]
    flag = tested['id'] + '=' + io.digest(tested['prompt'].encode())['sha256']
    assert switch.main(['plan', '--accept-prompt-drift', tested['id'] + '=' + '0' * 64]) == 2
    assert 'prompt_acceptance_hash_mismatch' in capsys.readouterr().err
    args = ['apply', '--backup-dir', str(backup), '--quiesced', '--accept-prompt-drift', flag]
    assert switch.main([*args, '--accept-prompt-drift', flag]) == 0
    assert io.read_json(backup / 'manifest.json')['accepted_prompt_drift'] == {}


def test_conflicting_prompt_consent_and_rollback_option_are_rejected(installation):
    home, backup = installation
    flags, _ = drift_agenda_prompts(home)
    before = io.snapshot(home)
    assert switch.main([*flags, '--accept-prompt-drift', switch.JOBS[0][1] + '=' + '0' * 64]) == 2
    assert switch.main(['rollback', '--backup-dir', str(backup), '--quiesced', *flags]) == 2
    assert io.snapshot(home) == before and not backup.exists()


def test_apply_preserves_identity_installs_tested_skill_and_roundtrips(installation):
    home, backup = installation
    before = io.snapshot(home)
    original_modes = io.file_modes(home, before)
    unchanged = io.snapshot(home, ('config.yaml', 'skills/hermes-plus-gtd'))
    old = io.read_json(home / 'cron/jobs.json')
    args = ['apply', '--backup-dir', str(backup), '--quiesced']
    assert switch.main(args) == 0
    after = io.snapshot(home)
    store = io.read_json(home / 'cron/jobs.json')
    assert store['extension'] == old['extension']
    assert store['jobs'][-1] == old['jobs'][-1]
    by_id = {job['id']: job for job in store['jobs']}
    changed = {'deliver', 'skills', 'skill', 'script', 'prompt', 'enabled_toolsets', 'attach_to_session', 'failure_deliver'}
    for row in switch.JOBS:
        prior = next(job for job in old['jobs'] if job['id'] == row[0])
        actual = by_id[row[0]]
        assert {k: v for k, v in actual.items() if k not in changed} == {k: v for k, v in prior.items() if k not in changed}
        assert actual['deliver'] == actual['failure_deliver'] == 'ollo:main'
        assert actual['skills'] == ['gtd', 'hermes-plus-gtd']
        assert actual['enabled_toolsets'] == [] and actual['attach_to_session'] is False
        assert row[1] not in by_id
    bridge = io.read_json(home / 'ollo/bridge.json')
    assert 'apple' not in bridge
    assert switch.apple_module().names(bridge.get('apple'))['lists']['工作'] == '工作'
    skill = (home / switch.SKILL).read_text()
    assert 'name: gtd\n' in skill and 'description: Original GTD description' in skill
    assert '仅供测试' not in skill and 'gtd-ollo-test' not in skill
    assert (home / 'skills/gtd/examples/sample.md').read_text() == 'SYNTHETIC_COMPANION_gtd-ollo-test'
    assert not (home / 'skills/gtd-ollo-test').exists()
    assert os.access(home / 'skills/gtd/examples/action.sh', os.X_OK)
    backup_before = io.snapshot(backup, ('before', 'manifest.json'))
    assert switch.main(args) == 0
    assert after == io.snapshot(home)
    assert backup_before == io.snapshot(backup, ('before', 'manifest.json'))
    assert switch.main(['plan']) == 0
    for path in backup.rglob('*'):
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    assert switch.main(['rollback', '--backup-dir', str(backup), '--quiesced']) == 0
    assert switch.main(['rollback', '--backup-dir', str(backup), '--quiesced']) == 0
    assert before == io.snapshot(home)
    assert io.file_modes(home, before) == original_modes
    assert unchanged == io.snapshot(home, ('config.yaml', 'skills/hermes-plus-gtd'))


@pytest.mark.parametrize('field,value', [
    ('name', 'changed'), ('deliver', 'other'), ('enabled', False), ('enabled_toolsets', ['terminal']),
    ('model', 'drifted'), ('attach_to_session', True), ('script', 'other.py'), ('skills', ['other']),
    ('pending_slot', {'scheduled_instant': 'old'}), ('manual_run_at', 'old'),
    ('failure_deliver', 'other'), ('schedule', {'kind': 'cron', 'expr': '1 8 * * *'}),
])
def test_job_drift_fails_without_backup_or_mutation(installation, field, value):
    home, backup = installation
    store = io.read_json(home / 'cron/jobs.json')
    store['jobs'][0][field] = value
    save(home / 'cron/jobs.json', store)
    before = io.snapshot(home)
    assert switch.main(['apply', '--backup-dir', str(backup), '--quiesced']) == 2
    assert io.snapshot(home) == before and not backup.exists()


@pytest.mark.parametrize('fault', ['test_missing', 'prompt', 'skill_missing', 'helper_missing',
    'timezone', 'timezone_env', 'mapping', 'scope', 'duplicate_id', 'script', 'candidate'])
def test_preconditions_fail_closed(installation, monkeypatch, fault, capsys):
    home, backup = installation
    store = io.read_json(home / 'cron/jobs.json')
    bridge = io.read_json(home / 'ollo/bridge.json')
    actions = {
        'test_missing': lambda: store['jobs'].pop(1),
        'prompt': lambda: store['jobs'][1].update(prompt='PRIVATE_DRIFT'),
        'duplicate_id': lambda: store['jobs'].append(deepcopy(store['jobs'][0])),
        'skill_missing': lambda: (home / switch.TEST_SKILL).unlink(),
        'helper_missing': lambda: (home / 'helper').unlink(),
        'timezone': lambda: (home / 'config.yaml').write_text('timezone: UTC\n'),
        'timezone_env': lambda: monkeypatch.setenv('HERMES_TIMEZONE', 'UTC'),
        'mapping': lambda: bridge['apple']['lists'].update(工作='Unexpected'),
        'scope': lambda: bridge.update(collection={'review': {'lists': ['工作']}}),
        'script': lambda: (home / 'scripts/ollo_collect.py').write_text('changed'),
        'candidate': lambda: monkeypatch.setattr(switch, 'PLUGIN', home / 'missing-candidate'),
    }
    actions[fault]()
    save(home / 'cron/jobs.json', store)
    save(home / 'ollo/bridge.json', bridge)
    before = io.snapshot(home)
    assert switch.main(['apply', '--backup-dir', str(backup), '--quiesced']) == 2
    assert io.snapshot(home) == before and not backup.exists()
    assert 'PRIVATE_DRIFT' not in capsys.readouterr().out


def test_mid_apply_failure_restores_all_files(installation, monkeypatch):
    home, backup = installation
    flags, records = drift_agenda_prompts(home)
    before = io.snapshot(home)
    real_write = io.atomic_write
    failed = False

    def fail_once(path, data, **kwargs):
        nonlocal failed
        if path == home / 'ollo/bridge.json' and not failed:
            failed = True
            raise OSError('SYNTHETIC_PRIVATE_FAILURE')
        return real_write(path, data, **kwargs)

    monkeypatch.setattr(io, 'atomic_write', fail_once)
    assert switch.main(['apply', '--backup-dir', str(backup), '--quiesced', *flags]) == 2
    assert failed and io.snapshot(home) == before
    assert io.read_json(backup / 'manifest.json')['status'] == 'rolled_back'
    assert io.read_json(backup / 'manifest.json')['accepted_prompt_drift'] == records


def test_interrupted_transaction_is_recoverable(installation):
    home, backup = installation
    before, after, _ = switch.build_plan(home)
    io.prepare_backup(home, backup, before, after)
    io.atomic_write(home / 'cron/jobs.json', after['cron/jobs.json'])
    (home / switch.TEST_SKILL).unlink()
    partial = backup / '.ollo-switch-interrupted'
    partial.write_bytes(b'partial staged payload')
    partial.chmod(0o600)
    assert switch.main(['rollback', '--backup-dir', str(backup), '--quiesced']) == 0
    assert io.snapshot(home) == before


@pytest.mark.parametrize('fault', ['existing_backup', 'no_quiescence', 'live_pid', 'symlink', 'secret_in_skill'])
def test_unsafe_mutation_refused(installation, fault):
    home, backup = installation
    args = ['apply', '--backup-dir', str(backup), '--quiesced']
    def existing_backup():
        backup.mkdir()
        (backup / 'keep').write_text('keep')

    actions = {
        'existing_backup': existing_backup,
        'no_quiescence': args.pop,
        'live_pid': lambda: save(home / 'gateway.pid', {'pid': os.getpid()}),
        'symlink': lambda: (home / 'skills/gtd/link').symlink_to(home / 'helper'),
        'secret_in_skill': lambda: (home / 'skills/gtd/.env').write_text('NEVER_READ'),
    }
    actions[fault]()
    jobs = (home / 'cron/jobs.json').read_bytes()
    assert switch.main(args) == 2
    assert (home / 'cron/jobs.json').read_bytes() == jobs


def test_rollback_rejects_tampered_backup_and_later_edits(installation):
    home, backup = installation
    assert switch.main(['apply', '--backup-dir', str(backup), '--quiesced']) == 0
    skill = home / switch.SKILL
    skill.write_text(skill.read_text() + '\nLater user edit')
    after = io.snapshot(home)
    assert switch.main(['rollback', '--backup-dir', str(backup), '--quiesced']) == 2
    assert io.snapshot(home) == after
    (backup / 'before/cron/jobs.json').write_text('{}')
    assert switch.main(['rollback', '--backup-dir', str(backup), '--quiesced']) == 2
    assert io.snapshot(home) == after


@pytest.mark.parametrize('entry', ['script', 'hermes_runtime'])
@pytest.mark.parametrize('with_drift', [False, True])
def test_cli_default_plan_needs_no_secrets(installation, tmp_path, entry, with_drift):
    home, _ = installation
    env = {key: value for key, value in os.environ.items() if not key.startswith('HERMES_')}
    env.update(HERMES_HOME=str(home), PYTHONDONTWRITEBYTECODE='1')
    flags = drift_agenda_prompts(home)[0] if with_drift else []
    command = [sys.executable, str(SCRIPT), *flags]
    if entry == 'hermes_runtime':
        from hermes_cli._launchers import runtime_command
        root = tmp_path / 'synthetic runtime'
        root.mkdir()
        # Isolate PM boot while exercising the real launcher, argv and -I boundary.
        (root / 'hermes_bootstrap.py').write_text('')
        command = runtime_command(root, [str(SCRIPT), *flags], python=sys.executable, home=home,
            code="import runpy,sys; sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')")
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'state=ready' in result.stdout and 'SYNTHETIC_PRIVATE_REMINDER' not in result.stdout


@pytest.mark.parametrize('time', ['2026-10-14T07:40:00+08:00', '2026-10-14T09:30:00+08:00',
    '2026-10-14T21:10:00+08:00', '2026-10-16T12:00:00+08:00'])
def test_window_and_overdue_slots_block_cutover(installation, time):
    home, _ = installation
    # The original function remains available independently of the fixture clock.
    spec = importlib.util.spec_from_file_location('switch_clock_check', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(io.SwitchError):
        module.check_window(home, datetime.fromisoformat(time))
