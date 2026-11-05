from copy import deepcopy
from datetime import date
from urllib.parse import unquote
import pytest
import car_calendar as cal
import car_history as history
import personal_trackers as tracker


def records():
    item=tracker.initial_records()[2]
    return [history.with_payments(item,[dict(id='paid',date='2025-11-08',amount=215,note='Tagliando')],date(2026,11,5))]


class Response:
    def __init__(self,data=None,status=200):self.data=data or {};self.status_code=status
    def json(self):return deepcopy(self.data)
    def raise_for_status(self):
        if self.status_code>=400:raise RuntimeError(str(self.status_code))


class Session:
    def __init__(self):self.store={};self.calls=[];self.fail=False;self.race=False
    def get(self,url,**kwargs):
        self.calls.append(('get',url,kwargs))
        if self.fail:return Response(status=403)
        identifier=url.rsplit('/',1)[-1]
        if identifier!='events':return Response(self.store[identifier])
        return Response({'items':list(self.store.values())})
    def post(self,url,json,**kwargs):
        self.calls.append(('post',url,kwargs))
        identifier=json['id']
        if identifier in self.store:return Response(status=409)
        self.store[identifier]=deepcopy(json)
        if self.race:self.race=False;return Response(status=409)
        return Response(json)
    def patch(self,url,json,**kwargs):
        self.calls.append(('patch',url,kwargs))
        identifier=url.rsplit('/',1)[-1]
        self.store[identifier].update(deepcopy(json))
        return Response(self.store[identifier])
    def delete(self,url,**kwargs):
        self.calls.append(('delete',url,kwargs))
        self.store.pop(url.rsplit('/',1)[-1],None)
        return Response()


def test_all_day_events_cover_history_and_automatic_future_without_guessing_cost():
    items=records()+[tracker.inspection_record()]
    events=cal.event_bodies(items,'sheet-one')
    assert len(events)==3
    paid=next(event for event in events.values() if event['summary'].endswith('pagato'))
    assert paid['start']=={'date':'2025-11-08'} and paid['end']=={'date':'2025-11-09'}
    estimate=next(event for event in events.values() if 'Tagliando' in event['summary'] and event['summary'].endswith('da fare'))
    assert estimate['start']=={'date':'2026-11-08'} and estimate['colorId']=='6'
    assert '€215,00' in estimate['description'] and 'Stima basata' in estimate['description']
    inspection=next(event for event in events.values() if 'Revisione' in event['summary'])
    assert inspection['start']=={'date':'2027-11-30'} and 'Importo da definire' in inspection['description']
    assert inspection['recurrence']==['RRULE:FREQ=MONTHLY;INTERVAL=24']
    assert estimate['recurrence']==['RRULE:FREQ=MONTHLY;INTERVAL=12']
    assert paid['recurrence']==[]
    assert all('dateTime' not in event['start'] for event in events.values())


def test_repeated_sync_does_not_duplicate_or_rewrite_unchanged_events():
    session=Session();items=records()
    assert cal.sync_events(session,'events@example.test',items,'sheet-one')==dict(created=2,updated=0,removed=0)
    session.calls=[]
    assert cal.sync_events(session,'events@example.test',items,'sheet-one')==dict(created=0,updated=0,removed=0)
    assert [call[0] for call in session.calls]==['get']
    params=session.calls[0][2]['params']
    assert ('privateExtendedProperty','speseApp='+cal.APP) in params
    assert ('privateExtendedProperty','speseScope=sheet-one') in params


def test_edit_moves_event_in_place_and_deletion_only_removes_owned_events():
    session=Session();items=records();cal.sync_events(session,'cal',items,'scope')
    identifiers=set(session.store)
    session.store['unrelated']=dict(id='unrelated',summary='Appuntamento personale')
    session.store['other-app']=dict(id='other-app',extendedProperties={'private':dict(speseApp=cal.APP,speseScope='other-sheet')})
    items[0]['payments'][0].update(date='2025-11-10',amount=220)
    items[0].update(last_date='2025-11-10',amount=220)
    result=cal.sync_events(session,'cal',items,'scope')
    assert result==dict(created=0,updated=2,removed=0)
    assert identifiers.issubset(session.store)
    assert 'unrelated' in session.store and 'other-app' in session.store
    assert cal.sync_events(session,'cal',[],'scope')['removed']==2
    assert set(session.store)=={'unrelated','other-app'}


def test_failure_does_not_delete_existing_events_and_retry_is_safe():
    session=Session();items=records();session.race=True
    assert cal.sync_events(session,'cal',items,'scope')['created']==1
    before=deepcopy(session.store)
    session.fail=True
    with pytest.raises(RuntimeError):cal.sync_events(session,'cal',[],'scope')
    assert session.store==before
    session.fail=False
    assert cal.sync_events(session,'cal',items,'scope')==dict(created=0,updated=0,removed=0)


def test_source_scope_isolates_ids_and_model_is_not_mutated():
    items=records();before=deepcopy(items)
    one=cal.event_bodies(items,'one');two=cal.event_bodies(items,'two')
    assert not set(one)&set(two)
    assert all(set(identifier)<=set('0123456789abcdefghijklmnopqrstuv') for identifier in one)
    assert items==before


def test_disabling_repetition_removes_calendar_recurrence():
    session=Session();items=records();items[0]['next_date']='2026-11-08'
    cal.sync_events(session,'cal',items,'scope')
    items[0]['interval_months']=0
    assert cal.sync_events(session,'cal',items,'scope')['updated']==1
    future=next(event for event in session.store.values() if event['summary'].endswith('da fare'))
    assert future['recurrence']==[]


@pytest.mark.parametrize('status,body,reason',[(403,{'error':{'errors':[{'reason':'accessNotConfigured'}]}},'api_disabled'),(403,{'error':{'status':'SERVICE_DISABLED'}},'api_disabled'),(403,{'error':{'message':'Forbidden'}},'permission'),(404,{},'calendar_missing'),(401,{},'authentication'),(500,{},'other')])
def test_sync_failures_have_specific_safe_messages(status,body,reason):
    from types import SimpleNamespace
    exc=RuntimeError('sensitive raw response')
    exc.response=SimpleNamespace(status_code=status,json=lambda:body)
    code,message=cal.sync_error_message(exc)
    assert code==reason
    assert 'sensitive raw response' not in message


def test_connection_save_persists_id_and_clears_previous_sync_failure():
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    import pandas as pd
    rows=tracker.encode_rows(tracker.initial_records())
    writes=[];errors=[]
    ws=SimpleNamespace(get_all_records=lambda:deepcopy(rows),update=lambda **kwargs:writes.append(kwargs['values']))
    state=dict(auto_calendar_failed_token='old',auto_calendar_sync_error='old error',auto_calendar_sync_token='old')
    source=Path(__file__).parents[1]/'spese_mensili.py'
    node=next(node for node in ast.parse(source.read_text()).body if isinstance(node,ast.FunctionDef) and node.name=='_save_auto_calendar_settings')
    ns=dict(st=SimpleNamespace(session_state=state,error=errors.append),_personal_trackers=tracker,get_gsheet_client=lambda:object(),get_or_create_worksheet=lambda *args:ws,SHEET_URL='mock',_mobile_sheet_records=SimpleNamespace(clear=lambda *args:None),_set_gsheets_cache=lambda *args:None,pd=pd)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns)
    assert ns['_save_auto_calendar_settings'](' events@example.test ')
    output=[dict(zip(writes[0][0],row)) for row in writes[0][1:]]
    assert tracker.decode_settings(output)['calendar_id']=='events@example.test'
    assert tracker.decode_rows(output)==tracker.initial_records()
    assert state['auto_calendar_settings']['calendar_id']=='events@example.test'
    assert not any(key in state for key in ('auto_calendar_failed_token','auto_calendar_sync_error','auto_calendar_sync_token'))
    assert not errors
