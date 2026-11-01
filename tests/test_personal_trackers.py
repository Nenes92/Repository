from datetime import date
from copy import deepcopy
import ast
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import personal_trackers as tracker

TODAY = date(2026, 11, 1)


def loan(**changes):
    return dict(id='loan', kind='installments', name='Auto', start_date='2026-01-31',
                count=12, amount=100., paid_offset=0, **changes)


def test_month_end_and_leap_year_schedule():
    record = loan()
    assert tracker.instalment_dates(record)[:3] == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]
    record['start_date'] = '2024-01-31'
    assert tracker.instalment_dates(record)[1] == date(2024, 2, 29)


def test_due_day_and_end_date_alternative():
    record = loan(due_day=15, end_date='2026-12-15')
    record['count'] = None
    schedule = tracker.instalment_dates(record)
    assert len(schedule) == 12 and schedule[-1] == date(2026, 12, 15)
    record['count'] = 11
    with pytest.raises(ValueError, match='non coincidono'):
        tracker.validate_record(record, TODAY)


def test_automatic_count_and_manual_correction_continue_in_future():
    record = loan()
    original = deepcopy(record)
    summary = tracker.instalment_summary(record, TODAY)
    assert (summary['paid'], summary['remaining'], summary['remaining_amount']) == (10, 2, 200.)
    corrected = tracker.validate_record(record, TODAY, paid_today=8)
    assert corrected['paid_offset'] == -2
    assert tracker.instalment_summary(corrected, TODAY)['paid'] == 8
    assert tracker.instalment_summary(corrected, date(2026, 11, 30))['paid'] == 9
    assert record == original
    assert tracker.validate_record(corrected, TODAY)['paid_offset'] == -2


def test_before_start_and_after_finish_clamped():
    record = loan()
    assert tracker.instalment_summary(record, date(2025, 1, 1))['paid'] == 0
    summary = tracker.instalment_summary(record, date(2027, 1, 1))
    assert summary['remaining'] == 0 and summary['next_due'] is None


def test_serialization_and_empty_register_do_not_restore_deleted_defaults():
    defaults = tracker.decode_rows([])
    assert len(defaults) == 12
    assert tracker.decode_rows(tracker.encode_rows(defaults)) == defaults
    assert tracker.decode_rows(tracker.encode_rows([])) == []
    with pytest.raises(ValueError):
        tracker.decode_rows([dict(ID='x', Sezione='auto', Nome='Bollo', Dati='broken')])
    with pytest.raises(ValueError):
        tracker.decode_rows(tracker.encode_rows([defaults[0], defaults[0]]))


def test_deadlines_reminders_and_investment_withdrawals():
    record = dict(id='car', kind='auto', name='Bollo, auto', last_date='2026-01-31', interval_months=1, reminder_days=14)
    assert tracker.car_deadline(record) == date(2026, 2, 28)
    record['next_date'] = '2026-11-15'
    assert tracker.deadline_status(tracker.car_deadline(record), TODAY, 14)[1] == '#fbbf24'
    ics = tracker.calendar_reminder(record, TODAY)
    assert 'DTSTART;VALUE=DATE:20261115' in ics and 'TRIGGER:-P14D' in ics
    assert 'SUMMARY:Bollo\\, auto' in ics
    assert tracker.holding_result(dict(invested=1000., current_value=900., withdrawn=200.)) == 100.


@pytest.mark.parametrize('changes', [dict(amount=-1), dict(amount=float('nan')), dict(count=0), dict(start_date=None), dict(due_day=32)])
def test_invalid_installments_cannot_be_saved(changes):
    record = loan()
    record.update(changes)
    with pytest.raises(ValueError):
        tracker.validate_record(record, TODAY)


def save_helper(rows, fail=False):
    writes, errors, caches = [], [], []
    def update(**kwargs):
        if fail:raise RuntimeError('offline')
        writes.append(kwargs['values'])
    ws = SimpleNamespace(get_all_records=lambda: deepcopy(rows), update=update)
    source = Path(__file__).parents[1] / 'spese_mensili.py'
    node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == '_save_personal_tracker_record')
    ns = dict(_personal_trackers=tracker, get_gsheet_client=lambda: object(),
              get_or_create_worksheet=lambda *a: ws, SHEET_URL='mock',
              _now_italy=lambda: SimpleNamespace(date=lambda: TODAY), pd=pd,
              _mobile_sheet_records=SimpleNamespace(clear=lambda *a: None),
              _set_gsheets_cache=lambda *a: caches.append(a), st=SimpleNamespace(error=errors.append))
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), ns)
    return ns['_save_personal_tracker_record'], writes, errors, caches


def test_save_merges_fresh_records_and_delete_blanks_tail_without_clear():
    records = tracker.initial_records()
    records[1]['notes'] = 'Changed from another session'
    save, writes, errors, caches = save_helper(tracker.encode_rows(records))
    assert save(records[0]['id'], None)
    output = [dict(zip(writes[0][0], row)) for row in writes[0][1:]]
    result = tracker.decode_rows(output)
    assert len(result) == 11 and result[0]['notes'] == 'Changed from another session'
    assert writes[0][-1] == ['', '', '', '']
    assert len(caches) == 1 and not errors


def test_failed_write_or_corrupt_record_does_not_update_cache():
    for rows, fail in [(tracker.encode_rows([]), True), ([{'ID':'x','Dati':'broken'}], False)]:
        save, writes, errors, caches = save_helper(rows, fail=fail)
        assert not save('loan', loan())
        assert errors and not writes and not caches
