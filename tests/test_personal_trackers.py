from datetime import date
from copy import deepcopy
import ast
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import personal_trackers as tracker

TODAY = date(2026, 11, 1)


def test_cometa_average_uses_latest_twelve_complete_months_and_keeps_month_detail():
    history = {f'2025-{m:02}':dict(personal=30.,company=70.,tfr=100.+m) for m in range(1,13)}
    history['2026-01'] = dict(personal=30.,company=70.,tfr=200.)
    history['2026-03'] = dict(personal=999.,company=999.,tfr=999.)  # Future month.
    history['2026-02'] = dict(personal=30.,company=70.)  # Missing TFR is not zero.
    item = dict(name='Cometa',kind='investments',_pension_history=history,cometa_rate_initialized=True,
                monthly_personal=30.,monthly_company=70.,monthly_tfr=200.,monthly_source_month='2026-01')
    expected = round((sum(200.+m for m in range(2,13))+300.)/12,2)
    assert tracker.pension_monthly_average(item,date(2026,2,20)) == dict(amount=expected,count=12)
    html = tracker._investment_card_content(item,date(2026,2,20))
    assert '≈ '+tracker.money(expected) in html
    assert '<strong>Gennaio</strong><strong class="tracker-ink-value">€300,00</strong>' in html


def test_cometa_average_uses_available_samples_without_inventing_missing_months():
    item = dict(_pension_history={'2026-01':dict(personal=30.,company=70.,tfr=200.)})
    assert tracker.pension_monthly_average(item,TODAY) == dict(amount=300.,count=1)
    assert tracker.pension_monthly_average(dict(item,cometa_auto_quotes=False),TODAY) == dict(amount=None,count=0)


def loan(**changes):
    return dict(id='loan', kind='installments', name='Auto', start_date='2026-01-31',
                count=12, amount=100., paid_offset=0, **changes)


def leonardo_share_plan():
    return dict(id='investment-3',kind='investments',name='Leonardo · JP Morgan',share_plan=True,
                current_value=5621.36,value_date='2026-11-06',redemption_year=2028,
                production_source_version=2,annual_contributions=[
                    dict(year=2025,date='2025-07-01',personal_shares=45,share_price=47.56,
                         matching_shares=15,welcome_shares=4,confirmed=True),
                    dict(year=2026,date='2026-07-01',personal_shares=39,share_price=50.37,
                         matching_shares=13,welcome_shares=0,confirmed=True),
                    dict(year=2027,date='2027-07-01',personal_shares=None,share_price=None,
                         matching_shares=None,welcome_shares=0,confirmed=False)])


def test_leonardo_purchases_awards_and_market_return_are_distinct():
    item=tracker.validate_record(leonardo_share_plan(),date(2026,11,6))
    summary=tracker.investment_summary(item,date(2026,11,6))
    assert (summary['personal'],summary['company'],summary['total'])==(4104.63,1558.45,5663.08)
    assert tracker.investment_performance(item,date(2026,11,6))['amount']==-41.72
    html=tracker.cards_html([item],'investments',date(2026,11,6))
    assert '116 = 84 + 32' in html
    assert '+€1.516,73' in html and '−€41,72' in html
    assert '€2.140,20' in html and '€1.964,43' in html
    assert '15 + 4 iniziali' in html and 'Luglio 2027 · azioni da inserire' in html
    restored=tracker.decode_rows(tracker.encode_rows([item]))[0]
    assert restored['annual_contributions'][0]['welcome_shares']==4
    assert tracker.investment_summary(restored,date(2027,8,1))['total']==5663.08
    updated=tracker.validate_record(dict(item,current_value=6000.,value_date='2026-11-07'),date(2026,11,7))
    assert tracker.investment_summary(updated,date(2026,11,7))['total']==5663.08
    assert tracker.investment_performance(updated,date(2026,11,7))['amount']==336.92


def test_share_purchase_defaults_matching_and_rejects_invalid_counts():
    row=tracker.share_purchase(dict(personal_shares=10,share_price=50.,welcome_shares=0))
    assert (row['matching_shares'],row['personal'],row['company'])==(3,500.,150.)
    for changes in [dict(personal_shares=1.5),dict(matching_shares=-1),dict(share_price=None)]:
        with pytest.raises(ValueError):
            tracker.share_purchase(dict(row,**changes))


def test_leonardo_form_saves_share_fields_and_valuation_only_on_submit(tmp_path):
    from streamlit.testing.v1 import AppTest
    script=tmp_path/'shares.py'
    script.write_text('''from datetime import date
import streamlit as st
import personal_trackers as t
if 'records' not in st.session_state:
    st.session_state.records=[t.investment_record('''+repr(leonardo_share_plan())+''')]
    st.session_state.saves=0
def save(identifier,item):
    st.session_state.records=t.merge_record(st.session_state.records,identifier,item)
    st.session_state.saves+=1
    return True
t.render_tracker(st.session_state.records,'investments',save,lambda _:None,date(2026,11,7))
''')
    at=AppTest.from_file(str(script)).run()
    at.selectbox(key='tracker_select_investments').set_value('investment-3').run()
    assert not at.exception
    next(field for field in at.number_input if field.label=='Controvalore totale (€)').set_value(6000.)
    assert at.session_state.saves==0
    next(button for button in at.button if button.label=='💾 Salva').click().run()
    assert not at.exception and at.session_state.saves==1
    item=at.session_state.records[0]
    assert item['current_value']==6000. and item['value_date']=='2026-11-07'
    assert item['annual_contributions'][0]['welcome_shares']==4
    assert tracker.investment_summary(item,date(2026,11,7))['total']==5663.08


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
    assert len(defaults) == 13
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


def save_helper(rows, fail=False, calendar_calls=None):
    writes, errors, caches = [], [], []
    def update(**kwargs):
        if fail:raise RuntimeError('offline')
        writes.append(kwargs['values'])
    ws = SimpleNamespace(get_all_records=lambda: deepcopy(rows), update=update)
    source = Path(__file__).parents[1] / 'spese_mensili.py'
    node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == '_save_personal_tracker_record')
    ns = dict(_personal_trackers=tracker, get_gsheet_client=lambda: object(),
              _sync_auto_calendar_after_save=lambda *args: calendar_calls.append(args) if calendar_calls is not None else None,
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
    assert len(result) == 12 and result[0]['notes'] == 'Changed from another session'
    assert writes[0][-1] == ['', '', '', '']
    assert len(caches) == 1 and not errors


def test_failed_write_or_corrupt_record_does_not_update_cache():
    for rows, fail in [(tracker.encode_rows([]), True), ([{'ID':'x','Dati':'broken'}], False)]:
        save, writes, errors, caches = save_helper(rows, fail=fail)
        assert not save('loan', loan())
        assert errors and not writes and not caches


def investment(name='Moneyfarm', **changes):
    item = tracker.investment_record(dict(id='holding', kind='investments', name=name,
                                        invested=None, current_value=None, withdrawn=0.))
    item.update(changes)
    return item


def test_investment_presets_and_legacy_migration_preserve_money():
    item = investment('Allianz / Alleanza', invested=2500.)
    # Simulate a previously saved row with no new contribution fields.
    item = tracker.investment_record(dict(id='legacy', kind='investments', name='Allianz / Alleanza',
                                         invested=2500., current_value=2700., withdrawn=0.))
    assert item['name'] == 'Alleanza Assicurazioni'
    assert item['contribution_mode'] == 'monthly'
    summary = tracker.investment_summary(item, TODAY)
    assert summary['total'] == 2500. and summary['personal'] == 2500.
    assert summary['unallocated'] == 0.
    assert summary['company'] == 0.
    assert summary['gain'] == 200.
    assert tracker.decode_rows(tracker.encode_rows([item])) == [item]
    assert tracker.investment_record(item) == item
    cometa = investment('Cometa')
    assert (cometa['monthly_personal'], cometa['monthly_company']) == (30.52, 68.66)


def test_monthly_investments_count_dates_and_separate_personal_company():
    item = investment('Cometa', contribution_start='2026-01-31', opening_personal=300., opening_company=700.)
    february = tracker.investment_summary(item, date(2026, 2, 27))
    assert (february['count'], february['personal'], february['company'], february['total']) == (1, 330.52, 768.66, 1319.2)
    february = tracker.investment_summary(item, date(2026, 2, 28))
    assert (february['count'], february['personal'], february['company'], february['total']) == (2, 361.04, 837.32, 1638.4)
    assert february['next_due'] == date(2026, 3, 31)
    assert tracker.investment_summary(item, date(2025, 12, 31))['total'] == 1000.


def test_monthly_investments_stop_and_manual_correction_continues():
    item = investment(contribution_start='2026-01-05')
    corrected = tracker.validate_record(item, TODAY, paid_today=8)
    assert tracker.investment_summary(corrected, TODAY)['total'] == 800.
    assert tracker.investment_summary(corrected, date(2026, 11, 5))['total'] == 900.
    assert tracker.validate_record(corrected, TODAY)['contribution_offset'] == -2
    stopped = investment(contribution_start='2026-01-05', contribution_end='2026-03-05')
    summary = tracker.investment_summary(stopped, TODAY)
    assert summary['count'] == 3 and summary['total'] == 300. and summary['next_due'] is None


def test_annual_investments_only_count_configured_dates_not_future_deposits():
    item = investment('Leonardo · JP Morgan', annual_contributions=[
        dict(year=2025, date='2025-06-15', personal=1000., company=200.),
        dict(year=2026, date='2026-06-15', personal=1500., company=300.),
        dict(year=2027, date='2027-06-15', personal=2000., company=400.)])
    item = tracker.validate_record(item, TODAY)
    summary = tracker.investment_summary(item, TODAY)
    assert (summary['personal'], summary['company'], summary['total'], summary['count']) == (2500., 500., 3000., 2)
    assert summary['next_due'] == date(2027, 6, 15)
    assert [row['counted'] for row in summary['schedule']] == [True, True, False]
    assert tracker.investment_summary(item, date(2027, 6, 15))['total'] == 5400.
    assert tracker.investment_summary(item, date(2028, 12, 31))['total'] == 5400.
    item['annual_contributions'][0]['date'] = None
    assert tracker.investment_summary(item, TODAY)['total'] == 1800.


def test_updated_value_is_manual_and_historical_gain_uses_same_date():
    item = investment(contribution_start='2026-01-01', current_value=1100., value_date='2026-10-01', withdrawn=50.)
    summary = tracker.investment_summary(item, date(2026, 11, 1))
    assert summary['total'] == 1100. and summary['gain'] == 150.
    assert item['current_value'] == 1100.
    item['value_date'] = '2026-11-01'
    assert tracker.investment_summary(item, TODAY)['gain'] == 50.


@pytest.mark.parametrize('changes', [
    dict(monthly_personal=-1.), dict(monthly_company=float('inf')),
    dict(contribution_start='2026-06-01', contribution_end='2026-05-01'),
    dict(contribution_end='2026-05-01'), dict(contribution_mode='invalid'),
])
def test_invalid_investment_settings_cannot_be_saved(changes):
    with pytest.raises(ValueError):
        tracker.validate_record(investment('Cometa', **changes), TODAY)


@pytest.mark.parametrize('rows,redemption', [
    ([dict(year=2025, date='2026-01-01', personal=100., company=10.)], 2028),
    ([dict(year=2025, date=None, personal=-100., company=10.)], 2028),
    ([dict(year=2025, date=None, personal=100., company=10.)]*2, 2028),
    ([dict(year=2027, date=None, personal=100., company=10.)], 2026),
])
def test_invalid_annual_years_amounts_or_withdrawal_rejected(rows, redemption):
    with pytest.raises(ValueError):
        tracker.validate_record(investment('Leonardo · JP Morgan', annual_contributions=rows, redemption_year=redemption), TODAY)


def test_investment_save_keeps_dates_splits_and_other_records():
    records = tracker.initial_records()
    save, writes, errors, caches = save_helper(tracker.encode_rows(records))
    item = investment('Cometa', contribution_start='2026-01-05', current_value=1100.)
    assert save(item['id'], item)
    output = [dict(zip(writes[0][0], row)) for row in writes[0][1:]]
    saved = tracker.decode_rows(output)
    assert saved[:-1] == records
    summary = tracker.investment_summary(saved[-1], TODAY)
    assert (summary['personal'], summary['company'], summary['total']) == (305.2, 686.6, 3192.0)
    assert len(caches) == 1 and not errors


def test_record_save_keeps_calendar_connection_settings():
    records=tracker.initial_records()
    save,writes,errors,caches=save_helper(tracker.encode_rows(records,dict(calendar_id='events@example.test')))
    replacement=dict(records[0],notes='Scadenza aggiornata')
    assert save(replacement['id'],replacement)
    output=[dict(zip(writes[0][0],row)) for row in writes[0][1:]]
    assert tracker.decode_settings(output)['calendar_id']=='events@example.test'
    assert tracker.decode_rows(output)[0]['notes']=='Scadenza aggiornata'
    assert not errors


def test_corrected_balance_adds_only_later_monthly_payments():
    item = investment('Moneyfarm', contribution_start='2022-05-20',
                      balance_date='2026-11-05', balance_personal=12100., balance_company=0.,
                      leonardo_personal=1000., current_value=15912.14, leonardo_value=940.99)
    item = tracker.validate_record(item, date(2026,11,5))
    assert tracker.investment_summary(item,date(2026,11,5))['total']==12100.
    assert tracker.investment_summary(item,date(2026,11,20))['total']==12200.
    assert tracker.investment_summary(item,date(2026,12,20))['total']==12300.
    # A larger one-off contribution corrects the actual balance, without
    # increasing the standing monthly payment or re-counting old instalments.
    item.update(balance_date='2026-11-20',balance_personal=12300.)
    assert tracker.investment_summary(item,date(2026,12,20))['total']==12400.
    restored=tracker.decode_rows(tracker.encode_rows([item]))[0]
    assert tracker.investment_summary(restored,date(2026,12,20))['total']==12400.


def test_closed_plan_and_titles_are_parts_of_total_not_added_twice():
    item = investment('Alleanza Assicurazioni',contribution_start='2024-12-01',
                      balance_date='2026-11-05',balance_personal=5431.,balance_company=0.,
                      closed_personal=2005.,closed_value=3200.,current_value=6500.)
    item=tracker.validate_record(item,date(2026,11,5))
    assert tracker.investment_summary(item,date(2026,11,5))['total']==5431.
    assert tracker.investment_summary(item,date(2026,12,1))['total']==5531.
    html=tracker.cards_html([item],'investments',date(2026,11,5))
    assert 'Risultato:' not in html
    assert 'Alleata Previdenza' in html and 'Smart Evergreen' in html and '€3.426,00' in html
    with pytest.raises(ValueError,match='controvalore totale'):
        tracker.validate_record(dict(item,closed_value=7000.),date(2026,11,5))
    with pytest.raises(ValueError,match='versato totale'):
        tracker.validate_record(dict(item,closed_personal=6000.),date(2026,11,5))


def test_alleanza_split_uses_valuation_date_and_only_evergreen_accumulates():
    item = investment('Alleanza Assicurazioni',contribution_start='2024-12-31',
                      balance_date='2026-11-06',balance_personal=4810.,balance_company=0.,
                      closed_personal=2510.,closed_value=3110.94,current_value=5407.22,
                      value_date='2026-11-06')
    item = tracker.validate_record(item,date(2026,11,6))
    current = tracker.cards_html([item],'investments',date(2026,11,6))
    assert '€4.810,00' in current and '€5.407,22' in current
    assert '€2.300,00' in current and '€2.296,28' in current
    assert '+€600,94' in current and '−€3,72' in current
    later = tracker.cards_html([item],'investments',date(2026,12,31))
    assert '€2.500,00' in later and '€2.510,00' in later
    assert '−€3,72' in later  # The stale valuation is not compared with later deposits.


def test_simple_investment_form_preserves_balance_and_saves_only_on_submit(tmp_path):
    from streamlit.testing.v1 import AppTest
    script=tmp_path/'simple_investment.py'
    script.write_text('''from datetime import date
import streamlit as st
import personal_trackers as t
if 'records' not in st.session_state:
    st.session_state.records=[t.investment_record(dict(id='mf',kind='investments',name='Moneyfarm',invested=12100.,current_value=15912.14,contribution_start='2022-05-20'))]
    st.session_state.saves=0
def save(identifier,item):
    st.session_state.records=t.merge_record(st.session_state.records,identifier,item)
    st.session_state.saves+=1
    return True
t.render_tracker(st.session_state.records,'investments',save,lambda _:None,date(2026,11,5))
''')
    at=AppTest.from_file(str(script)).run()
    at.selectbox(key='tracker_select_investments').set_value('mf').run()
    assert not at.exception
    assert [field.label for field in at.selectbox]==['Voce']
    next(field for field in at.number_input if field.label=='Versato da te a oggi (€)').set_value(12100.)
    next(field for field in at.number_input if field.label=='Controvalore Leonardo (€)').set_value(940.99)
    next(field for field in at.number_input if field.label=='Versato in Leonardo (€)').set_value(1000.)
    assert at.session_state.saves==0
    next(button for button in at.button if button.label=='💾 Salva').click().run()
    assert not at.exception
    assert at.session_state.saves==1
    record=at.session_state.records[0]
    assert record['balance_personal']==12100. and record['leonardo_personal']==1000.
    assert record['current_value']==15912.14
    assert tracker.investment_summary(record,date(2026,11,20))['total']==12200.


def test_moneyfarm_exact_pac_and_leonardo_breakdown():
    item=investment('Moneyfarm',contribution_start='2022-05-20',balance_date='2026-11-06',
                    balance_personal=12100.,balance_company=0.,pac_initial_personal=5000.,
                    leonardo_personal=967.03,leonardo_value=940.99,current_value=15912.14)
    item=tracker.validate_record(item,date(2026,11,6))
    html=tracker.cards_html([item],'investments',date(2026,11,6))
    assert all(value in html for value in ['€12.100,00','€11.132,97','€967,03','€14.971,15','€940,99','€5.000,00'])
    restored=tracker.decode_rows(tracker.encode_rows([item]))[0]
    assert restored['pac_initial_personal']==5000.
    assert '+€3.838,18' in html and '+34,48%' in html
    assert '−€26,04' in html and '-2,69%' in html
    assert 'holding-metrics holding-headline' in html and 'holding-result-compact' in html
    assert tracker.investment_summary(restored,date(2026,11,20))['total']==12200.
    with pytest.raises(ValueError,match='iniziale del PAC'):
        tracker.validate_record(dict(item,pac_initial_personal=12000.),date(2026,11,6))


def test_investment_performance_uses_valuation_date_and_handles_missing_values():
    item=investment('Moneyfarm',contribution_start='2022-05-20',balance_date='2026-11-06',
                    balance_personal=12100.,balance_company=0.,current_value=15912.14,value_date='2026-11-06')
    result=tracker.investment_performance(item,date(2026,12,21))
    assert result['amount']==3812.14 and result['percent']==pytest.approx(31.5052892562)
    assert not result['partial']
    html=tracker._performance_html(result)
    assert '+€3.812,14' in html and '+31,51%' in html and 'tracker-ink-gain' in html
    loss=tracker.investment_performance(dict(item,current_value=11000.),date(2026,11,6))
    assert loss['amount']==-1100.
    assert 'tracker-ink-loss' in tracker._performance_html(loss)
    assert tracker.investment_performance(dict(item,current_value=None),date(2026,11,6))['amount'] is None
    empty=investment('Moneyfarm',current_value=0.)
    assert tracker.investment_performance(empty,date(2026,11,6))['percent'] is None
    pension=investment('Cometa',opening_personal=100.,current_value=120.)
    assert tracker.investment_performance(pension,date(2026,11,6))['partial']
    assert 'Confronto parziale' in tracker._performance_html(tracker.investment_performance(pension,date(2026,11,6)))


@pytest.mark.parametrize('identifier',['investment-0','investment-1','investment-2','investment-3'])
def test_investment_form_accepts_integer_amounts_from_json(tmp_path,identifier):
    from streamlit.testing.v1 import AppTest
    script=tmp_path/'integer_investments.py'
    script.write_text('''from datetime import date
import streamlit as st
import personal_trackers as t
records=t.initial_records()
for item in records:
    if item['kind']!='investments':continue
    item.update(monthly_personal=100,monthly_company=70,monthly_tfr=220,
                opening_personal=1000,opening_company=200,opening_tfr=300,
                current_value=5000,withdrawn=0,pac_initial_personal=500,
                contribution_start='2026-01-20',leonardo_personal=967.03,leonardo_value=940.99)
    for row in item['annual_contributions']:row.update(personal=2000,company=500)
t.render_tracker(records,'investments',lambda *_:True,lambda _:None,date(2026,11,6))
''')
    at=AppTest.from_file(str(script)).run()
    at.selectbox(key='tracker_select_investments').set_value(identifier).run()
    assert not at.exception
    for widget in at.number_input:
        if widget.label!='Anno ritiro':
            assert widget.proto.data_type==1  # DOUBLE: integer JSON values use money inputs.
    assert next(widget for widget in at.number_input if widget.label=='Controvalore totale (€)').value==5000.
