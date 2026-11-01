import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
import pytest
import salary_extras as extras
import personal_trackers as trackers


def app_functions(*names):
    path = Path(__file__).parents[1]/'spese_mensili.py'
    nodes = [n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name in names]
    return compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec')


def test_declared_defaults_are_year_specific_and_future_prize_unknown():
    assert extras.for_year(extras.initial_rows(),2026,'production')['Importo'] == 3500.
    old = extras.for_year(extras.initial_rows(),2025,'production')
    assert old['Importo']==2800. and old['Stato']=='Indicativo'
    assert extras.for_year([],2027,'production')['Importo'] is None
    assert extras.for_year([],2026,'welfare')['Tipo importo']=='Credito welfare'
    assert extras.for_year([],2025,'welfare')['Importo'] is None


def test_edited_year_preserves_other_years_and_blank_stays_blank():
    rows=extras.initial_rows(); original=deepcopy(rows)
    item=extras.for_year(rows,2026,'production');item['Importo']=4000.;item['Mese erogazione']=8
    merged=extras.merge_rows(rows,[item])
    assert extras.for_year(merged,2025,'production')==original[0]
    assert extras.for_year(merged,2026,'production')['Mese erogazione']==8
    item['Importo']=''
    merged=extras.decode_rows(extras.merge_rows(merged,[item]))
    assert extras.for_year(merged,2026,'production')['Importo'] is None
    assert rows==original


@pytest.mark.parametrize('change',[{'Importo':-1},{'Importo':float('inf')},{'Mese erogazione':13},{'Anno':2026.5},{'Tipo importo':'unknown'}])
def test_invalid_data_rejected(change):
    row=extras.default_row(2026,'production');row.update(change)
    with pytest.raises(ValueError):extras.validate_row(row)


def test_markers_use_recorded_salary_positions_without_mutating_salaries():
    frame=pd.DataFrame([{'Mese':'2026-02-01','Mese_str':'Feb 2026','Stipendio':2500.},
                        {'Mese':'2026-07-01','Mese_str':'Jul 2026','Stipendio':2800.}])
    original=frame.copy(deep=True)
    ns=dict(pd=pd,_salary_extras=extras,_money_turni=lambda amount:f'€{amount:.2f}')
    exec(app_functions('_salary_extra_markers'),ns)
    markers=ns['_salary_extra_markers'](frame,extras.initial_rows())
    assert len(markers)==2
    assert markers.loc[markers.Tipo=='production','Stipendio'].iloc[0]==2800.
    assert markers.loc[markers.Tipo=='production','Importo'].iloc[0]==3500.
    assert markers.loc[markers.Tipo=='welfare','Importo'].iloc[0]==250.
    pd.testing.assert_frame_equal(frame,original)
    assert ns['_salary_extra_markers'](frame,None).empty


def test_save_merges_fresh_data_and_failure_preserves_cache():
    existing=extras.initial_rows();existing[0]['Note']='Changed in another session'
    edited=extras.default_row(2027,'production');edited['Importo']=3700.
    for fail in [False,True]:
        writes=[];caches=[];errors=[]
        def update(**kwargs):
            if fail:raise RuntimeError('offline')
            writes.append(kwargs['values'])
        ws=SimpleNamespace(get_all_records=lambda:deepcopy(existing),update=update)
        ns=dict(_salary_extras=extras,get_gsheet_client=lambda:object(),get_or_create_worksheet=lambda *a:ws,
                SHEET_URL='mock',st=SimpleNamespace(error=errors.append),pd=pd,
                _mobile_sheet_records=SimpleNamespace(clear=lambda *a:None),_set_gsheets_cache=lambda *a:caches.append(a))
        exec(app_functions('_save_salary_extras'),ns)
        assert ns['_save_salary_extras']([edited]) is (not fail)
        if fail:assert not caches and not writes and errors
        else:
            rows=[dict(zip(writes[0][0],row)) for row in writes[0][1:]]
            assert extras.for_year(rows,2025,'production')['Note']=='Changed in another session'
            assert extras.for_year(rows,2027,'production')['Importo']==3700.
            assert len(caches)==1


def test_personal_only_pacs_and_moneyfarm_name_are_migrated_idempotently():
    for name in ['Moneyfarm','Alleanza Assicurazioni']:
        record=trackers.investment_record(dict(id='pac',kind='investments',name=name,invested=None,
                                             opening_personal=500.,opening_company=20.,monthly_personal=100.,monthly_company=0.))
        assert record['personal_only'] and record['opening_personal']==520. and record['opening_company']==0.
        assert trackers.investment_record(record)==record
        if name=='Moneyfarm':assert record['name']=='Moneyfarm · PAC 7' and record['product']=='PAC · rischio 7'
        markup=trackers.cards_html([record],'investments',pd.Timestamp('2026-11-01').date())
        assert 'Azienda' not in markup and 'azienda' not in markup


def test_jp_morgan_2026_premium_source_seed_does_not_overwrite_edits():
    item=next(row for row in trackers.initial_records() if row['name']=='Leonardo · JP Morgan')
    row=next(row for row in item['annual_contributions'] if row['year']==2026)
    assert row['personal']==2000. and row['source']=='Premio di produzione'
    row['personal']=None;row['source']='Fonte aggiornata'
    item=trackers.validate_record(item,pd.Timestamp('2026-11-01').date())
    reread=trackers.decode_rows(trackers.encode_rows([item]))[0]
    row=next(row for row in reread['annual_contributions'] if row['year']==2026)
    assert row['personal'] is None and row['source']=='Fonte aggiornata'


def test_nullable_cached_amounts_load_without_becoming_invalid_nan():
    rows=extras.initial_rows()+[extras.default_row(2027,'production')]
    frame=pd.DataFrame(rows)
    assert pd.isna(frame.iloc[-1]['Importo'])
    errors=[]
    ns=dict(pd=pd,_salary_extras=extras,load_data_gsheets=lambda *a:frame,st=SimpleNamespace(error=errors.append))
    exec(app_functions('_load_salary_extras'),ns)
    loaded=ns['_load_salary_extras']()
    assert not errors and extras.for_year(loaded,2027,'production')['Importo'] is None
    assert loaded[0]['Mese erogazione']==7
    assert 'Mese' not in extras.HEADERS  # Generic Sheets loading parses that column as timestamps.


def test_known_2026_share_deposit_counts_without_inventing_an_exact_date():
    from datetime import date
    item=next(row for row in trackers.initial_records() if row['name']=='Leonardo · JP Morgan')
    summary=trackers.investment_summary(item,date(2026,11,1))
    assert summary['personal']==2000. and summary['total']==2000.
    row=next(row for row in item['annual_contributions'] if row['year']==2026)
    assert row['date'] is None and row['confirmed']
    row['year']=2027
    with pytest.raises(ValueError,match='futuro'):
        trackers.validate_record(item,date(2026,11,1))
