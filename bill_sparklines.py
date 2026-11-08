"""Small, read-only monthly bill charts for the existing total cards."""
from base64 import b64encode
from html import escape
import math
import re

import pandas as pd


def monthly_series(frame, category, through):
    if frame.empty or "Mese" not in frame or category not in frame:
        return []
    dates = pd.to_datetime(frame["Mese"], errors="coerce").dt.to_period("M")
    values = pd.to_numeric(frame[category], errors="coerce")
    valid = dates.notna() & values.notna() & values.map(lambda value: math.isfinite(value) if pd.notna(value) else False)
    rows = pd.DataFrame({"month": dates[valid], "value": values[valid]})
    rows = rows[rows["month"] <= pd.Period(through, freq="M")]
    return [(month, float(value)) for month, value in rows.groupby("month", sort=True)["value"].sum().items()]


def chart_html(frame, category, color, through):
    series = monthly_series(frame, category, through)
    if not series:
        return '<div style="height:46px;margin-top:7px;font-size:9px;color:#94a3b8;">Nessun dato</div>'
    color = color if re.fullmatch(r"#[0-9a-fA-F]{6}", color) else "#84B6F4"
    values = [value for _, value in series]
    low, high = min(0., min(values)), max(0., max(values))
    spread = high-low or 1.
    first, last = series[0][0].ordinal, series[-1][0].ordinal
    points = [(4+232*(month.ordinal-first)/(last-first) if last != first else 120.,
               33-29*(value-low)/spread) for month, value in series]
    coordinates = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    baseline = 33-29*(0.-low)/spread
    area = (f'<polygon points="{points[0][0]:.2f},{baseline:.2f} {coordinates} {points[-1][0]:.2f},{baseline:.2f}" '
            f'fill="{color}" fill-opacity=".10"/>') if len(points)>1 else ''
    labels = (f'<text x="4" y="48" text-anchor="start">{series[0][0].strftime("%m/%y")}</text>'
              f'<text x="236" y="48" text-anchor="end">{series[-1][0].strftime("%m/%y")}</text>') if len(points)>1 else (
              f'<text x="120" y="48" text-anchor="middle">{series[0][0].strftime("%m/%y")}</text>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 52">'
           f'<path d="M4 {baseline:.2f}H236" stroke="{color}" stroke-opacity=".18" fill="none"/>'
           +area+f'<polyline points="{coordinates}" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
           +f'<circle cx="{points[-1][0]:.2f}" cy="{points[-1][1]:.2f}" r="2.7" fill="{color}"/>'
           +'<g fill="#94a3b8" font-family="sans-serif" font-size="9">'+labels+'</g></svg>')
    description = category+" · andamento mensile: "+"; ".join(f'{month.strftime("%m/%Y")} {value:.2f} €' for month,value in series)
    uri = "data:image/svg+xml;base64,"+b64encode(svg.encode()).decode()
    return f'<img class="bill-mini-chart" alt="{escape(description,quote=True)}" src="{uri}" style="display:block;width:100%;height:46px;margin-top:7px;"/>'
