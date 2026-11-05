"""Paid car expenses and compact annual charts; no budget or banking writes."""
from copy import deepcopy
from datetime import date, datetime
from html import escape
import math
from base64 import b64encode
from uuid import uuid4

import pandas as pd
import streamlit as st


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
        amount = float(amount)
        if not math.isfinite(amount) or amount < 0:
            raise ValueError('Gli importi devono essere positivi o zero.')
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
    return {'auto-0':'#fbbf24','auto-1':'#60a5fa','auto-2':'#34d399','auto-3':'#c4b5fd'}.get(record.get('id'),'#60a5fa')


def chart_rows(record, today):
    rows = annual_totals(record)
    due = day(record.get('next_date'))
    amount = record.get('planned_amount')
    if due and amount is not None:
        existing = next((row for row in rows if row['year']==due.year),None)
        if existing is None:
            existing = dict(year=due.year,amount=0.,count=0)
            rows.append(existing)
        existing.update(amount=round(existing['amount']+float(amount),2),pending=float(amount),future=due>today)
    return sorted(rows,key=lambda row:row['year'])


def chart_html(record, today=None):
    rows = chart_rows(record, today or date.today())
    if not rows:
        return '<div class="car-history-empty">Storico pagamenti<br><span>Aggiungi data e importo per vedere l’andamento.</span></div>'
    color = card_color(record)
    def point_color(row):return '#fb923c' if row.get('future') else color
    first,last = rows[0]['year'],rows[-1]['year']
    peak = max(row['amount'] for row in rows) or 1.
    coordinates = [(120. if first==last else 12.+216.*(row['year']-first)/(last-first), 79.-57.*row['amount']/peak) for row in rows]
    # Missing years are gaps, not zero expenses or invented intermediate values.
    description = 'Storico annuale '+record['name']+': '+'; '.join(f'{row["year"]} {euro(row["amount"])}'+(' (previsione inclusa)' if 'pending' in row else '') for row in rows)
    points = ''.join(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="{point_color(row)}" tabindex="0" aria-label="{row["year"]}: {euro(row["amount"])}"><title>{row["year"]}: {euro(row["amount"])} · {row["count"]} pagamenti{' · previsione inclusa' if 'pending' in row else ''}</title></circle>' for row,(x,y) in zip(rows,coordinates))
    ticks = sorted({0,len(rows)//2,len(rows)-1})
    labels = ''.join(f'<text x="{coordinates[index][0]:.2f}" y="101" text-anchor="middle" fill="#94a3b8" font-size="12">{rows[index]["year"]}</text>' for index in ticks)
    value_indices = range(len(rows)) if len(rows) <= 4 else [0,len(rows)-1]
    values = ''.join(f'<text x="{coordinates[index][0]:.2f}" y="{coordinates[index][1]-10:.2f}" text-anchor="{"start" if index==0 and len(rows)>1 else "end" if index==len(rows)-1 and len(rows)>1 else "middle"}" fill="{point_color(rows[index])}" font-size="13">{euro(rows[index]["amount"]).removesuffix(",00")}</text>' for index in value_indices)
    latest = rows[-1]
    segments = []
    for index in range(1,len(rows)):
        if rows[index]['year'] != rows[index-1]['year']+1:continue
        x0,y0 = coordinates[index-1]
        x1,y1 = coordinates[index]
        dashed = ' stroke-dasharray="5 4"' if 'pending' in rows[index] or 'pending' in rows[index-1] else ''
        segments.append(f'<path d="M{x0:.2f},{y0:.2f} L{x1:.2f},{y1:.2f}" stroke="{point_color(rows[index])}" stroke-width="2.5" fill="none"{dashed}/>')
    pending = next((row for row in rows if 'pending' in row),None)
    forecast = '' if pending is None else f'<div class="tracker-note" style="color:{point_color(pending)}">{"Previsto" if pending["future"] else "Da confermare"}: {euro(pending["pending"])}</div>'
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 110">'
           f'<title>{escape(description)}</title><path d="M12,79H228 M12,22H228" stroke="#94a3b8" stroke-opacity=".18" fill="none"/>'
           f'{"".join(segments)}{points}{labels}{values}</svg>')
    # st.html sanitizes inline SVG; an embedded image keeps the chart intact.
    encoded = b64encode(svg.encode()).decode()
    return (f'<div class="car-mini-history"><div class="car-history-heading">Costi per anno</div>'
            f'<img src="data:image/svg+xml;base64,{encoded}" alt="{escape(description,quote=True)}" style="display:block;width:100%;height:auto"/>'
            f'<div class="car-history-latest">{latest["year"]}<strong style="color:{point_color(latest)}">{euro(latest["amount"])}</strong></div>{forecast}</div>')



def render_editor(items, save_record, saved, today):
    if not items:return
    with st.expander('Storico pagamenti · Auto',expanded=False):
        lookup = {item['id']:car_record(item,today) for item in items}
        key = 'car_payment_history_voice'
        if st.session_state.get(key) not in lookup:st.session_state.pop(key,None)
        identifier = st.selectbox('Voce dello storico',list(lookup),format_func=lambda value:lookup[value]['name'],key=key)
        item = lookup[identifier]
        st.caption('Ogni riga è un pagamento. Puoi aggiungere, correggere o eliminare righe; il grafico somma gli importi per anno. Salva per applicare le modifiche.')
        with st.form('car_payment_history_'+identifier,border=False,enter_to_submit=False):
            frame = pd.DataFrame([dict(ID=row['id'],Data=day(row['date']),Importo=float(row['amount']),Nota=row.get('note','')) for row in item['payments']],columns=['ID','Data','Importo','Nota'])
            # Explicit dtypes keep an initially empty editor editable as dates/numbers.
            frame['Data'] = pd.to_datetime(frame['Data'])
            frame['Importo'] = frame['Importo'].astype(float)
            edited = st.data_editor(frame,num_rows='dynamic',hide_index=True,width='stretch',key='car_payment_editor_'+identifier,
                column_config={'ID':None,'Data':st.column_config.DateColumn('Data',format='DD/MM/YYYY',required=True,min_value=date(1980,1,1),max_value=today),
                               'Importo':st.column_config.NumberColumn('Pagato (€)',min_value=0.,step=1.,format='%.2f',required=True),
                               'Nota':st.column_config.TextColumn('Nota',max_chars=300)})
            settle = False
            if item.get('next_date') and item.get('planned_amount') is not None:
                settle = st.checkbox('Il pagamento salda la scadenza del '+day(item['next_date']).strftime('%d/%m/%Y'),value=False,help='Spunta anche se hai pagato in anticipo: il costo previsto viene sostituito dal pagamento reale.')
            submit = st.form_submit_button('💾 Salva storico pagamenti',type='primary',use_container_width=True)
        if submit:
            try:
                rows = edited.astype(object).where(pd.notna(edited),None).to_dict(orient='records')
                rows = [dict(id=row['ID'],date=row['Data'],amount=row['Importo'],note=row['Nota']) for row in rows]
                candidate = with_payments(item,rows,today,settle=settle)
                if save_record(identifier,candidate):saved('Storico pagamenti salvato.')
            except (ValueError,TypeError) as exc:
                st.error(str(exc))
