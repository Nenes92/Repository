import ast
from pathlib import Path
from streamlit.testing.v1 import AppTest


def test_sync_button_is_outside_settings_and_retries_only_on_click(tmp_path):
    source=(Path(__file__).parents[1]/'spese_mensili.py').read_text()
    node=next(node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef) and node.name=='_render_auto_calendar')
    function=ast.get_source_segment(source,node)
    script=tmp_path/'calendar_ui.py'
    script.write_text('''import streamlit as st
from types import SimpleNamespace
from datetime import datetime
import car_calendar as calendar
SHEET_URL='mock-sheet'
CREDENTIALS_INFO={'client_email':'app@example.test'}
st.session_state.setdefault('auto_calendar_settings',{})
st.session_state.setdefault('sync_calls',0)
def sync(*args):st.session_state.sync_calls+=1
_car_calendar=SimpleNamespace(event_bodies=calendar.event_bodies,fingerprint=calendar.fingerprint,sync_events=sync,sync_error_message=calendar.sync_error_message)
def _get_auto_calendar_session(*args):return object()
def _now_italy():return datetime(2026,11,5,19,30)
def _save_auto_calendar_settings(value):
    st.session_state.auto_calendar_settings={'calendar_id':value}
    return True
def _mobile_save_refresh(message):st.rerun()
'''+function+"\n_render_auto_calendar([])\n")
    at=AppTest.from_file(str(script))
    at.secrets['GOOGLE_CALENDAR_AUTO_ID']=''
    at.run()
    assert not at.exception
    button=next(button for button in at.button if button.label=='🔄 Sincronizza Google Calendar')
    assert button.disabled
    assert all(button.label!='🔄 Sincronizza Google Calendar' for button in at.expander[0].button)
    assert at.session_state.sync_calls==0
    at.text_input[0].set_value('events@example.test')
    next(button for button in at.button if button.label=='💾 Salva collegamento').click().run()
    assert not at.exception
    assert not next(button for button in at.button if button.label=='🔄 Sincronizza Google Calendar').disabled
    assert at.session_state.sync_calls==1
    at.run()
    assert at.session_state.sync_calls==1
    next(button for button in at.button if button.label=='🔄 Sincronizza Google Calendar').click().run()
    assert not at.exception
    assert at.session_state.sync_calls==2
