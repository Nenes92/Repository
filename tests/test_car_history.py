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
    assert [row for row in h.chart_rows(updated,TODAY) if 'pending' not in row]==h.annual_totals(updated)
    assert h.forecast(updated)==dict(date=date(2027,5,10),amount=240.,automatic=True)


def test_forecast_stays_orange_after_deadline_until_payment_is_recorded():
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
    assert '#fb923c' in svg and h.card_color(record) in svg
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
with st.form('car-test'):
    edited,settle=h.payment_fields(st.session_state.record,date(2026,11,5))
    submit=st.form_submit_button('Salva')
if submit:
    record=h.with_payments(st.session_state.record,h.payment_rows(edited),date(2026,11,5),settle)
    if save(record['id'],record):saved('Salvato')
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
    assert [field.label for field in at.date_input]==['Scadenza']
    assert at.date_input[0].proto.form_id
    assert next(field for field in at.text_input if field.label=='Nome').proto.form_id
    assert 'Importo (€)' not in [field.label for field in at.number_input]
    original=at.session_state.records[0]['payments'].copy()
    at.date_input[0].set_value(date(2026,11,25))
    next(field for field in at.number_input if field.label=='Costo previsto (€)').set_value(230.)
    assert at.session_state.saves==0
    next(button for button in at.button if button.label in {'💾 Salva','Salva modifiche'}).click().run()
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


@pytest.mark.parametrize('identifier',['auto-0','auto-1','auto-2','auto-3'])
def test_each_chart_has_distinct_base_and_same_orange_forecast(identifier):
    from base64 import b64decode
    import xml.etree.ElementTree as ET
    record=car();record['id']=identifier
    record=h.with_payments(record,[payment(2025,210)],TODAY)
    record.update(next_date='2026-11-20',planned_amount=230)
    html=h.chart_html(record,TODAY)
    root=ET.fromstring(b64decode(html.split('base64,')[1].split('"')[0]))
    circles=root.findall('{http://www.w3.org/2000/svg}circle')
    assert circles[0].get('fill')==h.card_color(record)
    assert circles[1].get('fill')=='#fb923c'
    assert h.card_color(record)!='#fb923c'


def test_unified_editor_saves_selected_car_history_and_forecast_together(tmp_path):
    from streamlit.testing.v1 import AppTest
    app=tmp_path/'unified_car_app.py'
    app.write_text('''from datetime import date
import streamlit as st
import personal_trackers as t
import car_history as h
if 'records' not in st.session_state:
    st.session_state.records=[h.with_payments(item,[dict(id='old-'+item['id'],date='2025-11-20',amount=210+index*100,note='')],date(2026,11,5)) for index,item in enumerate(t.initial_records()[:2])]
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
    assert 'Storico pagamenti · Auto' not in [e.label for e in at.expander]
    assert 'Voce dello storico' not in [e.label for e in at.selectbox]
    assert any('Storico pagamenti · Bollo' in e.value for e in at.markdown)
    at.session_state['car_payment_editor_auto-0']={'edited_rows':{},'added_rows':[{'Data':'2024-11-20','Importo':'193,96','Nota':''}],'deleted_rows':[]}
    next(field for field in at.number_input if field.label=='Costo previsto (€)').set_value(230.95)
    at.date_input[0].set_value(date(2026,11,25))
    assert at.session_state.saves==0
    next(button for button in at.button if button.label in {'💾 Salva','Salva modifiche'}).click().run()
    assert not at.exception
    bollo,insurance=at.session_state.records
    assert [row['amount'] for row in bollo['payments']]==[193.96,210]
    assert bollo['planned_amount']==230.95 and bollo['next_date']=='2026-11-25'
    assert len(insurance['payments'])==1 and insurance['amount']==310
    at.selectbox(key='tracker_select_auto').set_value('auto-1').run()
    assert any('Storico pagamenti · Assicurazione auto' in e.value for e in at.markdown)
    at.session_state['car_payment_editor_auto-1']={'edited_rows':{'0':{'Importo':'315,55'}},'added_rows':[],'deleted_rows':[]}
    next(button for button in at.button if button.label in {'💾 Salva','Salva modifiche'}).click().run()
    assert not at.exception
    assert at.session_state.records[0]==bollo
    assert at.session_state.records[1]['payments'][0]['amount']==315.55

    at.selectbox(key='tracker_select_auto').set_value('auto-0').run()
    at.session_state['car_payment_editor_auto-0']={'edited_rows':{'2':{'Importo':'229,95'}},'added_rows':[],'deleted_rows':[]}
    at.date_input[0].set_value(date(2027,11,25))
    next(field for field in at.number_input if field.label=='Costo previsto (€)').set_value(250.)
    assert not at.checkbox
    next(button for button in at.button if button.label=='✅ Registra pagamento').click().run()
    assert not at.exception
    record=at.session_state.records[0]
    assert len(record['payments'])==3
    assert record['amount']==229.95
    assert record['next_date']=='2027-11-25' and record['planned_amount']==250


def test_tyres_show_two_semesters_instead_of_one_annual_sum():
    from base64 import b64decode
    record=car();record.update(id='auto-3',name='Cambio gomme',next_date='2026-11-20',planned_amount=30)
    record=h.with_payments(record,[dict(id='spring25',date='2025-04-20',amount=50,note=''),dict(id='autumn25',date='2025-11-20',amount=50,note=''),dict(id='spring26',date='2026-04-20',amount=50,note='')],TODAY)
    rows=h.chart_rows(record,TODAY)
    assert [(row['year'],row['semester'],row['amount']) for row in rows]==[(2025,1,50),(2025,2,50),(2026,1,50),(2026,2,30)]
    assert [row.get('pending') for row in rows]==[None,None,None,30]
    html=h.chart_html(record,TODAY)
    assert 'Costi per semestre' in html
    svg=b64decode(html.split('base64,')[1].split('"')[0]).decode()
    assert 'Primavera 2026: €50,00' in svg
    assert 'Autunno 2026: €30,00' in svg
    assert '€80' not in svg
    assert svg.count('stroke-width="2.5"')==3
    assert h.annual_totals(record)[-1]['amount']==50


@pytest.mark.parametrize('check_date',[date(2026,11,4),date(2026,11,5),date(2026,11,6)])
def test_tax_forecast_is_orange_before_on_and_after_due_date(check_date):
    from base64 import b64decode
    import xml.etree.ElementTree as ET
    record=h.with_payments(car(),[payment(2025,194.33)],TODAY)
    record.update(next_date='2026-11-05',planned_amount=193.96)
    html=h.chart_html(record,check_date)
    root=ET.fromstring(b64decode(html.split('base64,')[1].split('"')[0]))
    circles=root.findall('{http://www.w3.org/2000/svg}circle')
    assert circles[0].get('fill')==h.card_color(record)
    assert circles[-1].get('fill')==h.FORECAST_COLOR
    card=trackers.cards_html([record],'auto',check_date)
    label='Costo previsto' if check_date<date(2026,11,5) else 'Costo da confermare'
    assert f'style="color:{h.FORECAST_COLOR}">{label}' in card


def test_missing_tyres_semester_is_not_a_zero_payment():
    record=car();record.update(id='auto-3',name='Cambio gomme')
    record=h.with_payments(record,[dict(id='one',date='2025-04-20',amount=50,note=''),dict(id='two',date='2026-04-20',amount=55,note='')],TODAY)
    assert [(row['year'],row['semester'],row['amount']) for row in h.chart_rows(record,TODAY) if 'pending' not in row]==[(2025,1,50),(2026,1,55)]


def test_automatic_forecast_uses_last_amount_and_preserves_manual_override():
    record=h.with_payments(car(),[payment(2025,215)],TODAY)
    assert h.forecast(record)==dict(date=date(2026,5,10),amount=215.,automatic=True)
    assert h.chart_rows(record,TODAY)[-1]['pending']==215
    record.update(next_date='2026-11-15',planned_amount=230.5)
    assert h.forecast(record)==dict(date=date(2026,11,15),amount=230.5,automatic=False)
    assert record['payments'][0]['amount']==215


def test_zero_forecast_is_preserved_and_no_price_is_invented():
    record=h.with_payments(car(),[payment(2025,215)],TODAY)
    record['planned_amount']=0
    assert h.forecast(record)['amount']==0
    fresh=trackers.inspection_record()
    assert h.forecast(fresh)==dict(date=date(2027,11,30),amount=None,automatic=True)
    assert h.chart_rows(fresh,TODAY)==[]
    paid=h.with_payments(fresh,[dict(id='inspection',date='2027-11-20',amount=80,note='')],date(2027,11,20),settle=True)
    assert h.forecast(paid)==dict(date=date(2029,11,20),amount=80.,automatic=True)


def test_legacy_register_gets_inspection_only_once_and_deletion_sticks():
    import json
    rows=trackers.encode_rows([car()])
    rows[0]['Dati']=json.dumps(dict(version=1,calendar_id='events-test'))
    migrated=trackers.decode_rows(rows)
    assert [record['id'] for record in migrated]==['auto-0','auto-4']
    saved=trackers.encode_rows(migrated,trackers.decode_settings(rows))
    assert trackers.decode_settings(saved)['calendar_id']=='events-test'
    assert trackers.decode_rows(saved)==migrated
    assert trackers.decode_rows(trackers.encode_rows([migrated[0]]))==[migrated[0]]


def test_every_tyres_point_has_season_and_year_labels():
    from base64 import b64decode
    import xml.etree.ElementTree as ET
    record=car();record.update(id='auto-3',name='Cambio gomme',interval_months=6)
    payments=[dict(id=f'{year}-{month}',date=f'{year}-{month:02d}-20',amount=50,note='') for year in [2023,2024,2025] for month in [4,11]]
    record=h.with_payments(record,payments,TODAY)
    record.update(next_date='2026-11-20',planned_amount=30)
    html=h.chart_html(record,TODAY)
    root=ET.fromstring(b64decode(html.split('base64,')[1].split('"')[0]))
    ns={'svg':'http://www.w3.org/2000/svg'}
    labels=[node for node in root.findall('svg:text',ns) if node.findall('svg:tspan',ns)]
    rows=h.chart_rows(record,TODAY)
    assert len(labels)==len(rows)==len(root.findall('svg:circle',ns))
    assert [''.join(node.itertext()) for node in labels]==['Prim.2023','Aut.2023','Prim.2024','Aut.2024','Prim.2025','Aut.2025','Aut.2026']
    assert h.period_label(rows[-1],compact=True)=='Aut. 2026'
    assert '1S' not in html and '2S' not in html


def test_lifetime_total_counts_only_paid_history_not_forecasts():
    record=car()
    record=h.with_payments(record,[dict(id='one',date='2024-11-08',amount=194.87,note=''),dict(id='two',date='2025-11-08',amount=194.33,note='')],TODAY)
    record.update(next_date='2026-11-05',planned_amount=193.96)
    assert h.total_paid(record)==389.20
    assert h.total_paid(trackers.inspection_record())==0
    legacy=car();legacy.update(last_date='2025-11-08',amount=194.33)
    assert h.total_paid(legacy)==194.33
    html=trackers.cards_html([record],'auto',TODAY)
    assert 'Totale pagato' in html and '€389,20' in html
    corrected=h.with_payments(record,record['payments'][:1],TODAY)
    assert h.total_paid(corrected)==194.87


def test_forecast_appears_only_inside_configured_notice_window():
    record=h.with_payments(car(),[dict(id='last',date='2025-11-20',amount=210,note='')],TODAY)
    assert not h.forecast_visible(record,date(2026,10,20))
    assert h.forecast_visible(record,date(2026,10,21))
    assert h.forecast_visible(record,date(2026,11,21))
    assert all('pending' not in row for row in h.chart_rows(record,date(2026,10,20)))
    assert h.chart_rows(record,date(2026,10,21))[-1]['pending']==210
    # Hidden forecasts still supply the annual saving estimate and Calendar.
    assert h.forecast(record)['amount']==210
    record['reminder_days']=7
    assert not h.forecast_visible(record,date(2026,11,12))
    assert h.forecast_visible(record,date(2026,11,13))
    html=trackers.cards_html([record],'auto',date(2026,11,13))
    assert '⚠️ Da pagare' in html
    assert '🚨 Scaduta' in trackers.cards_html([record],'auto',date(2026,11,21))


def test_prefilled_payment_is_only_paid_after_explicit_confirmation():
    record=h.with_payments(car(),[dict(id='last',date='2025-11-20',amount=210,note='')],TODAY)
    frame,draft=h.payment_frame(record,TODAY)
    assert len(frame)==2 and frame.iloc[-1]['Stato']=='Da pagare'
    assert frame.iloc[-1]['Data'].date()==TODAY
    assert len(record['payments'])==1
    rows,confirmed=h.submitted_payments(frame,draft,False)
    assert not confirmed and len(rows)==1
    frame.loc[1,'Importo']='229,95'
    rows,confirmed=h.submitted_payments(frame,draft,True)
    updated=h.with_payments(record,rows,TODAY,settle=confirmed)
    assert len(updated['payments'])==2 and updated['amount']==229.95
    assert all(not row['id'].startswith('forecast-') for row in updated['payments'])
    assert h.next_due(updated)==date(2027,11,5)
    assert not h.forecast_visible(updated,TODAY)
    assert all('pending' not in row for row in h.chart_rows(updated,TODAY))
    assert h.total_paid(updated)==439.95
    _,next_draft=h.payment_frame(updated,TODAY)
    assert next_draft is None


def test_new_early_manual_row_settles_deadline_without_checkbox():
    record=h.with_payments(car(),[payment(2025,419)],TODAY)
    record.update(next_date='2026-11-10',planned_amount=419.)
    added=dict(id='paid',date='2026-11-05',amount=249.27,note='')
    updated=h.with_payments(record,record['payments']+[added],TODAY)
    assert updated['next_date'] is None and updated['planned_amount'] is None
    assert h.next_due(updated)==date(2027,11,5)
    assert h.total_paid(updated)==668.27


def test_confirmation_does_not_duplicate_a_manually_added_payment():
    record=car(payments=[]);record.update(next_date='2026-11-10',planned_amount=200.)
    frame,draft=h.payment_frame(record,TODAY)
    frame.loc[1]=[None,TODAY,'200,00','','']
    with pytest.raises(ValueError,match='una sola riga'):
        h.submitted_payments(frame,draft,True)


def test_prefilled_payment_can_be_confirmed_without_adding_a_row(tmp_path):
    from streamlit.testing.v1 import AppTest
    app=tmp_path/'confirm_prefilled_payment.py'
    app.write_text('''from datetime import date
import streamlit as st
import personal_trackers as t
import car_history as h
if 'records' not in st.session_state:
    item=t.initial_records()[0]
    st.session_state.records=[h.with_payments(item,[dict(id='last',date='2025-11-20',amount=210,note='')],date(2026,11,5))]
    st.session_state.saves=0
def save(identifier,record):
    st.session_state.records=t.merge_record(st.session_state.records,identifier,record)
    st.session_state.saves+=1
    return True
t.render_tracker(st.session_state.records,'auto',save,lambda _:None,date(2026,11,5))
''')
    at=AppTest.from_file(str(app)).run()
    at.selectbox(key='tracker_select_auto').set_value('auto-0').run()
    assert not at.exception and not at.checkbox
    assert at.session_state.saves==0
    next(button for button in at.button if button.label=='✅ Registra pagamento').click().run()
    assert not at.exception and at.session_state.saves==1
    record=at.session_state.records[0]
    assert len(record['payments'])==2
    assert record['payments'][-1]['date']=='2026-11-05'
    assert record['payments'][-1]['amount']==210
    at.run()
    assert all(button.label!='✅ Registra pagamento' for button in at.button)
