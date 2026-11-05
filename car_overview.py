"""Read-only Auto history and a monthly sinking-fund estimate."""
from base64 import b64encode
from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_CEILING
from html import escape
import math

import car_history

MONTHS = ('gennaio','febbraio','marzo','aprile','maggio','giugno','luglio','agosto','settembre','ottobre','novembre','dicembre')


def summary(records, today):
    annual = {}
    monthly_parts = []
    missing = []
    due_now = []
    month_end = date(today.year,today.month,monthrange(today.year,today.month)[1])
    for record in records:
        if record.get('kind') != 'auto':continue
        item = car_history.car_record(record,today)
        for row in car_history.annual_totals(item):
            annual[row['year']] = annual.get(row['year'],0.)+row['amount']
        estimate = car_history.forecast(item)
        price = estimate['amount']
        months = int(item.get('interval_months') or 0)
        if price is None:
            missing.append(item['name'])
            continue
        if estimate['date'] and estimate['date']<=month_end:due_now.append(float(price))
        if months == 6:
            # Spring and autumn can have different prices (e.g. tyres: 50 + 30).
            seasonal = {}
            for payment in sorted(item['payments'],key=lambda row:row['date']):
                semester = 1 if car_history.day(payment['date']).month<=6 else 2
                seasonal[semester] = float(payment['amount'])
            if estimate['date']:
                semester = 1 if estimate['date'].month<=6 else 2
                seasonal[semester] = float(price)
            annual_cost = sum(seasonal.get(semester,float(price)) for semester in (1,2))
            monthly_parts.append(annual_cost/12.)
        elif months>0:
            monthly_parts.append(float(price)/months)
        # Non-recurring costs are shown when due, not treated as yearly subscriptions.
    monthly_raw = math.fsum(monthly_parts)
    monthly = float(Decimal(str(monthly_raw)).quantize(Decimal('0.01'),rounding=ROUND_CEILING)) if monthly_parts else None
    return dict(years=[dict(year=year,amount=round(amount,2)) for year,amount in sorted(annual.items())],
                monthly=monthly,annual_estimate=round(monthly_raw*12.,2) if monthly_parts else None,
                missing=missing,due_now=round(math.fsum(due_now),2),month=MONTHS[today.month-1])


def annual_chart(rows):
    if not rows:return '<div class="car-overview-note">Ancora nessun pagamento registrato.</div>'
    width = max(220,len(rows)*52)
    peak = max(row['amount'] for row in rows) or 1.
    step = (width-16)/len(rows)
    bars=[]
    for index,row in enumerate(rows):
        x=8+(index+.5)*step
        height=32*row['amount']/peak
        amount=car_history.euro(row['amount']).removesuffix(',00')
        bars.append(f'<rect x="{x-9:.1f}" y="{48-height:.1f}" width="18" height="{max(height,1):.1f}" rx="3" fill="#60a5fa" fill-opacity=".65"/>'
                    f'<text x="{x:.1f}" y="{43-height:.1f}" text-anchor="middle" fill="#cbd5e1" font-size="10">{amount}</text>'
                    f'<text x="{x:.1f}" y="64" text-anchor="middle" fill="#94a3b8" font-size="10">{row["year"]}</text>')
    description = 'Pagamenti Auto per anno: '+'; '.join(f'{row["year"]}: {car_history.euro(row["amount"])}' for row in rows)
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 70"><title>{escape(description)}</title>{"".join(bars)}</svg>'
    encoded=b64encode(svg.encode()).decode()
    minimum=f'min-width:{len(rows)*35}px;' if len(rows)>4 else ''
    return f'<div style="overflow-x:auto"><img alt="{escape(description,quote=True)}" src="data:image/svg+xml;base64,{encoded}" style="display:block;width:100%;height:auto;{minimum}"/></div>'


def html(records, today, full_width=False):
    data=summary(records,today)
    monthly = car_history.euro(data['monthly']) if data['monthly'] is not None else 'Da stimare'
    qualifier = 'Almeno ' if data['missing'] and data['monthly'] is not None else ''
    annual = '' if data['annual_estimate'] is None else f'<div class="car-overview-note">{car_history.euro(data["annual_estimate"])}/anno · quota su 12 mesi</div>'
    missing = '' if not data['missing'] else '<div class="car-overview-note">Da stimare: '+escape(', '.join(data['missing']))+'.</div>'
    due = '' if not data['due_now'] else f'<div class="car-overview-due">Da coprire a {data["month"]}: <strong>{car_history.euro(data["due_now"])}</strong></div>'
    span = ' style="grid-column:1/-1"' if full_width else ''
    return (f'<aside class="car-overview" aria-label="Riepilogo spese auto"{span}><strong>Riepilogo Auto</strong>'
            f'<div class="car-overview-note">Pagato per anno</div>{annual_chart(data["years"])}'
            f'<div class="car-overview-note">Da mettere da parte</div><div class="car-overview-monthly">{qualifier}{monthly}'+('/mese' if data['monthly'] is not None else '')+'</div>'
            f'{annual}{missing}{due}<div class="car-overview-footnote">Quota regolare da accumulare nel tempo; non considera quanto hai già da parte.</div></aside>')
