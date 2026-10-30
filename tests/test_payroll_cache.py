import ast
from datetime import date
from pathlib import Path
import pandas as pd
import pytest
import streamlit as st
from payroll_engine import DEFAULT_RULES,Shift,calculate_month_variables


def test_monthly_cache_reuses_only_matching_turns_rules_and_engine_version():
    tree=ast.parse((Path(__file__).parents[1]/'spese_mensili.py').read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_cached_payroll_variables_by_month')
    calls=[]
    def calculate(shifts,rules):
        calls.append(True)
        return calculate_month_variables(shifts,rules)
    ns={'st':st,'calculate_month_variables':calculate,
        '_payroll_shifts_from_df':lambda df:[Shift(date(2026,10,27),'Mattina',paid_leave_minutes=int(df.iloc[0]['leave']))]}
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'payroll-cache','exec'),ns)
    cache=ns['_cached_payroll_variables_by_month'];cache.clear()
    data=pd.DataFrame({'leave':[0]});rules=dict(DEFAULT_RULES,indennita_nette=True)
    first=cache(data,rules,1)['2026-10']
    assert cache(data.copy(),dict(rules),1)['2026-10']==first and len(calls)==1
    leave=cache(pd.DataFrame({'leave':[60]}),rules,1)['2026-10']
    assert leave.premiums_gross < first.premiums_gross and len(calls)==2
    changed=cache(data,dict(rules,paga_oraria_lorda=30.),1)['2026-10']
    assert changed.premiums_gross > first.premiums_gross and len(calls)==3
    cache(data,rules,2)
    assert len(calls)==4
    gross=cache(data,dict(rules,indennita_nette=False),1)['2026-10']
    assert gross.allowances_net==0 and gross.allowances_gross>0 and len(calls)==5
    cache.clear()


@pytest.mark.parametrize('hour,next_day,last_day', [(1,30,29),(6,31,30)])
def test_nearest_shift_selection_keeps_overnight_boundaries(hour,next_day,last_day):
    from datetime import datetime,timedelta
    tree=ast.parse((Path(__file__).parents[1]/'spese_mensili.py').read_text())
    names={'_nearest_turni_shift_times','_shift_bounds','_dt_for_turno'}
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    hours=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
               and any(isinstance(t,ast.Name) and t.id=='TURNI_ORARI' for t in n.targets))
    ns=dict(pd=pd,datetime=datetime,timedelta=timedelta,TURNI_ORARI=hours)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'nearest-shift','exec'),ns)
    data=pd.DataFrame([
        {'Data':'2026-10-31','Turno':'Mattina'},
        {'Data':'2026-10-29','Turno':'Notte'},
        {'Data':'2026-10-28','Turno':'Notte'},
        {'Data':'2026-10-30','Turno':'Mattina'},
        {'Data':'2026-11-01','Turno':'Ferie'},
    ])
    next_start,last_end=ns['_nearest_turni_shift_times'](data,datetime(2026,10,30,hour))
    assert next_start==datetime(2026,10,next_day,6)
    assert last_end==datetime(2026,10,last_day,6)
