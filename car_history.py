"""Paid car expenses and compact annual charts; no budget or banking writes."""
from copy import deepcopy
from datetime import date, datetime
from calendar import monthrange
from html import escape
import math
from base64 import b64encode
from uuid import uuid4

import pandas as pd
import streamlit as st

FORECAST_COLOR = "#fb923c"


def day(value):
    if value is None or value == '':return None
    if isinstance(value, datetime):return value.date()
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def car_record(record, today=None):
    item = deepcopy(record)
    if 'payments' not in item:
        item['payments'] = []
        paid_on = day(item.get('last_date'))
        if paid_on and paid_on <= (today or date.today()) and item.get('amount') is not None:
            item['payments'].append(dict(id='legacy-'+str(item.get('id','')), date=paid_on.isoformat(), amount=item['amount'], note=''))
    return item


def payment_amount(value):
    if isinstance(value,str):
        value = value.strip().replace('€','').replace(' ','').replace('\u00a0','')
        if ',' in value:
            value = value.replace('.','').replace(',','.')
    try:
        number = float(value)
    except (ValueError,TypeError):
        raise ValueError('Inserisci un importo valido, per esempio 193,96.') from None
    if not math.isfinite(number) or number < 0:
        raise ValueError('Gli importi devono essere positivi o zero.')
    return round(number,2)


def validate_payments(rows, today):
    if len(rows)>500:raise ValueError('Massimo 500 pagamenti per voce.')
    result, seen = [], set()
    for row in rows:
        paid_on, amount = day(row.get('date')), row.get('amount')
        note = str(row.get('note') or '').strip()[:300]
        if paid_on is None and (amount is None or amount == '') and not note:
            continue
        if paid_on is None or amount is None or amount == '':
            raise ValueError('Ogni pagamento deve avere data e importo.')
        if not date(1980,1,1) <= paid_on <= today:
            raise ValueError('Lo storico contiene solo pagamenti già effettuati, dal 1980 a oggi.')
        amount = payment_amount(amount)
        identifier = str(row.get('id') or uuid4().hex)
        if identifier in seen:raise ValueError('Pagamento duplicato: controlla le righe dello storico.')
        seen.add(identifier)
        result.append(dict(id=identifier,date=paid_on.isoformat(),amount=round(amount,2),note=note))
    return sorted(result,key=lambda row:row['date'])


def with_payments(record, rows, today, settle=False):
    item = car_record(record,today)
    item['payments'] = validate_payments(rows,today)
    if settle and not item['payments']:
        raise ValueError('Inserisci il pagamento effettuato prima di saldare la scadenza.')
    if item['payments']:
        latest = item['payments'][-1]
        item.update(last_date=latest['date'],amount=latest['amount'])
        if settle or (item.get('next_date') and day(item['next_date']) <= day(latest['date'])):
            item['next_date'] = None  # The paid deadline is replaced by the existing recurrence.
            item['planned_amount'] = None
    else:
        item.update(last_date=None,amount=None)
    return item


def next_due(record):
    if record.get('next_date'):return day(record['next_date'])
    last = day(record.get('last_date'))
    months = int(record.get('interval_months') or 0)
    if last and months:
        year,month0 = divmod(last.year*12+last.month-1+months,12)
        return date(year,month0+1,min(last.day,monthrange(year,month0+1)[1]))
    return day(record.get('first_due')) if not last else None


def forecast(record):
    due = next_due(record)
    amount = record.get('planned_amount')
    automatic = amount is None
    if automatic and due:
        payments = car_record(record).get('payments',[])
        latest = max(payments,key=lambda row:row['date']) if payments else None
        amount = latest['amount'] if latest else record.get('amount')
    return dict(date=due,amount=amount if due else None,automatic=automatic)


def annual_totals(record):
    totals = {}
    for row in car_record(record).get('payments',[]):
        year = day(row['date']).year
        total,count = totals.get(year,(0.,0))
        totals[year] = (round(total+float(row['amount']),2),count+1)
    return [dict(year=year,amount=amount,count=count) for year,(amount,count) in sorted(totals.items())]


def euro(amount):
    return f'€{amount:,.2f}'.replace(',','X').replace('.',',').replace('X','.')


def card_color(record):
    return {'auto-0':'#d9e99a','auto-1':'#60a5fa','auto-2':'#34d399','auto-3':'#c4b5fd','auto-4':'#f9a8d4'}.get(record.get('id'),'#60a5fa')


def uses_semesters(record):
    return record.get('id') == 'auto-3' or 'gomme' in record.get('name','').casefold()


def period_index(row):
    return row['year']*2+row['semester']-1 if 'semester' in row else row['year']


def period_label(row, compact=False):
    if 'semester' not in row:return str(row['year'])
    return f'{row["semester"]}S {row["year"]}' if compact else f'{row["semester"]}° semestre {row["year"]}'


def chart_rows(record, today):
    semiannual = uses_semesters(record)
    if semiannual:
        totals = {}
        for payment in car_record(record).get('payments',[]):
            paid_on = day(payment['date'])
            key = (paid_on.year,1 if paid_on.month<=6 else 2)
            row = totals.setdefault(key,dict(year=key[0],semester=key[1],amount=0.,count=0))
            row.update(amount=round(row['amount']+float(payment['amount']),2),count=row['count']+1)
        rows = list(totals.values())
    else:
        rows = annual_totals(record)
    estimate = forecast(record)
    due,amount = estimate['date'],estimate['amount']
    if due and amount is not None:
        semester = 1 if due.month<=6 else 2
        existing = next((row for row in rows if row['year']==due.year and (not semiannual or row['semester']==semester)),None)
        if existing is None:
            existing = dict(year=due.year,amount=0.,count=0)
            if semiannual:existing['semester']=semester
            rows.append(existing)
        existing.update(amount=round(existing['amount']+float(amount),2),pending=float(amount),future=due>today)
    return sorted(rows,key=period_index)


def chart_html(record, today=None):
    rows = chart_rows(record, today or date.today())
    if not rows:
        return '<div class="car-history-empty">Storico pagamenti<br><span>Aggiungi data e importo per vedere l’andamento.</span></div>'
    color = card_color(record)
    def point_color(row):return FORECAST_COLOR if 'pending' in row else color
    first,last = period_index(rows[0]),period_index(rows[-1])
    peak = max(row['amount'] for row in rows) or 1.
    coordinates = [(120. if first==last else 12.+216.*(period_index(row)-first)/(last-first), 51.-30.*row['amount']/peak) for row in rows]
    # Missing years are gaps, not zero expenses or invented intermediate values.
    description = ('Storico semestrale ' if uses_semesters(record) else 'Storico annuale ')+record['name']+': '+'; '.join(f'{period_label(row)} {euro(row["amount"])}'+(' (previsione inclusa)' if 'pending' in row else '') for row in rows)
    points = ''.join(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{point_color(row)}" tabindex="0" aria-label="{period_label(row)}: {euro(row["amount"])}"><title>{period_label(row)}: {euro(row["amount"])} · {row["count"]} pagamenti{' · previsione inclusa' if 'pending' in row else ''}</title></circle>' for row,(x,y) in zip(rows,coordinates))
    ticks = sorted({0,len(rows)//2,len(rows)-1})
    labels = ''.join(f'<text x="{coordinates[index][0]:.2f}" y="70" text-anchor="{"start" if index==0 else "end" if index==len(rows)-1 else "middle"}" fill="#94a3b8" font-size="10">{period_label(rows[index],compact=True)}</text>' for index in ticks)
    value_indices = range(len(rows)) if len(rows) <= 4 else [0,len(rows)-1]
    values = ''.join(f'<text x="{coordinates[index][0]:.2f}" y="{coordinates[index][1]-6:.2f}" text-anchor="{"start" if index==0 and len(rows)>1 else "end" if index==len(rows)-1 and len(rows)>1 else "middle"}" fill="{point_color(rows[index])}" font-size="10">{euro(rows[index]["amount"]).removesuffix(",00")}</text>' for index in value_indices)
    latest = rows[-1]
    segments = []
    for index in range(1,len(rows)):
        if period_index(rows[index]) != period_index(rows[index-1])+1:continue
        x0,y0 = coordinates[index-1]
        x1,y1 = coordinates[index]
        dashed = ' stroke-dasharray="5 4"' if 'pending' in rows[index] or 'pending' in rows[index-1] else ''
        segments.append(f'<path d="M{x0:.2f},{y0:.2f} L{x1:.2f},{y1:.2f}" stroke="{color}" stroke-width="2.5" fill="none"{dashed}/>')
    pending = next((row for row in rows if 'pending' in row),None)
    forecast = '' if pending is None else f'<div class="tracker-note" style="color:{point_color(pending)}">{"Previsto" if pending["future"] else "Da confermare"}: {euro(pending["pending"])}</div>'
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 76">'
           f'<title>{escape(description)}</title><path d="M12,51H228 M12,21H228" stroke="#94a3b8" stroke-opacity=".18" fill="none"/>'
           f'{"".join(segments)}{points}{labels}{values}</svg>')
    # st.html sanitizes inline SVG; an embedded image keeps the chart intact.
    encoded = b64encode(svg.encode()).decode()
    return (f'<div class="car-mini-history"><div class="car-history-heading">Costi per {"semestre" if uses_semesters(record) else "anno"}</div>'
            f'<img src="data:image/svg+xml;base64,{encoded}" alt="{escape(description,quote=True)}" style="display:block;width:100%;height:auto"/>'
            f'<div class="car-history-latest">{period_label(latest,compact=True)}<strong style="color:{point_color(latest)}">{euro(latest["amount"])}</strong></div>{forecast}</div>')



def payment_rows(edited):
    rows = edited.astype(object).where(pd.notna(edited),None).to_dict(orient='records')
    return [dict(id=row['ID'],date=row['Data'],amount=row['Importo'],note=row['Nota']) for row in rows]


def payment_fields(record, today):
    """Payment table inside the selected car record's existing save form."""
    item = car_record(record,today)
    identifier = item.get('id') or '__new__'
    st.markdown('**Storico pagamenti'+(' · '+item['name'] if item.get('name') else '')+'**')
    frame = pd.DataFrame([dict(ID=row['id'],Data=day(row['date']),Importo=euro(float(row['amount'])).removeprefix('€'),Nota=row.get('note','')) for row in item['payments']],columns=['ID','Data','Importo','Nota'])
    # Text amounts allow Italian decimal commas on every browser/keyboard.
    frame['Data'] = pd.to_datetime(frame['Data'])
    frame['Importo'] = frame['Importo'].astype('string')
    edited = st.data_editor(frame,num_rows='dynamic',hide_index=True,width='stretch',key='car_payment_editor_'+identifier,
        column_config={'ID':None,'Data':st.column_config.DateColumn('Data',format='DD/MM/YYYY',required=True,min_value=date(1980,1,1),max_value=today),
                       'Importo':st.column_config.TextColumn('Pagato (€)',help='Importo con centesimi, per esempio 193,96.',required=True),
                       'Nota':st.column_config.TextColumn('Nota',max_chars=300)})
    settle = False
    estimate = forecast(item)
    if estimate['date'] and estimate['amount'] is not None:
        settle = st.checkbox('Il pagamento salda la scadenza del '+estimate['date'].strftime('%d/%m/%Y'),value=False,help='Spunta anche se hai pagato in anticipo: il costo previsto viene sostituito dal pagamento reale.')
    return edited,settle
