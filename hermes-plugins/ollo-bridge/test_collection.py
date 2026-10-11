"""Standalone collectors and the real cron path/prompt contract, no Apple store."""
from datetime import datetime
from importlib import import_module
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest_plugins = ['ollo_bridge_test_support']


@pytest.fixture
def collector(service):
    return import_module(service.base + '.collector')


@pytest.mark.parametrize('kind,days', [('agenda', 1), ('inbox', 1), ('review', 7)])
def test_scope_and_window_match_gtd_rules(collector, kind, days):
    now = datetime.fromisoformat('2026-10-11T21:00:00+08:00')
    request = collector.collect_request({}, kind, now)
    assert request['method'] == 'collect'
    assert request['lists'] == (['收件箱'] if kind == 'inbox' else ['收件箱', '工作', '生活', '家庭', '等待', '长期'])
    assert request['calendars'] == ([] if kind == 'inbox' else ['工作', '生活', '家庭'])
    start, end = (datetime.fromisoformat(request[k]) for k in ('start', 'end'))
    assert start == (now if kind == 'review' else now.replace(hour=0))
    assert (end - start).total_seconds() == days * 86400
    # There is no due-date filter for reminders, including old overdue and undated.
    assert set(request) == {'method', 'lists', 'calendars', 'apple', 'start', 'end'}


def test_collector_uses_shanghai_day_and_explicit_scope_overrides(collector):
    config = {'apple': {'lists': {'工作': 'Ollo 测试·工作'}},
              'collection': {'agenda': {'lists': ['工作'], 'calendars': []}}}
    request = collector.collect_request(config, 'agenda', datetime.fromisoformat('2026-10-11T18:00:00Z'))
    assert request['start'] == '2026-10-12T00:00:00+08:00'
    assert request['end'] == '2026-10-13T00:00:00+08:00'
    assert request['lists'] == ['工作'] and request['calendars'] == []
    assert request['apple']['lists']['工作'] == 'Ollo 测试·工作'


def collected(count=1):
    rows = [{'id': f'item-{i}', 'source': 'reminder', 'exists': True, 'completed': False,
             'title': '保留原文😀', 'list': '工作', 'due': None, 'modified_at': '2026-10-11T08:00:00+08:00'}
            for i in range(count)]
    return {'items': rows, 'originals': {row['id']: {k: v for k, v in row.items() if k not in {'id', 'exists'}} for row in rows},
            'collected_at': '2026-10-11T08:00:00+08:00'}


@pytest.mark.parametrize('count', [0, 500, 501])
def test_complete_result_and_hard_item_limit(collector, count):
    value = collected(count)
    if count <= 500:
        assert collector.validate_collection(value) == value
    else:
        with pytest.raises(ValueError, match='collection_limit_exceeded'):
            collector.validate_collection(value)


@pytest.mark.parametrize('corruption', ['completed', 'missing_original', 'changed_original', 'duplicate', 'missing_time'])
def test_unusable_collection_is_not_an_empty_success(collector, corruption):
    value = collected()
    if corruption == 'completed':
        value['items'][0]['completed'] = True
    elif corruption == 'missing_original':
        value['originals'] = {}
    elif corruption == 'changed_original':
        value['originals']['item-0']['title'] = 'not the captured original'
    elif corruption == 'duplicate':
        value['items'].append(value['items'][0].copy())
    else:
        value.pop('collected_at')
    with pytest.raises(ValueError):
        collector.validate_collection(value)


@pytest.mark.parametrize('count', [0, 1, 500])
def test_stdout_is_one_complete_utf8_document(collector, bridge, monkeypatch, capsys, count):
    value = collected(count)
    runner = Mock(return_value=SimpleNamespace(returncode=0,
        stdout=json.dumps(value, ensure_ascii=False).encode('utf-8'), stderr=b''))
    monkeypatch.setattr(collector.subprocess, 'run', runner)
    assert collector.main('agenda', bridge.home) == 0
    output = capsys.readouterr()
    assert output.err == '' and output.out.count('\n') == 1
    assert json.loads(output.out) == value
    if count:
        assert '保留原文😀' in output.out
    runner.assert_called_once()


@pytest.mark.parametrize('failure', ['timeout', 'permission', 'overflow', 'invalid_json', 'missing_helper'])
def test_collector_failure_has_no_success_stdout(collector, bridge, monkeypatch, capsys, failure):
    result = SimpleNamespace(returncode=1, stdout=b'{"error":"permission"}', stderr=b'private')
    runner = Mock(return_value=result)
    if failure == 'timeout':
        runner.side_effect = subprocess.TimeoutExpired('helper', 6)
    elif failure == 'missing_helper':
        runner.side_effect = FileNotFoundError('private path')
    elif failure == 'overflow':
        result.stdout = b'{"error":"collection_limit_exceeded"}'
    elif failure == 'invalid_json':
        result.returncode, result.stdout = 0, b'{bad'
    monkeypatch.setattr(collector.subprocess, 'run', runner)
    assert collector.main('agenda', bridge.home) == 1
    output = capsys.readouterr()
    assert output.out == '' and output.err.startswith('Ollo collection failed: ')
    assert 'private' not in output.err
    if failure == 'overflow':
        assert 'collection_limit_exceeded' in output.err
    runner.assert_called_once()
    assert runner.call_args.kwargs['timeout'] == 6
    request = json.loads(runner.call_args.kwargs['input'])
    assert request['method'] == 'collect'


@pytest.mark.platforms('macos')
@pytest.mark.parametrize('kind', ['agenda', 'inbox', 'review'])
def test_installed_entry_runs_via_host_once_without_keys_or_tools(bridge, monkeypatch, kind):
    from cron import scheduler, scheduler_script
    from cron.lifecycle_guard import check_gateway_lifecycle
    scripts = bridge.home / 'scripts'
    shutil.copytree(Path(__file__).parent / 'scripts', scripts, dirs_exist_ok=True)
    helper = bridge.home / 'fake-helper'
    result = collected(0)
    helper.write_text('#!' + sys.executable + '\n' +
        'import json, pathlib, sys\n' +
        'request = json.load(sys.stdin)\n' +
        'pathlib.Path(__file__).with_suffix(".request").write_text(json.dumps({"request": request, "argv": sys.argv}))\n' +
        'print(' + repr(json.dumps(result, ensure_ascii=False)) + ')\n', encoding='utf-8')
    helper.chmod(0o700)
    path = bridge.home / 'ollo/bridge.json'
    config = json.loads(path.read_text())
    config['helper_path'] = str(helper)
    path.write_text(json.dumps(config), encoding='utf-8')
    (bridge.home / 'ollo/bridge.key').unlink()  # Collection has no HTTP/auth need.
    entry = f'ollo_collect_{kind}.py'
    check_gateway_lifecycle('Generate the GTD report.', script=entry)
    ok, output = scheduler_script._run_job_script(entry)
    assert ok and json.loads(output) == result
    captured = json.loads(helper.with_suffix('.request').read_text())
    assert captured['argv'] == [str(helper)] and captured['request']['method'] == 'collect'
    assert captured['request']['calendars'] == ([] if kind == 'inbox' else ['工作', '生活', '家庭'])
    assert scheduler._parse_wake_gate(output) is True
    rerun = Mock(side_effect=AssertionError('script must only execute once'))
    monkeypatch.setattr(scheduler_script, '_run_job_script', rerun)
    job = {'id': 'fixture', 'prompt': 'Generate the GTD report.', 'script': entry, 'enabled_toolsets': []}
    prompt = scheduler._build_job_prompt(job, prerun_script=(ok, output))
    assert prompt.count(output) == 1 and 'Script Output' in prompt
    rerun.assert_not_called()
    assert scheduler._resolve_cron_enabled_toolsets(job, {'mcp_servers': {'any': {}}}) == []
    failure = scheduler._build_job_prompt(job, prerun_script=(False, 'Ollo collection failed: helper_failed'))
    assert 'Script Error' in failure and 'helper_failed' in failure
    # Host semantics: pre-run failure remains model context, not an early
    # no-agent failure. The candidate prompt must never produce a fake empty GTD.
