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

def test_last_payment_save_preserves_history_and_corrects_without_duplicates():
    old=h.with_payments(car(),[payment(2025,210)],TODAY)
    updated=dict(old,last_date='2026-05-10',amount=230,next_date='2026-05-10')
    updated=h.sync_current(updated,old,TODAY)
    assert [p['amount'] for p in updated['payments']]==[210,230]
    assert updated['next_date'] is None
    assert trackers.car_deadline(updated)==date(2027,5,10)
    corrected=h.sync_current(dict(updated,amount=240),updated,TODAY)
    assert [p['amount'] for p in corrected['payments']]==[210,240]
    assert corrected['payments'][-1]['id']==updated['payments'][-1]['id']
    assert h.sync_current(corrected,corrected,TODAY)==corrected

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
    at.session_state['car_payment_editor_auto-0']={'edited_rows':{},'added_rows':[{'Data':'2025-05-10','Importo':210,'Nota':'Bollo 2025'}],'deleted_rows':[]}
    at.button[0].click().run()
    assert not at.exception
    assert at.session_state.record['payments'][0]['amount']==210
    identifier=at.session_state.record['payments'][0]['id']
    at.run()
    at.session_state['car_payment_editor_auto-0']={'edited_rows':{'0':{'Importo':225}},'added_rows':[],'deleted_rows':[]}
    at.button[0].click().run()
    assert at.session_state.record['payments'][0]['amount']==225
    assert at.session_state.record['payments'][0]['id']==identifier
    at.run()
    at.session_state['car_payment_editor_auto-0']={'edited_rows':{},'added_rows':[],'deleted_rows':[0]}
    at.button[0].click().run()
    assert at.session_state.record['payments']==[]
