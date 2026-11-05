import ast
from pathlib import Path
from streamlit.testing.v1 import AppTest


def test_calendar_is_invisible_and_runs_only_after_auto_save(tmp_path):
    source=(Path(__file__).parents[1]/'spese_mensili.py').read_text()
    names={'_save_personal_tracker_record','_sync_auto_calendar_after_save'}
    functions='\n\n'.join(ast.get_source_segment(source,node) for node in ast.parse(source).body if isinstance(node,ast.FunctionDef) and node.name in names)
    script=tmp_path/'calendar_ui.py'
    script.write_text('''import streamlit as st
from types import SimpleNamespace
from datetime import datetime
import pandas as pd
import personal_trackers as _personal_trackers
import car_calendar as calendar
SHEET_URL='mock-sheet'
CREDENTIALS_INFO={'client_email':'app@example.test'}
st.session_state.setdefault('rows',_personal_trackers.encode_rows(_personal_trackers.initial_records(),{'calendar_id':'events@example.test'}))
st.session_state.setdefault('sync_calls',0)
st.session_state.setdefault('fail_calendar',False)
def sync(*args):
    st.session_state.sync_calls+=1
    if st.session_state.fail_calendar:raise RuntimeError('offline')
_car_calendar=SimpleNamespace(sync_events=sync,sync_error_message=calendar.sync_error_message)
def _get_auto_calendar_session(*args):return object()
def _now_italy():return datetime(2026,11,5,19,30)
class Worksheet:
    def get_all_records(self):return st.session_state.rows
    def update(self,*args,**kwargs):
        values=kwargs['values']
        st.session_state.rows=[dict(zip(values[0],row)) for row in values[1:]]
def get_gsheet_client():return object()
def get_or_create_worksheet(*args):return Worksheet()
_mobile_sheet_records=SimpleNamespace(clear=lambda *args:None)
def _set_gsheets_cache(*args):pass
def saved(message):st.rerun()
'''+functions+"\n_personal_trackers.render_tracker(_personal_trackers.decode_rows(st.session_state.rows),'auto',_save_personal_tracker_record,saved,_now_italy().date())\n")
    at=AppTest.from_file(str(script)).run()
    assert not at.exception
    assert at.session_state.sync_calls==0
    assert all('Calendar' not in button.label and button.label!='💾 Salva collegamento' for button in at.button)
    assert all('Google Calendar' not in expander.label for expander in at.expander)
    assert all(field.label!='ID del calendario Eventi' for field in at.text_input)
    at.selectbox(key='tracker_select_auto').set_value('auto-0').run()
    assert at.session_state.sync_calls==0
    at.run()
    assert at.session_state.sync_calls==0
    at.text_area[0].set_value('Bollo aggiornato')
    assert at.session_state.sync_calls==0
    next(button for button in at.button if button.label=='💾 Salva').click().run()
    assert not at.exception
    assert at.session_state.sync_calls==1
    at.run()
    assert at.session_state.sync_calls==1
    at.session_state.fail_calendar=True
    at.text_area[0].set_value('Spesa salvata anche se Calendar non risponde')
    next(button for button in at.button if button.label=='💾 Salva').click().run()
    assert not at.exception
    assert at.session_state.sync_calls==2
    assert 'Spesa salvata.' in at.session_state.auto_calendar_save_notice
    assert any(row['Nome']=='Bollo' and 'Spesa salvata anche' in row['Dati'] for row in at.session_state.rows)
    at.run()
    assert at.session_state.sync_calls==2
