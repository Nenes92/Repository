from copy import deepcopy
from datetime import date
import car_history as history
import car_overview as overview
import personal_trackers as trackers

TODAY=date(2026,11,5)


def model(inspection_amount=None):
    items=trackers.initial_records()[:5]
    histories={
        'auto-0':[('2024-11-08',194.87),('2025-11-08',194.33)],
        'auto-1':[('2024-11-05',245.90),('2025-11-05',419)],
        'auto-2':[('2025-11-08',215)],
        'auto-3':[('2025-04-20',50),('2025-11-20',50),('2026-04-20',50)],
    }
    for index,item in enumerate(items):
        rows=[dict(id=str(i),date=day,amount=amount,note='') for i,(day,amount) in enumerate(histories.get(item['id'],[]))]
        items[index]=history.with_payments(item,rows,TODAY)
    items[0].update(next_date='2026-11-05',planned_amount=193.96)
    items[1]['next_date']='2026-11-10'
    items[2]['next_date']='2026-11-15'
    items[3].update(next_date='2026-11-20',planned_amount=30)
    items[4]['planned_amount']=inspection_amount
    return items


def test_actual_annual_totals_exclude_forecasts_and_other_tracker_sections():
    items=model()+[trackers.initial_records()[5]]
    before=deepcopy(items)
    result=overview.summary(items,TODAY)
    assert result['years']==[dict(year=2024,amount=440.77),dict(year=2025,amount=928.33),dict(year=2026,amount=50.)]
    assert items==before


def test_monthly_fund_accounts_for_two_different_tyres_prices_and_biennial_inspection():
    result=overview.summary(model(80),TODAY)
    # 193.96 tax + 419 insurance + 215 servicing + (50+30) tyres + 80/2 inspection.
    assert result['annual_estimate']==947.96
    assert result['monthly']==79.
    assert result['monthly']*12>=result['annual_estimate']
    assert result['missing']==[]
    assert result['due_now']==857.96


def test_unknown_inspection_is_not_silently_treated_as_free():
    result=overview.summary(model(),TODAY)
    assert result['annual_estimate']==907.96
    assert result['monthly']==75.67
    assert result['missing']==['Revisione']
    rendered=overview.html(model(),TODAY)
    assert 'Almeno €75,67/mese' in rendered
    assert 'Da stimare: Revisione.' in rendered
    assert 'Da coprire a novembre' in rendered


def test_no_data_or_zero_costs_do_not_generate_invented_history():
    result=overview.summary(trackers.initial_records(),TODAY)
    assert result['years']==[] and result['monthly'] is None
    assert len(result['missing'])==5
    items=model(0)
    for item in items:item['planned_amount']=0
    result=overview.summary(items,TODAY)
    assert result['monthly']==4.17  # One paid spring tyre change, next autumn is free.
    assert result['missing']==[]


def test_overview_occupies_free_slot_without_creating_a_record_or_a_card():
    items=model();before=deepcopy(items)
    rendered=trackers.cards_html(items,'auto',TODAY)
    assert rendered.count('class="tracker-card"')==5
    assert rendered.count('class="car-overview"')==1
    assert rendered.index('class="car-overview"')>rendered.index('<strong>Revisione</strong>')
    assert items==before
    assert 'Pagato per anno' in rendered
    assert 'class="car-overview"' not in trackers.cards_html(trackers.initial_records(),'investments',TODAY)
