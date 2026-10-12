"""A6 dates must work without jsonschema's optional RFC 3339 implementation."""
import copy
from importlib import import_module

from jsonschema import FormatChecker
import pytest

pytest.register_assert_rewrite('ollo_bridge_test_support')
from ollo_bridge_test_support import example

pytest_plugins = ['ollo_bridge_test_support']


@pytest.fixture(params=[('date', 'uuid'), ()], ids=['stdlib-formats', 'no-formats'])
def format_checker(request, service, monkeypatch):
    # Explicitly disable date-time even when a developer dependency installed it.
    # Also bypass the bridge's own stdlib checker: the schema must stand alone.
    checker = FormatChecker(formats=request.param)
    assert 'date-time' not in checker.checkers
    monkeypatch.setattr(service.protocol, '_CHECKER', checker)
    return checker


def assert_accepts(protocol, checker, name, value):
    assert protocol.validate(name, value) == value
    derived = protocol._VALIDATOR(protocol.schema(name), format_checker=checker)
    assert list(derived.iter_errors(value)) == [], (name, value)


def assert_rejects(protocol, checker, name, value):
    with pytest.raises(protocol.BridgeError) as error:
        protocol.validate(name, value)
    assert (error.value.status, error.value.code, error.value.retryable) == (400, 'invalid_request', False)
    derived = protocol._VALIDATOR(protocol.schema(name), format_checker=checker)
    assert not derived.is_valid(value), (name, value)


def test_date_and_datetime_branches_are_disjoint_without_optional_formats(service, format_checker):
    protocol = service.protocol
    dates = ['2026-10-12', '2000-02-29', '2024-02-29', '0001-01-01', '9999-12-31']
    times = ['2026-10-12T10:00+08:00', '2026-10-12T02:00Z', '2026-10-11T22:00-04:00',
             '2026-10-12T10:00:00+08:00', '2026-10-12T02:00:00.125Z', '2000-02-29T23:59:59Z',
             '2026-10-12T02:00:00.' + '1' * 43 + 'Z']
    branches = {
        name: protocol._VALIDATOR(protocol.schema(name), format_checker=format_checker)
        for name in ('P_ISODate', 'P_ISODateTime')
    }
    for values, expected in [(dates, 'P_ISODate'), (times, 'P_ISODateTime')]:
        for value in values:
            assert [name for name, check in branches.items() if check.is_valid(value)] == [expected], value
            assert_accepts(protocol, format_checker, 'P_GTDDate', value)
            assert_accepts(protocol, format_checker, 'B_' + expected.removeprefix('P_'), value)


def test_invalid_dates_and_unzoned_times_fail_even_without_any_formats(service, format_checker):
    for value in ['2026-10-12T10:00', '2026-10-12T10:00:00', '2026-10-12T10:00:00.125',
                  '2026-02-30', '1900-02-29', '2026-04-31', '0000-01-01', '10000-01-01',
                  '2026-02-30T10:00+08:00', '1900-02-29T10:00Z', '0000-01-01T10:00Z',
                  '2026-10-12T24:00Z', '2026-10-12T10:60Z', '2026-10-12T10:00:60Z',
                  '2026-10-12T10:00+24:00', '2026-10-12T10:00+08:60', '2026-10-12T10:00+0800',
                  '2026-10-12t10:00Z', '2026-10-12T10:00z', '２０２６-10-12',
                  ' 2026-10-12', '2026-10-12\n', '2026-10-12T10:00Z\n',
                  '2026-10-12T02:00:00.' + '1' * 44 + 'Z']:
        assert_rejects(service.protocol, format_checker, 'P_GTDDate', value)


def documents(due, instant):
    report = example('report')['report']['gtd']
    report['generated_at'] = instant
    report['agenda']['due'][0]['due'] = due
    for event in report['agenda']['events']:
        event.update(start=instant, end=instant)
    for original in report['originals'].values():
        original['modified_at'] = instant
        if original['source'] == 'reminder':
            original['due'] = due
        else:
            original.update(start=instant, end=instant)
    yield 'P_GTDReport', report, ('agenda', 'due', 0, 'due'), ('generated_at',)

    suggestion = example('interpret')['item']['suggestion']
    suggestion.update(due=due, follow_up=due, event={'start': instant, 'minutes': 60, 'calendar': '工作'})
    yield 'P_GTDSuggestion', suggestion, ('due',), ('event', 'start')

    confirmation = example('confirmation')['confirmation']
    confirmation['changes'][1]['follow_up'] = due
    confirmation['changes'][2].update(due=due, alarm=instant)
    # create_reminder.alarm is GTDDate (or null), not ISODateTime.
    # Use update_event.start for the timestamp-only rejection contract.
    confirmation['changes'].append({'op': 'update_event', 'item': 'fixture-event-1',
                                    'start': instant, 'by': 'user'})
    yield 'P_GTDConfirmation', confirmation, ('changes', 2, 'due'), ('changes', 3, 'start')

    item = {'id': 'fixture-reminder-1', 'exists': True, 'completed': False, 'title': '合成事项',
            'list': '工作', 'due': due, 'modified_at': instant}
    yield 'P_ScheduleItem', item, ('due',), ('modified_at',)


def test_report_suggestion_confirmation_and_item_accept_date_or_zoned_time(service, format_checker):
    for due in ['2026-10-12', '2026-10-12T10:00+08:00', '2026-10-12T02:00:00.125Z']:
        for name, value, _, _ in documents(due, '2026-10-12T10:00+08:00'):
            assert_accepts(service.protocol, format_checker, name, value)


def test_nested_date_fields_reject_naive_time_and_timestamp_fields_reject_date(service, format_checker):
    for name, value, due_path, timestamp_path in documents('2026-10-12', '2026-10-12T10:00+08:00'):
        assert_accepts(service.protocol, format_checker, name, value)
        for path, invalid in [(due_path, '2026-10-12T10:00'), (timestamp_path, '2026-10-12'),
                              (timestamp_path, '2026-10-12T10:00')]:
            bad = copy.deepcopy(value)
            target = bad
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = invalid
            assert_rejects(service.protocol, format_checker, name, bad)


def test_confirmation_alarm_accepts_gtd_date_but_rejects_naive_time(service, format_checker):
    value = example('confirmation')['confirmation']
    change = value['changes'][2]
    assert change['op'] == 'create_reminder'
    for alarm in ['2026-10-12', '2026-10-12T10:00+08:00', '2026-10-12T02:00:00.125Z', None]:
        change['alarm'] = alarm
        assert_accepts(service.protocol, format_checker, 'P_GTDConfirmation', value)
    del change['alarm']
    assert_accepts(service.protocol, format_checker, 'P_GTDConfirmation', value)
    for alarm in ['2026-10-12T10:00', '2026-10-12T10:00:00', '2026-02-30']:
        change['alarm'] = alarm
        assert_rejects(service.protocol, format_checker, 'P_GTDConfirmation', value)


def test_bridge_python_parsers_accept_minute_precision_and_keep_original_text(service, format_checker):
    execution = import_module(service.base + '.execution')
    collector = import_module(service.base + '.collector')
    for instant in ['2026-10-12T10:00+08:00', '2026-10-12T02:00Z', '2026-10-11T22:00-04:00']:
        assert execution.business_day(instant) == '2026-10-12'
        value = {'items': [], 'originals': {}, 'collected_at': instant}
        assert collector.validate_collection(value) == value
        assert value['collected_at'] == instant
    # Date-only is legitimate for GTD due dates, never for execution/collection stamps.
    for invalid in ['2026-10-12', '2026-10-12T10:00']:
        with pytest.raises(service.protocol.BridgeError):
            execution.business_day(invalid)
        with pytest.raises(ValueError):
            collector.validate_collection({'items': [], 'originals': {}, 'collected_at': invalid})
