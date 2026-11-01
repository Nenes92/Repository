from copy import deepcopy
from datetime import date
from decimal import Decimal
from dataclasses import replace
import pytest
from payslip_drive import Payslip, parse_payslip_pension_items, collect_payslips
import personal_trackers as trackers
import salary_extras as extras

TODAY=date(2026,11,1)
ROWS='''4 N_IT V_70004 Ctr.Ord.Prev.Compl.C/Dip.: COMETA 10/2026 31,25
3 N_IT V_70006 Ctr.Ord.Prev.Compl.C/Azienda: 10/2026 69,75
Contributo C/Dip. 301,50
Contributo C/AZ 678,35
Quota TFR dest. F.P.C 220,02
3 N_IT V_03010 Contributo Metasalute Azienda 10/2026 23,34'''


def test_parser_only_reads_monthly_paired_cometa_rows_and_not_cumulative_or_tfr():
    rows=parse_payslip_pension_items(ROWS+'\n'+ROWS)
    assert [(row.side,row.competence_month,row.amount) for row in rows]==[
        ('personal','2026-10',Decimal('31.25')),('company','2026-10',Decimal('69.75'))]
    assert not parse_payslip_pension_items(ROWS.replace('COMETA','ALTRO FONDO'))
    assert not parse_payslip_pension_items('V_70006 Ctr.Ord.Prev.Compl.C/Azienda: 10/2026 69,75')


def test_collection_deduplicates_payslip_copies_and_keeps_net_unchanged():
    slip=Payslip('2026-10',Decimal('2500'),'position',pension_items=parse_payslip_pension_items(ROWS))
    files=[dict(id='1',name='2026_10_CED_ORD_SAMPLE.pdf'),dict(id='2',name='2026_10_CED_ORD_COPY.pdf')]
    result=collect_payslips(files,lambda _:slip)
    assert result['salaries']=={'2026-10':2500.}
    assert len(result['pension_details']['2026-10'])==2
    rejected=collect_payslips(files,lambda item:slip if item['id']=='1' else replace(slip,net=Decimal('2400')))
    assert not rejected['pension_details']


def record():
    return next(row for row in trackers.initial_records() if row['name']=='Cometa')


def history():
    return {'2026-09':[dict(side='personal',amount=30.),dict(side='company',amount=65.)],
            '2026-10':[dict(side='personal',amount=31.25),dict(side='company',amount=69.75)],
            '2026-11':[dict(side='personal',amount=32.)]}


def test_actual_monthly_quotes_overlay_saved_snapshot_and_preserve_record():
    original=record();before=deepcopy(original)
    item=trackers.apply_pension_history([original],history(),TODAY)[0]
    assert original==before
    assert (item['monthly_personal'],item['monthly_company'])==(31.25,69.75)
    summary=trackers.investment_summary(item,TODAY)
    assert (summary['personal'],summary['company'],summary['total'],summary['count'])==(61.25,134.75,196.,2)
    assert summary['gain'] is None
    reread=trackers.decode_rows(trackers.encode_rows([item]))[0]
    assert '_pension_history' not in reread


def test_actual_months_replace_estimates_and_unsynced_month_stays_forecast():
    item=record();item['contribution_start']='2026-09-01'
    item=trackers.apply_pension_history([item],history(),TODAY)[0]
    summary=trackers.investment_summary(item,TODAY)
    # September/October are real, November is forecast at the latest observed rate.
    assert summary['personal']==92.5 and summary['company']==204.5 and summary['total']==297.
    assert summary['count']==3
    item['cometa_auto_quotes']=False
    assert '_pension_history' not in trackers.apply_pension_history([record()|{'cometa_auto_quotes':False}],history(),TODAY)[0]
    manual=trackers.investment_summary(item,TODAY)
    assert manual['total']==303.


def test_future_and_incomplete_months_do_not_replace_latest_known_pair():
    data=history();data['2027-01']=[dict(side='personal',amount=100.),dict(side='company',amount=200.)]
    data['2026-13']=[dict(side='personal',amount=100.),dict(side='company',amount=200.)]
    item=trackers.apply_pension_history([record()],data,TODAY)[0]
    assert item['monthly_source_month']=='2026-10'


def test_confirmed_net_bonus_migration_and_actual_spent_split():
    old=[dict(ID='production-2025',Anno=2025,Tipo='production',Importo=2800.,
              **{'Mese erogazione':7,'Tipo importo':'Da confermare','Stato':'Indicativo','Note':'Importo 2025 da verificare'})]
    loaded=extras.decode_rows(old)[0]
    assert loaded['Tipo importo']=='Netto' and loaded['Stato']=='Confermato'
    assert loaded['Note']=='' and loaded['Quota Leonardo']==2000. and loaded['Residuo speso']
    assert '€800,00' in extras.card_html(loaded) and 'Speso' in extras.card_html(loaded)
    row=extras.default_row(2026,'production')
    assert '€1.500,00' in extras.card_html(row)
    row['Quota Leonardo']=2100.
    assert extras.decode_rows([row])[0]['Quota Leonardo']==2100.  # No repeated migration after editing.
    row['Quota Leonardo']=4000.
    with pytest.raises(ValueError):extras.validate_row(row)


def test_both_leonardo_deposits_survive_upgrading_the_previous_source_version():
    item=next(row for row in trackers.initial_records() if row['name']=='Leonardo · JP Morgan')
    item['production_source_version']=1
    item['annual_contributions'][0].update(personal=None,source='')
    updated=trackers.investment_record(item)
    for row in updated['annual_contributions'][:2]:
        assert row['personal']==2000. and row['confirmed'] and row['source']=='Premio di produzione'
    assert trackers.investment_summary(updated,TODAY)['personal']==4000.
    updated['annual_contributions'][0]['personal']=1900.
    assert trackers.investment_record(updated)['annual_contributions'][0]['personal']==1900.


def test_old_balance_without_cutoff_is_not_counted_twice_when_history_arrives():
    item=record();item['opening_unallocated']=1000.
    item=trackers.apply_pension_history([item],history(),TODAY)[0]
    assert trackers.investment_summary(item,TODAY)['total']==1000.
    item['contribution_start']='2026-10-01'
    # Opening balance is before October: only October real + November forecast are added.
    assert trackers.investment_summary(item,TODAY)['total']==1202.
