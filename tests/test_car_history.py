from datetime import date
import pytest
import car_history as h
import personal_trackers as trackers

TODAY = date(2026,11,5)

def car(**values):
    return dict(id='auto-0',kind='auto',name='Bollo',interval_months=12,reminder_days=30,last_date=None,next_date=None,amount=None,notes='',**values)

def payment(year,amount,identifier=None):
    return dict(id=identifier or str(year),date=f'{year}-05-10',amount=amount,note='')

def test_migration_only_known_payment_and_no_reappearance_after_delete():
    old=car();old.update(last_date='2025-05-10',amount=210)
    migrated=h.car_record(old,TODAY)
    assert len(migrated['payments'])==1
    assert h.car_record(migrated,TODAY)==migrated
    assert h.with_payments(migrated,[],TODAY)['last_date'] is None
    assert h.car_record(h.with_payments(migrated,[],TODAY),TODAY)['payments']==[]
    for values in ({'amount':210},{'last_date':'2025-05-10'},{'last_date':'2027-05-10','amount':210}):
        record=car();record.update(values)
        assert h.car_record(record,TODAY)['payments']==[]

def test_annual_totals_include_multiple_payments_without_fake_missing_years():
    record=car(payments=[payment(2023,200),payment(2025,100),payment(2025,120,'second')])
    assert h.annual_totals(record)==[dict(year=2023,amount=200,count=1),dict(year=2025,amount=220,count=2)]
    svg=h.chart_html(record)
    assert '2024' not in svg
    assert '2025 €220,00' in svg
    from base64 import b64decode
    image=b64decode(svg.split('base64,')[1].split('"')[0]).decode()
    assert 'L' not in image.split('stroke-width="2.5"')[0].split('<path d="')[-1]

def test_confirmed_payment_replaces_forecast_without_double_counting():
    record=h.with_payments(car(),[payment(2025,210)],TODAY)
    record.update(next_date='2026-05-10',planned_amount=230)
    updated=h.with_payments(record,record['payments']+[payment(2026,240)],TODAY)
    assert [p['amount'] for p in updated['payments']]==[210,240]
    assert updated['next_date'] is None and updated['planned_amount'] is None
    assert trackers.car_deadline(updated)==date(2027,5,10)
    assert h.chart_rows(updated,TODAY)==h.annual_totals(updated)


def test_forecast_changes_color_after_deadline_without_becoming_a_payment():
    from base64 import b64decode
    record=h.with_payments(car(),[payment(2025,210)],TODAY)
    record.update(next_date='2026-11-20',planned_amount=230)
    original=record['payments'].copy()
    before=h.chart_html(record,TODAY)
    svg=b64decode(before.split('base64,')[1].split('"')[0]).decode()
    assert '#fb923c' in svg and 'stroke-dasharray' in svg
    assert 'Previsto: €230,00' in before
    after=h.chart_html(record,date(2026,11,21))
    svg=b64decode(after.split('base64,')[1].split('"')[0]).decode()
    assert '#fb923c' not in svg and h.card_color(record) in svg
    assert 'Da confermare: €230,00' in after
    assert record['payments']==original
    assert h.annual_totals(record)==[dict(year=2025,amount=210,count=1)]


def test_same_year_forecast_is_separate_from_paid_total():
    record=h.with_payments(car(),[payment(2026,30)],TODAY)
    record.update(next_date='2026-11-20',planned_amount=40)
    assert h.chart_rows(record,TODAY)==[dict(year=2026,amount=70,count=1,pending=40,future=True)]
    assert h.annual_totals(record)==[dict(year=2026,amount=30,count=1)]
    assert trackers.decode_rows(trackers.encode_rows([record]))==[record]


def test_history_edits_restore_latest_and_preserve_future_deadline():
    record=car();record['next_date']='2027-06-01'
    record=h.with_payments(record,[payment(2026,230),payment(2024,210)],TODAY)
    assert record['last_date']=='2026-05-10'
    assert record['amount']==230
    assert record['next_date']=='2027-06-01'
    record=h.with_payments(record,[payment(2024,210)],TODAY)
    assert record['last_date']=='2024-05-10'
    assert record['amount']==210
    record=h.with_payments(record,[],TODAY)
    assert record['last_date'] is None and record['amount'] is None
    assert record['next_date']=='2027-06-01'

@pytest.mark.parametrize('row',[
    dict(date=None,amount=20),dict(date='2025-01-01',amount=None),
    dict(date='2027-01-01',amount=20),dict(date='2025-01-01',amount=-1),
    dict(date='2025-01-01',amount=float('nan')),dict(date='2025-01-01',amount=float('inf')),
])
def test_invalid_payment_rejected(row):
    with pytest.raises(ValueError):h.validate_payments([row],TODAY)

def test_zero_and_duplicate_ids():
    assert h.validate_payments([payment(2025,0)],TODAY)[0]['amount']==0
    with pytest.raises(ValueError):h.validate_payments([payment(2025,10),payment(2025,20)],TODAY)

def test_storage_roundtrip_and_escaped_chart():
    record=h.with_payments(car(),[payment(2025,210),payment(2026,230)],TODAY)
    assert trackers.decode_rows(trackers.encode_rows([record]))==[record]
    record['name']='<script>hello</script>'
    assert '<script>' not in h.chart_html(record)
    assert '&lt;script&gt;' in h.chart_html(record)


def test_editor_add_correct_delete_through_save(tmp_path):
    from streamlit.testing.v1 import AppTest
    app=tmp_path/'car_editor_app.py'
    app.write_text('''from datetime import date
import streamlit as st
import car_history as h
if 'record' not in st.session_state:st.session_state.record=dict(id='auto-0',name='Bollo',payments=[])
def save(identifier,record):
    st.session_state.record=record
    return True
def saved(message):st.success(message)
h.render_editor([st.session_state.record],save,saved,date(2026,11,5))
''')
    at=AppTest.from_file(str(app)).run()
    assert not at.exception
    at.session_state['car_payment_editor_auto-0']={'edited_rows':{},'added_rows':[{'Data':'2025-05-10','Importo':'210,95','Nota':'Bollo 2025'}],'deleted_rows':[]}
    at.button[0].click().run()
    assert not at.exception
    assert at.session_state.record['payments'][0]['amount']==210.95
    identifier=at.session_state.record['payments'][0]['id']
    at.run()
    at.session_state['car_payment_editor_auto-0']={'edited_rows':{'0':{'Importo':'225,45'}},'added_rows':[],'deleted_rows':[]}
    at.button[0].click().run()
    assert at.session_state.record['payments'][0]['amount']==225.45
    assert at.session_state.record['payments'][0]['id']==identifier
    at.run()
    at.session_state['car_payment_editor_auto-0']={'edited_rows':{},'added_rows':[],'deleted_rows':[0]}
    at.button[0].click().run()
    assert at.session_state.record['payments']==[]


def test_upcoming_form_edits_only_forecast_and_saves_on_submit(tmp_path):
    from streamlit.testing.v1 import AppTest
    app=tmp_path/'upcoming_app.py'
    app.write_text('''from datetime import date
import streamlit as st
import personal_trackers as t
import car_history as h
if 'records' not in st.session_state:
    item=t.initial_records()[0]
    st.session_state.records=[h.with_payments(item,[dict(id='old',date='2025-11-20',amount=210,note='')],date(2026,11,5))]
    st.session_state.saves=0
def save(identifier,record):
    st.session_state.records=t.merge_record(st.session_state.records,identifier,record)
    st.session_state.saves+=1
    return True
def saved(message):st.success(message)
t.render_tracker(st.session_state.records,'auto',save,saved,date(2026,11,5))
''')
    at=AppTest.from_file(str(app)).run()
    at.selectbox(key='tracker_select_auto').set_value('auto-0').run()
    assert not at.exception
    assert [field.label for field in at.date_input]==['Prossima scadenza']
    assert 'Importo (€)' not in [field.label for field in at.number_input]
    original=at.session_state.records[0]['payments'].copy()
    at.date_input[0].set_value(date(2026,11,25))
    next(field for field in at.number_input if field.label=='Costo previsto (€)').set_value(230.)
    assert at.session_state.saves==0
    next(button for button in at.button if button.label=='💾 Salva').click().run()
    assert not at.exception
    assert at.session_state.saves==1
    record=at.session_state.records[0]
    assert record['payments']==original
    assert record['last_date']=='2025-11-20' and record['amount']==210
    assert record['next_date']=='2026-11-25' and record['planned_amount']==230


def test_forecast_requires_a_deadline():
    record=car();record['planned_amount']=100
    with pytest.raises(ValueError,match='scadenza'):
        trackers.validate_record(record,TODAY)


def test_early_payment_can_settle_forecast_without_losing_other_history():
    record=h.with_payments(car(),[payment(2025,210)],TODAY)
    record.update(next_date='2026-11-20',planned_amount=230)
    early=dict(id='early',date='2026-11-05',amount=225,note='Pagato in anticipo')
    updated=h.with_payments(record,record['payments']+[early],TODAY,settle=True)
    assert updated['planned_amount'] is None and updated['next_date'] is None
    assert trackers.car_deadline(updated)==date(2027,11,5)
    assert h.annual_totals(updated)==[dict(year=2025,amount=210,count=1),dict(year=2026,amount=225,count=1)]
    with pytest.raises(ValueError,match='Inserisci il pagamento'):
        h.with_payments(record,[],TODAY,settle=True)


@pytest.mark.parametrize('value,expected',[('193,96',193.96),('1.234,56',1234.56),('193.96',193.96),('€ 193,96',193.96),('0,50',.5),('210',210)])
def test_decimal_comma_payment_amounts(value,expected):
    record=h.with_payments(car(),[dict(id='decimal',date='2025-05-10',amount=value,note='')],TODAY)
    assert record['payments'][0]['amount']==expected
    assert trackers.decode_rows(trackers.encode_rows([record]))==[record]


def test_only_forecast_marker_is_orange_not_historical_curve():
    from base64 import b64decode
    import xml.etree.ElementTree as ET
    record=h.with_payments(car(),[payment(2024,200),payment(2025,210)],TODAY)
    record.update(next_date='2026-11-20',planned_amount=230)
    html=h.chart_html(record,TODAY)
    root=ET.fromstring(b64decode(html.split('base64,')[1].split('"')[0]))
    ns={'svg':'http://www.w3.org/2000/svg'}
    assert [circle.get('fill') for circle in root.findall('svg:circle',ns)]==[h.card_color(record),h.card_color(record),'#fb923c']
    curves=[path for path in root.findall('svg:path',ns) if path.get('stroke-width')=='2.5']
    assert len(curves)==2
    assert all(path.get('stroke')==h.card_color(record) for path in curves)
    assert curves[-1].get('stroke-dasharray')=='5 4'
