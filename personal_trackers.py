"""Personal records for deadlines, holdings and monthly instalments.

No banking connection, price lookup or changes to the existing expense budget.
"""
from __future__ import annotations
from calendar import monthrange
from copy import deepcopy
from datetime import date, datetime, timedelta
from html import escape
import json
import car_history
import car_overview
import math
from uuid import uuid4

import streamlit as st

SHEET = "Scadenze e investimenti"
HEADERS = ["ID", "Sezione", "Nome", "Dati"]
KINDS = ("auto", "investments", "installments")


def as_date(value):
    if not value:
        return None
    return value if isinstance(value, date) and not isinstance(value, datetime) else date.fromisoformat(str(value)[:10])


def month_date(anchor, offset, day=None):
    month = anchor.year * 12 + anchor.month - 1 + offset
    year, zero_month = divmod(month, 12)
    return date(year, zero_month + 1, min(day or anchor.day, monthrange(year, zero_month + 1)[1]))


def initial_records():
    records = []
    for name, interval in [("Bollo", 12), ("Assicurazione auto", 12), ("Tagliando", 12), ("Cambio gomme", 6)]:
        records.append(dict(id="auto-" + str(len(records)), kind="auto", name=name,
                            last_date=None, next_date=None, interval_months=interval, reminder_days=30, amount=None, notes="", payments=[]))
    records.append(inspection_record())
    for index, name in enumerate(["Moneyfarm", "Alleanza Assicurazioni", "Cometa", "Leonardo · JP Morgan"]):
        records.append(investment_record(dict(id=f"investment-{index}", kind="investments", name=name,
                            invested=None, current_value=None, withdrawn=0., notes="")))
    for index, name in enumerate(["Mutuo casa", "Assicurazione vita mutuo", "Rata auto", "Finanziamento cucina"]):
        records.append(dict(id=f"instalment-{index}", kind="installments", name=name,
                            start_date=None, end_date=None, count=None, due_day=None, amount=None, paid_offset=0, notes=""))
    return records


def inspection_record():
    return dict(id='auto-4',kind='auto',name='Revisione',last_date=None,next_date=None,
                first_due='2027-11-30',interval_months=24,reminder_days=30,amount=None,notes='',payments=[])


def decode_settings(rows):
    for row in rows:
        if row.get('ID') == '__settings__':
            settings = json.loads(row['Dati'])
            if not isinstance(settings,dict):raise ValueError('Impostazioni del registro non leggibili.')
            return settings
    return dict(version=1)


def decode_rows(rows):
    if not rows:
        return initial_records()
    result, seen = [], set()
    for row in rows:
        if not any(str(value).strip() for value in row.values()):
            continue
        if row.get("ID") == "__settings__":
            continue
        try:
            record = json.loads(row["Dati"])
            identifier, kind, name = str(row["ID"]), str(row["Sezione"]), str(row["Nome"])
            if not isinstance(record, dict) or not identifier or identifier in seen or kind not in KINDS or not name.strip():
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise ValueError("Una voce del registro non è leggibile. Il registro non verrà sovrascritto.") from None
        seen.add(identifier)
        item = dict(record, id=identifier, kind=kind, name=name)
        result.append(investment_record(item) if kind == "investments" else car_history.car_record(item) if kind == "auto" else item)
    if result and decode_settings(rows).get('version',1) < 2 and not any(item['kind']=='auto' and (item['id']=='auto-4' or 'revisione' in item['name'].casefold()) for item in result):
        result.append(inspection_record())
    return result


def encode_rows(records, settings=None):
    settings = dict(settings or {}, version=2)
    rows = [{"ID": "__settings__", "Sezione": "settings", "Nome": "Registro personale", "Dati": json.dumps(settings,ensure_ascii=False)}]
    for item in records:
        data = {key: value for key, value in item.items() if key not in {"id", "kind", "name"} and not key.startswith("_")}
        rows.append(dict(zip(HEADERS, [item["id"], item["kind"], item["name"],
            json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))])))
    return rows


def merge_record(records, identifier, replacement):
    result, found = [], False
    for item in records:
        if item["id"] == identifier:
            found = True
            if replacement is not None:result.append(deepcopy(replacement))
        else:result.append(deepcopy(item))
    if not found and replacement is not None:result.append(deepcopy(replacement))
    return result


def _number(value):
    if value is None or value == "":
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("Gli importi devono essere numeri positivi o zero.")
    return round(number, 2)


def validate_record(record, today, paid_today=None):
    item = deepcopy(record)
    item["name"] = str(item.get("name", "")).strip()
    if not item["name"] or len(item["name"]) > 80:
        raise ValueError("Inserisci un nome, fino a 80 caratteri.")
    if item.get("kind") not in KINDS:
        raise ValueError("Sezione non valida.")
    item["notes"] = str(item.get("notes", ""))[:1000]
    for field in ("amount", "planned_amount", "invested", "current_value", "withdrawn"):
        if field in item:
            item[field] = _number(item[field])
    for field in ("last_date", "next_date", "first_due", "start_date", "end_date"):
        if field in item:
            parsed = as_date(item[field])
            item[field] = parsed.isoformat() if parsed else None
    if item["kind"] == "auto":
        item["payments"] = car_history.validate_payments(car_history.car_record(item, today)["payments"], today)
        if item.get("planned_amount") is not None and not item.get("next_date"):
            raise ValueError("Inserisci la scadenza del costo previsto.")
        item["interval_months"] = int(item.get("interval_months", 0))
        item["reminder_days"] = int(item.get("reminder_days", 30))
        if not 0 <= item["interval_months"] <= 120 or not 0 <= item["reminder_days"] <= 365:
            raise ValueError("Controlla intervallo e giorni di preavviso.")
        if item.get("last_date") and item.get("next_date") and item["next_date"] < item["last_date"]:
            raise ValueError("La prossima scadenza non può precedere l'ultima data.")
    if item["kind"] == "installments":
        count = item.get("count")
        if count is not None:
            if float(count) != int(count) or not 1 <= int(count) <= 1200:
                raise ValueError("Inserisci un numero di rate da 1 a 1200.")
            item["count"] = int(count)
        due_day = item.get("due_day")
        if due_day is not None and not 1 <= int(due_day) <= 31:
            raise ValueError("Il giorno di addebito deve essere da 1 a 31.")
        if (item.get("count") or item.get("end_date")) and not item.get("start_date"):
            raise ValueError("Inserisci anche la data della prima rata.")
        schedule = instalment_dates(item)
        if paid_today is not None:
            if float(paid_today) != int(paid_today) or not 0 <= int(paid_today) <= len(schedule):
                raise ValueError("Le rate pagate devono essere comprese tra zero e il totale delle rate.")
            item["paid_offset"] = int(paid_today) - sum(day <= today for day in schedule)
        item["paid_offset"] = int(item.get("paid_offset", 0))
    if item["kind"] == "investments":
        item = validate_investment(item, today, paid_today)
    return item


def instalment_dates(record):
    start, end = as_date(record.get("start_date")), as_date(record.get("end_date"))
    count = record.get("count")
    if not start:
        return []
    if end and end < start:
        raise ValueError("La data finale precede la prima rata.")
    day = int(record.get("due_day") or start.day)
    if not 1 <= day <= 31:
        raise ValueError("Giorno di addebito non valido.")
    def due(index):
        return start if index == 0 else month_date(start, index, day)
    if end:
        n = (end.year-start.year)*12 + end.month-start.month + 1
        n -= int(due(n-1) > end)
        if count is not None and int(count) != n:
            raise ValueError("Numero di rate e data finale non coincidono. Compila solo uno dei due, oppure correggili.")
        count = n
    if count is None:
        return []
    if not 1 <= int(count) <= 1200:
        raise ValueError("Durata non valida: massimo 1200 rate mensili.")
    return [due(index) for index in range(int(count))]


def instalment_summary(record, today):
    schedule = instalment_dates(record)
    if not schedule:
        return None
    calendar_paid = sum(day <= today for day in schedule)
    paid = max(0, min(len(schedule), calendar_paid + int(record.get("paid_offset", 0))))
    remaining = len(schedule) - paid
    amount = record.get("amount")
    return dict(total=len(schedule), paid=paid, remaining=remaining, calendar_paid=calendar_paid,
        paid_amount=round(amount*paid, 2) if amount is not None else None,
        remaining_amount=round(amount*remaining, 2) if amount is not None else None,
        total_amount=round(amount*len(schedule), 2) if amount is not None else None,
        next_due=schedule[paid] if remaining else None, end=schedule[-1], progress=paid/len(schedule))


def car_deadline(record):
    return car_history.next_due(record)


def deadline_status(deadline, today, notice=30):
    if deadline is None:
        return "Da impostare", "#94a3b8"
    days = (deadline-today).days
    if days < 0:
        return f"🚨 Scaduta da {-days} {'giorno' if days == -1 else 'giorni'}", "#f87171"
    if days == 0:
        return "⚠️ Scade oggi", "#fbbf24"
    return (f"⚠️ Da pagare · tra {days} giorni" if days <= notice else f"Tra {days} giorni"), "#fbbf24" if days <= notice else "#34d399"


def share_purchase(row):
    """Value purchased and awarded shares at the same acquisition price."""
    result = dict(row)
    for key in ("personal_shares","matching_shares","welcome_shares"):
        value = row.get(key)
        if value is not None:
            if not math.isfinite(float(value)) or float(value) != int(value) or not 0 <= int(value) <= 10000000:
                raise ValueError("Il numero di azioni deve essere intero e non negativo.")
            result[key] = int(value)
    price = _number(row.get("share_price"))
    result["share_price"] = price
    if result.get("personal_shares") is None:
        if any(result.get(key) for key in ("matching_shares","welcome_shares")) or price is not None:
            raise ValueError("Inserisci anche il numero di azioni acquistate da te.")
        return dict(result,personal=None,company=None)
    if price is None or price <= 0:
        raise ValueError("Inserisci il prezzo di acquisto per azione.")
    result["matching_shares"] = result.get("matching_shares") if result.get("matching_shares") is not None else result["personal_shares"]//3
    result["welcome_shares"] = result.get("welcome_shares") or 0
    result["personal"] = round(result["personal_shares"]*price,2)
    result["company"] = round((result["matching_shares"]+result["welcome_shares"])*price,2)
    return result


def investment_record(record):
    """Upgrade the old register without discarding balances or guessing their split."""
    item = deepcopy(record)
    name = item.get("name", "").strip().casefold()
    if name == "allianz / alleanza":
        item["name"] = "Alleanza Assicurazioni"
        name = item["name"].casefold()
    if name == "moneyfarm":
        item["name"] = "Moneyfarm · PAC 7"
        name = item["name"].casefold()
    preset = {"moneyfarm · pac 7": ("monthly", "PAC · rischio 7", 100., 0.),
              "alleanza assicurazioni": ("monthly", "PAC", 100., 0.),
              "cometa": ("monthly", "Pensione integrativa", 30.52, 68.66),
              "leonardo · jp morgan": ("annual", "Azioni Leonardo", None, None)}.get(name)
    mode, product, personal, company = preset or ("manual", "Investimento", None, None)
    for key, value in dict(contribution_mode=mode, product=product, monthly_personal=personal,
                           monthly_company=company, contribution_start=None, contribution_end=None,
                           contribution_offset=0, opening_personal=0., opening_company=0.,
                           opening_unallocated=item.get("invested") or 0., redemption_year=2028 if mode == "annual" else None,
                           redemption_date=None, annual_contributions=[dict(year=year, date=None, personal=None, company=None)
                               for year in (2025, 2026, 2027)] if mode == "annual" else []).items():
        item.setdefault(key, value)
    if name == "moneyfarm · pac 7" and item.get("product") == "PAC":
        item["product"] = "PAC · rischio 7"
    if name in {"moneyfarm · pac 7", "alleanza assicurazioni"}:
        item["personal_only"] = True
    if item.get("personal_only"):
        item["monthly_company"] = 0.
        item["opening_personal"] = (item.get("opening_personal") or 0.)+(item.get("opening_company") or 0.)+(item.get("opening_unallocated") or 0.)
        item["opening_company"] = 0.
        item["opening_unallocated"] = 0.
    if name == "cometa":
        item.setdefault("cometa_auto_quotes", True)
        item.setdefault("monthly_tfr", 220.02 if item["cometa_auto_quotes"] else None)
        item.setdefault("opening_tfr", 0.)
        if item["monthly_tfr"] == 220.02:
            item.setdefault("monthly_tfr_source_month", "2026-10")
        if not item.get("cometa_rate_initialized"):
            if (item.get("monthly_personal"), item.get("monthly_company")) in {(30., 70.), (30.52, 68.66)}:
                item.update(monthly_personal=30.52, monthly_company=68.66, monthly_source_month="2026-10")
            item["cometa_rate_initialized"] = True
    if name == "leonardo · jp morgan" and item.get("production_source_version", 0) < 2:
        for row in item["annual_contributions"]:
            if row["year"] in {2025, 2026}:
                if row.get("personal") is None:
                    row["personal"] = 2000.
                    row["confirmed"] = True
                elif row["personal"] == 2000.:
                    row.setdefault("confirmed", True)
                if not row.get("source"):
                    row["source"] = "Premio di produzione"
        item["production_source_initialized"] = True
        item["production_source_version"] = 2
    if item.get("share_plan"):
        item["annual_contributions"] = [share_purchase(row) for row in item["annual_contributions"]]
    return item


def apply_pension_history(records, history, today=None):
    """Overlay the saved payslip snapshot; no Drive access or persisted duplicate."""
    result = deepcopy(records)
    today = today or date.today()
    complete = {}
    for month, rows in (history or {}).items():
        if not isinstance(month, str) or len(month) != 7:
            continue
        try:
            period = as_date(month+"-01")
        except ValueError:
            continue
        if period > today:
            continue
        values = {}
        for row in rows:
            side = row.get("side")
            if side in {"personal", "company", "tfr"}:
                values[side] = values.get(side, 0.)+(_number(row.get("amount")) or 0.)
        if {"personal", "company"}.issubset(values):
            complete[month] = values
    for item in result:
        if item.get("kind") == "investments" and item.get("name", "").casefold() == "cometa" and item.get("cometa_auto_quotes", True) and complete:
            latest = max(complete)
            item.update(monthly_personal=complete[latest]["personal"], monthly_company=complete[latest]["company"],
                        monthly_source_month=latest, _pension_history=complete, contribution_offset=0)
            tfr_months = [month for month in complete if "tfr" in complete[month]]
            if tfr_months:
                month = max(tfr_months)
                item.update(monthly_tfr=complete[month]["tfr"], monthly_tfr_source_month=month)
    return result


def pension_monthly_average(record, today):
    """Average the latest twelve complete imported monthly contribution totals."""
    totals = []
    history = record.get("_pension_history", {}) if record.get("cometa_auto_quotes",True) else {}
    for month, quotes in sorted(history.items(),reverse=True):
        try:
            period = date.fromisoformat(month+"-01")
        except (TypeError,ValueError):
            continue
        if period > today or not all(quotes.get(side) is not None for side in ("personal","company","tfr")):
            continue
        totals.append(math.fsum(quotes[side] for side in ("personal","company","tfr")))
        if len(totals) == 12:
            break
    return dict(amount=round(math.fsum(totals)/len(totals),2) if totals else None,count=len(totals))


def monthly_contributions(record, today):
    start, end = as_date(record.get("contribution_start")), as_date(record.get("contribution_end"))
    if not start:
        return dict(calendar_count=0, count=0, next_due=None)
    if end and end < start:
        raise ValueError("La fine dei versamenti precede il primo versamento.")
    stop = min(today, end) if end else today
    count = max(0, (stop.year-start.year)*12 + stop.month-start.month + 1)
    if count and month_date(start, count-1) > stop:
        count -= 1
    next_due = month_date(start, count)
    if end and next_due > end:
        next_due = None
    return dict(calendar_count=count, count=max(0, count+int(record.get("contribution_offset", 0))), next_due=next_due)


def investment_summary(record, today):
    item = investment_record(record)
    mode = item["contribution_mode"]
    personal, company = item["opening_personal"] or 0., item["opening_company"] or 0.
    tfr = item.get("opening_tfr") or 0.
    tfr_incomplete = False
    legacy = item["opening_unallocated"] or 0.
    count, next_due, schedule = 0, None, []
    if mode == "manual":
        total = item.get("invested")
        personal, company, legacy = 0., 0., total or 0.
    elif mode == "monthly":
        monthly = monthly_contributions(item, today)
        count, next_due = monthly["count"], monthly["next_due"]
        personal += count * (item.get("monthly_personal") or 0.)
        company += count * (item.get("monthly_company") or 0.)
        tfr += count * (item.get("monthly_tfr") or 0.)
        tfr_incomplete = item.get("name", "").casefold() == "cometa" and count > 0 and item.get("monthly_tfr") is None
        history = item.get("_pension_history", {}) if item.get("cometa_auto_quotes", True) else {}
        start, end = as_date(item.get("contribution_start")), as_date(item.get("contribution_end"))
        for month, amounts in history.items():
            period = as_date(month+"-01")
            if start:
                offset = (period.year-start.year)*12+period.month-start.month
                due = month_date(start, offset)
                if due < start or due > today or (end and due > end):
                    continue
                personal += amounts["personal"]-(item.get("monthly_personal") or 0.)
                company += amounts["company"]-(item.get("monthly_company") or 0.)
                tfr += amounts.get("tfr", 0.)-(item.get("monthly_tfr") or 0.)
                tfr_incomplete |= "tfr" not in amounts
            elif not (legacy or item["opening_personal"] or item["opening_company"] or item.get("opening_tfr")) and month <= today.strftime("%Y-%m") and (not end or period <= end):
                count += 1
                personal += amounts["personal"]
                company += amounts["company"]
                tfr += amounts.get("tfr", 0.)
                tfr_incomplete |= "tfr" not in amounts
        total = personal+company+tfr+legacy
    else:
        for row in sorted(item["annual_contributions"], key=lambda row: row["year"]):
            due = as_date(row.get("date"))
            configured = row.get("personal") is not None or row.get("company") is not None
            counted = bool(configured and ((due and due <= today) or
                           (not due and row.get("confirmed") and row["year"] <= today.year)))
            schedule.append(dict(row, counted=counted))
            if counted:
                count += 1
                personal += row.get("personal") or 0.
                company += row.get("company") or 0.
            elif due and due > today and (next_due is None or due < next_due):
                next_due = due
        total = personal+company+legacy
    if mode == "monthly" and item.get("balance_date") and item.get("name", "").casefold() != "cometa":
        anchor = as_date(item["balance_date"])
        if today >= anchor:
            later = max(0, monthly_contributions(item, today)["count"]-monthly_contributions(item, anchor)["count"])
            personal = (item.get("balance_personal") or 0.)+later*(item.get("monthly_personal") or 0.)
            company = (item.get("balance_company") or 0.)+later*(item.get("monthly_company") or 0.)
            legacy = 0.
            total = personal+company+tfr
    value = item.get("current_value")
    value_date = as_date(item.get("value_date"))
    valuation_total = total
    if value is not None and value_date and value_date < today:
        valuation_total = investment_summary(dict(item, current_value=None), value_date)["total"]
    gain = round(value+(item.get("withdrawn") or 0.)-valuation_total, 2) if value is not None and valuation_total is not None else None
    if item.get("name", "").casefold() == "cometa":
        gain = None  # Historical coverage and opening balances may still be partial.
    return dict(mode=mode, personal=round(personal, 2), company=round(company, 2), unallocated=legacy,
                total=round(total, 2) if total is not None else None, count=count, next_due=next_due,
                schedule=schedule, gain=gain, tfr=round(tfr, 2), tfr_incomplete=tfr_incomplete)


def investment_performance(record, today):
    """Compare the manually supplied valuation with contributions at its date."""
    item = investment_record(record)
    value = item.get("current_value")
    stamp = as_date(item.get("value_date"))
    reference_date = min(stamp,today) if stamp else today
    reference = investment_summary(dict(item,current_value=None),reference_date)
    base = reference["total"]
    amount = round(value+(item.get("withdrawn") or 0.)-base,2) if value is not None and base is not None else None
    partial = item.get("name", "").casefold() == "cometa" or any(
        row["counted"] and row.get("company") is None for row in reference["schedule"])
    return dict(amount=amount,percent=100*amount/base if amount is not None and base and base>0 else None,
                partial=partial,basis=base,date=reference_date)


def _performance_html(result, compact=False, label=None):
    if result["amount"] is None:
        return '<div class="tracker-note">Scarto: — · controvalore da aggiornare</div>'
    amount = result["amount"]
    tone = "gain" if amount>0 else "loss" if amount<0 else "muted"
    signed_amount = ("+" if amount>0 else "−" if amount<0 else "")+money(abs(amount))
    percent = f'{result["percent"]:+.2f}%'.replace('.',',') if result["percent"] is not None else '—'
    label = label or ('Scarto' if compact else 'Scarto sul registrato' if result["partial"] else 'Scarto sul versato')
    partial = '<span class="holding-result-note">Confronto parziale</span>' if result["partial"] else ''
    variant = ' holding-result-compact' if compact else ''
    return (f'<div class="holding-result{variant} tracker-ink-{tone}"><span>{label}</span>'
            f'<strong>{signed_amount} <small>{percent}</small></strong>{partial}</div>')


def validate_investment(record, today, paid_today=None):
    item = investment_record(record)
    mode = item["contribution_mode"]
    if mode not in {"manual", "monthly", "annual"}:
        raise ValueError("Tipo di versamento non valido.")
    item["product"] = str(item.get("product", ""))[:60]
    for key in ("monthly_personal", "monthly_company", "opening_personal", "opening_company", "opening_unallocated"):
        item[key] = _number(item.get(key))
    for key in ("balance_personal", "balance_company", "leonardo_personal", "leonardo_value", "closed_personal", "closed_value", "pac_initial_personal"):
        if key in item:
            item[key] = _number(item[key])
    if item.get("balance_date"):
        anchor = as_date(item["balance_date"])
        if anchor > today:
            raise ValueError("Il saldo versato non può avere una data futura.")
        item["balance_date"] = anchor.isoformat()
    for key in ("monthly_tfr", "opening_tfr"):
        if key in item:
            item[key] = _number(item[key])
    for key in ("contribution_start", "contribution_end", "redemption_date", "value_date"):
        value = as_date(item.get(key))
        item[key] = value.isoformat() if value else None
    if item.get("redemption_year") is not None:
        year = int(item["redemption_year"])
        if year != float(item["redemption_year"]) or not 1980 <= year <= 2100:
            raise ValueError("Anno di ritiro non valido.")
        item["redemption_year"] = year
    if item.get("redemption_date"):
        item["redemption_year"] = as_date(item["redemption_date"]).year
    if mode == "monthly":
        if item.get("contribution_end") and not item.get("contribution_start"):
            raise ValueError("Inserisci anche la data del primo versamento.")
        monthly = monthly_contributions(item, today)
        if paid_today is not None:
            if not item.get("contribution_start"):
                raise ValueError("Imposta il primo versamento prima di correggere il conteggio.")
            if float(paid_today) != int(paid_today) or not 0 <= int(paid_today) <= 1500:
                raise ValueError("Controlla il numero dei versamenti già effettuati.")
            item["contribution_offset"] = int(paid_today)-monthly["calendar_count"]
    if mode == "annual":
        rows, seen = [], set()
        for row in item["annual_contributions"]:
            year = int(row["year"])
            if year != float(row["year"]) or not 1980 <= year <= 2100 or year in seen:
                raise ValueError("Gli anni dei versamenti devono essere validi e diversi.")
            seen.add(year)
            due = as_date(row.get("date"))
            if due and due.year != year:
                raise ValueError(f"La data del versamento {year} deve appartenere allo stesso anno.")
            confirmed = row.get("confirmed", False)
            if not isinstance(confirmed, bool):
                raise ValueError("Controlla la conferma del versamento.")
            if confirmed and (year > today.year or (due and due > today)):
                raise ValueError("Un versamento futuro non può essere già effettuato.")
            if item.get("redemption_date") and due and due > as_date(item["redemption_date"]):
                raise ValueError("Un versamento non può essere successivo al ritiro previsto.")
            clean = dict(year=year, date=due.isoformat() if due else None,
                             personal=_number(row.get("personal")), company=_number(row.get("company")),
                             source=str(row.get("source", ""))[:120], confirmed=confirmed)
            if item.get("share_plan"):
                clean.update({key:row.get(key) for key in ("personal_shares","matching_shares","welcome_shares","share_price")})
                clean = share_purchase(clean)
            rows.append(clean)
        item["annual_contributions"] = sorted(rows, key=lambda row: row["year"])
        if item.get("redemption_year") and seen and max(seen) > item["redemption_year"]:
            raise ValueError("Il ritiro previsto precede un anno di versamento.")
    summary = investment_summary(item, today)
    for personal_key, value_key in (("leonardo_personal", "leonardo_value"), ("closed_personal", "closed_value")):
        if item.get(personal_key) is not None and summary["total"] is not None and item[personal_key] > summary["total"]:
            raise ValueError("La quota del singolo piano è già compresa nel versato totale e non può superarlo.")
        if item.get(value_key) is not None and item.get("current_value") is not None and item[value_key] > item["current_value"]:
            raise ValueError("Il controvalore del singolo piano non può superare il controvalore totale.")
    if item.get("pac_initial_personal") is not None and summary["total"] is not None:
        pac_total = round(summary["total"]-(item.get("leonardo_personal") or 0.),2)
        if item["pac_initial_personal"] > pac_total:
            raise ValueError("Il versamento iniziale del PAC è già compreso nel versato del PAC e non può superarlo.")
    return item


def holding_result(record, today=None):
    if today is not None:
        return investment_summary(record, today)["gain"]
    invested, value = record.get("invested"), record.get("current_value")
    if invested is None or value is None:
        return None
    return round(value + (record.get("withdrawn") or 0.) - invested, 2)


def calendar_reminder(record, today):
    if record["kind"] == "auto":
        due = car_deadline(record)
        advance = int(record.get("reminder_days", 30))
    else:
        summary = instalment_summary(record, today)
        due = summary["next_due"] if summary else None
        advance = 3
    if due is None:
        return None
    def quoted(text):
        return str(text).replace("\\", "\\\\").replace("\r", "").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")
    return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Spese Personali//Scadenze//IT",
        "BEGIN:VEVENT", f"UID:{quoted(record['id'])}-{due.isoformat()}@spese-personali",
        f"DTSTAMP:{today.strftime('%Y%m%d')}T000000Z", f"DTSTART;VALUE=DATE:{due.strftime('%Y%m%d')}",
        f"DTEND;VALUE=DATE:{(due+timedelta(days=1)).strftime('%Y%m%d')}",
        f"SUMMARY:{quoted(record['name'])}", "BEGIN:VALARM", f"TRIGGER:-P{advance}D",
        "ACTION:DISPLAY", f"DESCRIPTION:{quoted(record['name'])}", "END:VALARM", "END:VEVENT", "END:VCALENDAR", ""])


def money(value):
    if value is None:
        return "—"
    return f"€{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _ink(value, tone, color_missing=False):
    text = str(value)
    if text == '—' and not color_missing:tone = 'muted'
    return f'<strong class="tracker-ink-{tone}">{escape(text)}</strong>'


def _amount(value, tone, color_missing=False):
    return _ink(money(value),tone,color_missing=color_missing)


def date_label(value):
    day = as_date(value)
    return day.strftime("%d/%m/%Y") if day else "—"


def _card(item, content):
    notes = '<div class="tracker-note">'+escape(item.get("notes", ""))+'</div>' if item.get("notes") else ""
    paid = ''
    if item.get('kind') == 'auto':
        paid = f'<div class="car-paid-total" title="Totale di tutti i pagamenti registrati nello storico, escluse le previsioni"><span>Totale pagato</span><strong style="color:{car_history.card_color(item)}">{money(car_history.total_paid(item))}</strong></div>'
    title = escape(item["name"])
    if item.get("kind") == "auto":
        icon = {"auto-0":"🧾", "auto-1":"🛡️", "auto-2":"🔧",
                "auto-3":"🛞", "auto-4":"🔍"}.get(item.get("id"),"🚗")
        title = f'<span aria-hidden="true">{icon}</span> '+title
    if item.get("kind") == "investments" and item["name"].casefold() == "moneyfarm · pac 7":
        title = "Moneyfarm"
    if item.get("kind") == "investments":
        icon = {"moneyfarm · pac 7":"🌱", "alleanza assicurazioni":"🛡️",
                "cometa":"☄️", "leonardo · jp morgan":"📈"}.get(item["name"].casefold(),"💼")
        title = f'<span aria-hidden="true">{icon}</span> '+title
    if item.get("kind") == "installments":
        name = item["name"].casefold()
        icon = ("🛡️" if "assicuraz" in name else "🍽️" if "cucina" in name
                else "🚗" if "auto" in name or "macchina" in name
                else "🏠" if "mutuo" in name or "casa" in name else "💳")
        title = f'<span aria-hidden="true">{icon}</span> '+title
    return '<div class="tracker-card"><strong>'+title+"</strong>"+paid+content+notes+'</div>'


def _holding_metric(label, value):
    return f'<div><span>{escape(label)}</span>{value}</div>'


def _holding_metrics(cells, headline=False):
    return '<div class="holding-metrics'+(' holding-headline' if headline else '')+'">'+''.join(_holding_metric(label,value) for label,value in cells)+'</div>'


def _component_performance(paid, value):
    delta = round(value-paid,2) if paid is not None and value is not None else None
    return dict(amount=delta,percent=100*delta/paid if delta is not None and paid and paid>0 else None,partial=False)


def _share_card_content(item, today, summary, valuation, include_years=True):
    content = _holding_metrics([("Versato da te",_amount(summary["personal"],"paid")),
                                ("Controvalore",valuation)],headline=True)
    content += '<div class="share-schedule">'+_holding_metrics([("Versamenti",_ink("Ogni luglio · welfare","muted")),
                                 ("Ritiro previsto",_ink(item.get("redemption_year") or "Da impostare","due"))])+'</div>'
    counted = [row for row in summary["schedule"] if row["counted"]]
    own = sum(row.get("personal_shares") or 0 for row in counted)
    company = sum((row.get("matching_shares") or 0)+(row.get("welcome_shares") or 0) for row in counted)
    content += _holding_metrics([("Azioni = tue + azienda",_ink(f"{own+company} = {own} + {company}","value")),
                                 ("Azienda · valore iniziale",_amount(summary["company"],"company"))])
    stamp = as_date(item.get("value_date"))
    reference = investment_summary(dict(item,current_value=None),min(stamp,today) if stamp else today)
    value = item.get("current_value")
    personal_return = _component_performance(reference["personal"],value+(item.get("withdrawn") or 0.) if value is not None else None)
    content += '<div class="share-personal-return">'+_performance_html(personal_return,label="Scarto sul tuo versato · incluse azioni gratuite")+'</div>'
    market = investment_performance(item,today)
    content += '<div class="share-market">'+_performance_html(market,compact=True,label="Andamento azioni · variazione di mercato")
    content += ('<div class="share-market-explanation">Confronto con il valore iniziale di tutte le azioni ('
                +money(reference["total"])+'), incluse quelle gratuite. Lo scarto sopra considera invece solo i tuoi '
                +money(reference["personal"])+'.'
                +(' Entrambi includono gli eventuali prelievi.' if item.get("withdrawn") else '')+'</div></div>')
    if include_years:
        content += _share_years_content(item,summary)
    return content


def _share_years_content(item, summary, wide=False):
    columns = []
    rows = {row["year"]:row for row in summary["schedule"]}
    years = sorted(set(rows)|{item.get("redemption_year") or 2028})
    for year in years:
        row = rows.get(year,{})
        retirement = year == item.get("redemption_year") and year not in rows
        if retirement:
            counted = [purchase for purchase in summary["schedule"] if purchase["counted"]]
            own = sum(purchase.get("personal_shares") or 0 for purchase in counted)
            company = sum((purchase.get("matching_shares") or 0)+(purchase.get("welcome_shares") or 0) for purchase in counted)
            total = own+company
            value = item.get("current_value")
            price = value/total if value is not None and total else None
            columns.append(f'<div><strong>{year}</strong><span>Ritiro · stima ultimo valore</span>'
                           '<span>Azioni totali</span>'+_ink(total if counted else "—","value",True)
                           +'<div class="share-purchase">'+_holding_metrics([
                               ("Tue",_ink(own if counted else "—","paid",True)),
                               ("Azienda",_ink(company if counted else "—","company",True))])+'</div>'
                           +'<span>Valore per azione</span>'+_ink('≈ '+money(price) if price is not None else "—","value",True)
                           +'<span>Ritiro stimato</span>'+_ink('≈ '+money(value) if value is not None else "—","value",True)
                           +('<span>Al '+date_label(item["value_date"])+'</span>' if item.get("value_date") else '')+'</div>')
            continue
        missing = row.get("personal_shares") is None
        if missing:
            caption = "Luglio · azioni da inserire"
        else:
            caption = 'Luglio · '+('acquistate' if row["counted"] else 'previste')+' da te'
        gifted = (row.get("matching_shares") or 0)+(row.get("welcome_shares") or 0) if not missing else None
        detail = (f'<div><strong>{year}</strong><span>{caption}</span>'
                  +'<div class="share-purchase">'
                  +_holding_metrics([("Azioni",_ink(row["personal_shares"] if not missing else "—","value",True)),
                                     ("Prezzo cad.",_amount(row.get("share_price"),"value",True))])+'</div>'
                  +'<span>Costo tuo</span>'+_amount(row.get("personal"),"paid",True)
                  +'<span>Azioni gratuite</span><div class="share-award">'+_ink(str(gifted) if gifted is not None else "—","company",True))
        if row.get("welcome_shares"):
            detail += '<small>'+str(row.get("matching_shares") or 0)+' + '+str(row["welcome_shares"])+ ' iniziali</small>'
        detail += '</div><span>Valore assegnato</span>'+_amount(row.get("company"),"company",True)+'</div>'
        columns.append(detail)
    content = '<div class="holding-split share-years-grid'+(' share-years-wide' if wide else '')+'">'+''.join(columns)+'</div>'
    content += '<div class="tracker-note">Il controvalore è il valore delle azioni oggi, prima di eventuali costi e imposte al ritiro.</div>'
    return content


def _investment_card_content(record, today, include_share_years=True):
    item = investment_record(record)
    summary = investment_summary(item,today)
    pension = item.get("name", "").casefold() == "cometa"
    valuation = _amount(item.get("current_value"),"value")
    if item.get("value_date"):
        valuation += f'<small class="holding-date">Al {date_label(item["value_date"])}</small>'
    if item.get("share_plan"):
        return _share_card_content(item,today,summary,valuation,include_years=include_share_years)
    content = _holding_metrics([
        ("Totale versato",_amount(summary["total"],"paid")),
        ("Controvalore",valuation)],headline=True)
    pension_content = ''
    if pension:
        monthly = sum(item.get(key) or 0. for key in ("monthly_personal","monthly_company","monthly_tfr"))
        period_label = "Mensile"
        if item.get("monthly_source_month") and item.get("cometa_auto_quotes",True):
            try:
                period = date.fromisoformat(item["monthly_source_month"]+"-01")
                period_label = ("Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
                                "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre")[period.month-1]
            except (TypeError, ValueError):
                pass
        pension_content = ('<div class="holding-split pension-split"><div><div class="pension-column-title"><strong>'+period_label+'</strong>'
                    +_amount(monthly,"value")+'</div>'
                    +_holding_metrics([("Tu",_amount(item.get("monthly_personal"),"value")),
                                       ("Azienda",_amount(item.get("monthly_company"),"company")),
                                       ("TFR",_amount(item.get("monthly_tfr"),"tfr"))])
                    +'</div><div><div class="pension-column-title"><strong>Totale</strong></div>'
                    +_holding_metrics([("Tu",_amount(summary["personal"],"paid")),
                                       ("Azienda",_amount(summary["company"],"company")),
                                       ("TFR",_amount(summary["tfr"],"tfr"))])+'</div></div>')
    details = []
    if summary["mode"] != "manual" and not item.get("personal_only") and not pension:
        details.extend([("Tu",_amount(summary["personal"],"paid")),("Azienda",_amount(summary["company"],"company"))])
    if summary["mode"] == "monthly":
        monthly = (item.get("monthly_personal") or 0.)+(item.get("monthly_company") or 0.)+(item.get("monthly_tfr") or 0.)
        if not pension:
            details.append(("Ogni mese",_amount(monthly,"value")))
        if not item.get("personal_only") and not pension:
            details.extend([("Mese · tu",_amount(item.get("monthly_personal"),"value")),("Mese · azienda",_amount(item.get("monthly_company"),"company"))])
        if pension:
            payday = date(today.year,today.month,27)
            if payday < today:
                payday = month_date(payday,1)
            average = pension_monthly_average(item,today)
            monthly_display = (f'<strong class="tracker-ink-value" title="Media degli ultimi {average["count"]} cedolini completi, incluse le quote TFR">'
                               +'≈ '+money(average["amount"])+'</strong>') if average["count"] else _amount(monthly,"value")
            details.extend([("Ogni mese",monthly_display),
                            ("Prossimo pagamento",_ink(date_label(payday),"due"))])
        elif item.get("contribution_start"):
            details.append(("Prossimo versamento",_ink(date_label(summary["next_due"]),"due")))
        if item.get("contribution_end"):
            details.append(("Fine versamenti",_ink(date_label(item["contribution_end"]),"due")))
        if not pension and item.get("monthly_source_month") and item.get("cometa_auto_quotes",True):
            details.append(("Quote da cedolino",_ink(item["monthly_source_month"],"muted")))
        if not pension and item.get("monthly_tfr_source_month") and item.get("monthly_tfr_source_month") != item.get("monthly_source_month"):
            details.append(("TFR da cedolino",_ink(item["monthly_tfr_source_month"],"muted")))
    elif summary["mode"] == "annual":
        redemption = date_label(item["redemption_date"]) if item.get("redemption_date") else str(item.get("redemption_year") or "Da impostare")
        details.append(("Ritiro previsto",_ink(redemption,"due")))
    if item.get("withdrawn"):
        details.append(("Prelevato",_amount(item["withdrawn"],"due")))
    if summary["unallocated"]:
        details.append(("Da ripartire",_amount(summary["unallocated"],"due")))
    if details:content += _holding_metrics(details)
    result = investment_performance(item,today)
    content += _performance_html(result)
    content += pension_content
    if item.get("name", "").casefold() == "moneyfarm · pac 7" and item.get("leonardo_personal") is not None:
        pac_paid = summary["total"]-item["leonardo_personal"] if summary["total"] is not None else None
        pac_value = item["current_value"]-item["leonardo_value"] if item.get("current_value") is not None and item.get("leonardo_value") is not None else None
        # The valuation is manual: compare its components with contributions
        # at that same date, not with later automatically counted deposits.
        pac_basis = result["basis"]-item["leonardo_personal"] if result["basis"] is not None else None
        initial = f'<span class="holding-initial">Iniziali {_amount(item["pac_initial_personal"],"paid")}</span>' if item.get("pac_initial_personal") is not None else ''
        content += ('<div class="holding-split">'
                    f'<div><strong>PAC 7 · ETF</strong><span>{money(item.get("monthly_personal"))} al mese</span><span>Versato</span>{_amount(pac_paid,"paid")}'
                    f'<span>Controvalore</span>{_amount(pac_value,"value")}{initial}'
                    +_performance_html(_component_performance(pac_basis,pac_value),compact=True)+'</div>'
                    f'<div><strong>Leonardo · Azioni</strong><span>Versamento una tantum</span><span>Versato</span>{_amount(item["leonardo_personal"],"paid")}'
                    f'<span>Controvalore</span>{_amount(item.get("leonardo_value"),"value")}'
                    +_performance_html(_component_performance(item["leonardo_personal"],item.get("leonardo_value")),compact=True)+'</div></div>')
    if summary["mode"] == "annual":
        for row in summary["schedule"]:
            label = "versato" if row["counted"] else "previsto" if row.get("date") else "da impostare"
            tone = "paid" if row["counted"] else "due"
            content += f'<div class="holding-year"><span>{row["year"]} · {_ink(label,tone)}</span>'
            content += _holding_metrics([("Tu",_amount(row.get("personal"),tone)),("Azienda",_amount(row.get("company"),"company" if row["counted"] else "due"))])+'</div>'
        sources = list(dict.fromkeys(row["source"] for row in summary["schedule"] if row.get("source")))
        if sources:content += '<div class="tracker-note">Fonte: '+escape(' · '.join(sources))+'</div>'
        if any(row["counted"] and row.get("company") is None for row in summary["schedule"]):
            content += '<div class="tracker-note">Quota azienda da completare</div>'
    if item.get("name", "").casefold() == "alleanza assicurazioni" and (item.get("closed_personal") is not None or item.get("closed_value") is not None):
        active_paid = summary["personal"]+summary["unallocated"]-item["closed_personal"] if item.get("closed_personal") is not None else None
        active_value = item["current_value"]-item["closed_value"] if item.get("current_value") is not None and item.get("closed_value") is not None else None
        active_basis = result["basis"]-item["closed_personal"] if result["basis"] is not None and item.get("closed_personal") is not None else None
        content += ('<div class="holding-split">'
                    f'<div><strong>Smart Evergreen</strong><span>{money(item.get("monthly_personal"))} al mese</span>'
                    f'<span>Versato</span>{_amount(active_paid,"paid")}'
                    f'<span>Controvalore</span>{_amount(active_value,"value")}'
                    +_performance_html(_component_performance(active_basis,active_value),compact=True)+'</div>'
                    '<div><strong>Alleata Previdenza</strong><span>Versamenti sospesi</span>'
                    f'<span>Versato</span>{_amount(item.get("closed_personal"),"paid")}'
                    f'<span>Controvalore</span>{_amount(item.get("closed_value"),"value")}'
                    +_performance_html(_component_performance(item.get("closed_personal"),item.get("closed_value")),compact=True)+'</div></div>')
    if summary["tfr_incomplete"]:
        content += '<div class="tracker-note">Totale parziale · TFR da completare con Sincronizza Drive</div>'
    return content


def cards_html(records, kind, today):
    holdings = [item for item in records if item["kind"] == "investments"] if kind == "investments" else []
    share_items = [item for item in holdings if item.get("share_plan")]
    l_plan = share_items[0] if len(holdings) == 4 and len(share_items) == 1 else None
    share_card = ''
    if kind == "auto":
        order = {identifier:index for index,identifier in enumerate(["auto-0","auto-2","auto-1","auto-3","auto-4"])}
        records = sorted(records,key=lambda item:order.get(item["id"],len(order)))
    if kind == "installments":
        order = {identifier:index for index,identifier in enumerate(["instalment-2","instalment-3","instalment-0","instalment-1"])}
        def maturity(item):
            summary = instalment_summary(item,today) if item["kind"] == kind else None
            return (summary["end"] if summary else date.max,order.get(item["id"],len(order)))
        records = sorted(records,key=maturity)
    cards = []
    for item in records:
        if item["kind"] != kind:
            continue
        if kind == "auto":
            due = car_deadline(item)
            status, color = deadline_status(due, today, item.get("reminder_days", 30))
            content = f'<div class="tracker-value">{date_label(due)}</div><div style="color:{color};">{status}</div>'
            content += f'<div class="tracker-note">Ultima volta: {date_label(item.get("last_date"))}<br>Importo: {money(item.get("amount"))}</div>'
            estimate = car_history.forecast(item)
            if car_history.forecast_visible(item,today):
                planned_label = "Stima dall’ultimo pagamento" if estimate['automatic'] and estimate['amount'] is not None else ("Costo previsto" if estimate["date"] > today else "Costo da confermare")
                planned_amount = money(estimate['amount']) if estimate['amount'] is not None else "da definire"
                content += f'<div class="tracker-note" style="color:{car_history.FORECAST_COLOR}">{planned_label}: {planned_amount}</div>'
            content += car_history.chart_html(item, today)
        elif kind == "investments":
            content = _investment_card_content(item,today,include_share_years=l_plan is None)
        else:
            summary = instalment_summary(item, today)
            if summary is None:
                content = '<div class="tracker-note">Imposta prima rata e durata</div>'
            else:
                pct = summary["progress"]*100
                content = f'<div class="tracker-value">{_ink(summary["paid"],"paid")} / {_ink(summary["total"],"muted")} rate</div>'
                content += f'<div class="tracker-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{pct:.1f}"><span style="width:{pct:.2f}%;"></span></div>'
                tone = "due" if summary["remaining"] else "paid"
                content += f'<div>Ancora {_ink(summary["remaining"],tone)} rate · {_amount(summary["remaining_amount"],tone)}</div>'
                content += f'<div class="tracker-note">Pagato da calendario: {_amount(summary["paid_amount"],"paid")}<br>Rata: {_amount(item.get("amount"),"value")}<br>Prossima: {_ink(date_label(summary["next_due"]),"due")}<br>Fine: {_ink(date_label(summary["end"]),"company")}</div>'
                if item.get("paid_offset"):
                    content += '<div class="tracker-note">Conteggio corretto manualmente</div>'
        card = _card(item, content)
        if l_plan is not None and item["id"] == l_plan["id"]:
            share_card = card.replace('class="tracker-card"','class="tracker-card share-summary"',1)
        else:
            cards.append(card)
    if kind == "auto" and cards:
        cards.append(car_overview.html(records,today,full_width=len(cards)%2==0))
    if kind == "investments":
        if l_plan is not None:
            years = _share_years_content(investment_record(l_plan),investment_summary(l_plan,today),wide=True)
            return ('<div class="tracker-grid investment-l-layout">'+''.join(cards)+share_card
                    +'<div class="tracker-card share-years">'+years+'</div></div>')
        columns = ''.join('<div class="holding-card-column">'+''.join(cards[index::2])+'</div>' for index in range(2))
        return '<div class="tracker-grid holding-card-grid">'+columns+'</div>'
    return '<div class="tracker-grid">'+"".join(cards)+'</div>'


def _date_input(label, value, key):
    return st.date_input(label, value=as_date(value), min_value=date(1980, 1, 1), max_value=date(2100, 12, 31), format="DD/MM/YYYY", key=key)


def _money_input(label, **kwargs):
    # JSON may encode 100.0 as 100. Streamlit requires all numeric arguments
    # to share the same type, including min_value and step.
    kwargs.setdefault("min_value",0.)
    kwargs.setdefault("step",.01)
    for key in ("value","min_value","max_value","step"):
        if kwargs.get(key) is not None:
            kwargs[key] = float(kwargs[key])
    return st.number_input(label,**kwargs)


def _investment_fields(item, old, prefix, mode, today):
    """Edit actual amounts; contribution schedules stay internal to the register."""
    item["contribution_mode"] = mode
    summary = investment_summary(old, today)
    pension = old.get("name", "").casefold() == "cometa"
    if pension:
        columns = st.columns(3, gap="small")
        for col, label, key in zip(columns, ["Tu · cedolino (€)", "Azienda · cedolino (€)", "TFR · cedolino (€)"],
                                   ["monthly_personal", "monthly_company", "monthly_tfr"]):
            with col:
                _money_input(label, value=old.get(key), disabled=True)
        if old.get("monthly_source_month"):
            st.caption("Quote mensili dall’ultimo cedolino sincronizzato: "+old["monthly_source_month"])
        with st.expander("Importi precedenti ai cedolini disponibili", expanded=False):
            cols = st.columns(3, gap="small")
            for col, label, key in zip(cols, ["Tu (€)", "Azienda (€)", "TFR (€)"],
                                       ["opening_personal", "opening_company", "opening_tfr"]):
                with col:
                    item[key] = _money_input(label, min_value=0., value=float(old.get(key) or 0.), step=100.)
            if old.get("opening_unallocated"):
                item["opening_unallocated"] = _money_input("Precedenti ancora da ripartire (€)", min_value=0., value=float(old["opening_unallocated"]), step=100.)
    elif mode == "monthly":
        cols = st.columns(2 if old.get("personal_only") else 3, gap="small")
        with cols[0]:
            personal = _money_input("Versato da te a oggi (€)", min_value=0., value=summary["personal"]+summary["unallocated"], step=100.,
                help="Totale complessivo già versato. Puoi correggerlo anche dopo un versamento diverso dal solito.")
        company = 0.
        if not old.get("personal_only"):
            with cols[1]:
                company = _money_input("Versato dall’azienda (€)", min_value=0., value=summary["company"], step=100.)
        with cols[-1]:
            item["monthly_personal"] = _money_input("Ogni mese · tu (€)", min_value=0., value=old.get("monthly_personal"), step=10.)
        item.update(balance_personal=personal, balance_company=company, balance_date=today.isoformat())
        # Preserve existing schedules. Stopping a plan never removes its balance.
        item["contribution_end"] = _date_input("Fine versamenti (se interrompi)", old.get("contribution_end"), prefix+"contribution_end")
    elif mode == "annual":
        rows = old.get("annual_contributions") or []
        item["annual_contributions"] = []
        for index, row in enumerate(rows):
            if old.get("share_plan"):
                st.markdown(f"**Luglio {row['year']} · azioni da welfare**")
                cols = st.columns(2,gap="small")
                with cols[0]:
                    shares = st.number_input("Azioni acquistate da te",min_value=0,value=row.get("personal_shares"),step=1,key=prefix+f"shares{index}")
                    matching = st.number_input("Azioni azienda",min_value=0,value=row.get("matching_shares"),step=1,key=prefix+f"matching{index}",help="Se vuoto: una azione ogni tre acquistate da te.")
                with cols[1]:
                    price = _money_input("Prezzo per azione (€)",min_value=0.,value=row.get("share_price"),step=.01,key=prefix+f"price{index}")
                    welcome = st.number_input("Azioni bonus iniziale",min_value=0,value=row.get("welcome_shares") or 0,step=1,key=prefix+f"welcome{index}")
                confirmed = st.checkbox("Già acquistate",value=bool(row.get("confirmed",False)),key=prefix+f"confirmed{index}")
                item["annual_contributions"].append(dict(row,date=f"{row['year']}-07-01",personal_shares=shares,
                    share_price=price,matching_shares=matching,welcome_shares=welcome,confirmed=confirmed,source="Welfare · premio di risultato"))
                continue
            st.markdown(f"**{row['year']} · dal premio di risultato**")
            cols = st.columns(3, gap="small")
            with cols[0]:
                personal = _money_input("Tu (€)", min_value=0., value=row.get("personal"), step=100., key=prefix+f"personal{index}")
            with cols[1]:
                company = _money_input("Azienda (€)", min_value=0., value=row.get("company"), step=100., key=prefix+f"company{index}")
            with cols[2]:
                due = _date_input("Data versamento", row.get("date"), prefix+f"annual_date{index}")
            confirmed = st.checkbox("Già versato", value=bool(row.get("confirmed", False)), key=prefix+f"confirmed{index}")
            item["annual_contributions"].append(dict(row, date=due, personal=personal, company=company, confirmed=confirmed))
        item["redemption_year"] = st.number_input("Anno ritiro", min_value=1980, max_value=2100, value=old.get("redemption_year") or 2028)
    else:
        item["invested"] = _money_input("Versato complessivo (€)", min_value=0., value=old.get("invested"), step=100.)
    value_col, withdrawn_col = st.columns(2, gap="small")
    with value_col:
        item["current_value"] = _money_input("Controvalore totale (€)", min_value=0., value=old.get("current_value"), step=100.)
    if item["current_value"] != old.get("current_value"):
        item["value_date"] = today.isoformat() if item["current_value"] is not None else None
    with withdrawn_col:
        item["withdrawn"] = _money_input("Totale prelevato (€)", min_value=0., value=float(old.get("withdrawn") or 0.), step=100.)
    name = old.get("name", "").casefold()
    if name == "moneyfarm · pac 7":
        st.markdown("**Ripartizione Moneyfarm · già compresa nei totali**")
        initial, a, b = st.columns(3, gap="small")
        with initial:
            item["pac_initial_personal"] = _money_input("PAC · importo iniziale (€)", min_value=0., value=old.get("pac_initial_personal"), step=100.)
        with a:
            item["leonardo_personal"] = _money_input("Versato in Leonardo (€)", min_value=0., value=old.get("leonardo_personal"), step=100.)
        with b:
            item["leonardo_value"] = _money_input("Controvalore Leonardo (€)", min_value=0., value=old.get("leonardo_value"), step=100.)
    elif name == "alleanza assicurazioni":
        st.markdown("**Alleata Previdenza · versamenti sospesi**")
        a, b = st.columns(2, gap="small")
        with a:
            item["closed_personal"] = _money_input("Alleata · versato (€)", min_value=0., value=old.get("closed_personal"), step=100.)
        with b:
            item["closed_value"] = _money_input("Alleata · controvalore (€)", min_value=0., value=old.get("closed_value"), step=100.)
        st.caption("Alleata è già compresa nei totali. La differenza è Smart Evergreen, che prosegue con il versamento mensile senza scadenza.")
    return None


@st.fragment
def render_tracker(records, kind, save_record, saved, today):
    with st.container(key=f"personal-tracker-{kind}"):
        title = {"auto": "🚗 Auto · scadenze e manutenzione", "investments": "📈 Investimenti", "installments": "🗓️ Rate e finanziamenti"}[kind]
        if kind == "auto" and records is not None:
            upcoming = sorted((car_deadline(item), item["name"]) for item in records if item["kind"] == "auto" and car_deadline(item) is not None)
            next_label = ''
            if upcoming:
                days = (upcoming[0][0]-today).days
                nearest = next(item for item in records if item["kind"] == "auto" and item["name"] == upcoming[0][1] and car_deadline(item) == upcoming[0][0])
                accent = "#fda4af" if days < 0 else "#fcd34d" if days <= int(nearest.get("reminder_days",30)) else "#7dd3fc"
                next_label = (f'<span style="flex:0 0 auto;max-width:40%;text-align:right;padding:7px 10px;border-radius:12px;'
                              f'border:1px solid {accent}40;background:linear-gradient(135deg,{accent}18,rgba(255,255,255,.02));line-height:1.4;">'
                              f'<span style="display:block;font-size:11px;color:#cbd5e1;">{escape(upcoming[0][1])}</span>'
                              f'<strong style="display:block;white-space:nowrap;font-size:13px;color:{accent};letter-spacing:.3px;">{date_label(upcoming[0][0])}</strong></span>')
            st.html(f'<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px;"><h3 style="margin:0;padding:0;min-width:0;font-size:20px;line-height:1.3;">{escape(title)}</h3>{next_label}</div>')
        elif kind == "installments" and records is not None:
            summaries = [instalment_summary(item, today) for item in records if item["kind"] == kind]
            remaining = [row["remaining_amount"] for row in summaries if row and row["remaining_amount"] is not None]
            total = money(math.fsum(remaining) if remaining else None)
            st.html(f'<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px;">'
                    f'<h3 style="margin:0;padding:0;min-width:0;font-size:20px;line-height:1.3;">{escape(title)}</h3>'
                    f'<span style="flex:0 0 auto;max-width:43%;text-align:right;padding:7px 10px;border-radius:12px;border:1px solid #fb923c40;background:linear-gradient(135deg,#fb923c18,rgba(255,255,255,.02));line-height:1.4;">'
                    f'<span style="display:block;font-size:11px;color:#cbd5e1;">Totale residuo</span>'
                    f'<strong style="display:block;white-space:nowrap;font-size:13px;color:#fb923c;letter-spacing:.3px;">{total}</strong></span></div>')
        else:
            st.subheader(title)
        if records is None:
            st.info("Registro non disponibile. Riprova il caricamento prima di modificare i dati.")
            return
        items = [investment_record(item) if kind == "investments" else car_history.car_record(item, today) if kind == "auto" else item for item in records if item["kind"] == kind]
        if kind == "investments":
            summaries = [investment_summary(item, today) for item in items]
            totals = [row["total"] for row in summaries if row["total"] is not None]
            values = [item["current_value"] for item in items if item.get("current_value") is not None]
            missing_values = [item["name"] for item in items if item.get("current_value") is None]
            capital_label = "Capitale totale · parziale" if values and missing_values else "Capitale totale"
            st.html(f'<div class="tracker-totals"><div><span>Versato complessivo</span>{_amount(sum(totals) if totals else None,"paid")}</div>'
                    f'<div><span>{capital_label}</span>{_amount(math.fsum(values) if values else None,"value")}</div></div>')
            if missing_values:
                st.html('<div class="tracker-section-note">Controvalori da aggiornare: '+escape(', '.join(missing_values))+'.</div>')
        st.html("""<style>
          [class*="st-key-personal-tracker-"] [data-testid="stCaptionContainer"] {color:#94a3b8;}
          [class*="st-key-personal-tracker-"] [data-testid="stWidgetLabel"] p {white-space:normal!important;font-size:11px!important;color:#cbd5e1!important;}
          [class*="st-key-personal-tracker-"] [data-testid="stExpander"] [data-testid="stHorizontalBlock"] {flex-wrap:nowrap!important;gap:8px!important;}
          [class*="st-key-personal-tracker-"] [data-testid="stExpander"] [data-testid="stHorizontalBlock"] > :is([data-testid="stColumn"],[data-testid="column"]) {min-width:0!important;}
          [class*="st-key-personal-tracker-"] [data-testid="stExpander"] :is(input,button) {font-size:12px!important;min-width:0;}
          [class*="st-key-personal-tracker-"] [data-testid="stExpander"] [data-testid="stVerticalBlock"] {gap:9px;}
          .tracker-grid {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;}
          .tracker-card {min-width:0;padding:12px;border:.5px solid rgba(255,255,255,.12);border-radius:14px;background:rgba(255,255,255,.05);font-size:12px;color:#cbd5e1;overflow-wrap:anywhere;}
          .tracker-card>strong {display:block;color:#e2e8f0;font-size:13px;margin-bottom:7px;}
          .tracker-value {font-family:"DM Mono",monospace;color:#60a5fa;font-size:17px;margin:5px 0 8px;}
          .tracker-note {font-size:11px;color:#94a3b8;line-height:1.5;margin-top:7px;white-space:pre-line;}
          .holding-card-grid {align-items:start;}
          .holding-card-column {display:flex;flex-direction:column;gap:8px;min-width:0;}
          .investment-l-layout {align-items:stretch;}
          .investment-l-layout>.tracker-card {background:#222936;}
          .investment-l-layout>.share-summary {position:relative;margin:6px 0 0 6px;border-bottom:0;border-radius:14px 14px 0 0;z-index:1;}
          .share-summary::after {content:"";position:absolute;left:-.5px;right:-.5px;bottom:-14px;height:14px;background:#222936;border-left:.5px solid rgba(255,255,255,.12);border-right:.5px solid rgba(255,255,255,.12);}
          .share-schedule {margin-top:15px;}
          .share-schedule>.holding-metrics {margin-top:0;}
          .share-personal-return {margin-top:12px;}
          .share-personal-return>.holding-result {margin-top:0;}
          .share-market {margin-top:9px;}
          .share-market-explanation {font-size:9px;color:#94a3b8;line-height:1.4;margin:5px 5px 0;}
          .investment-l-layout>.share-years {grid-column:1/-1;position:relative;margin:6px 0 0 6px;border-top-right-radius:0;}
          .share-years::before {content:"";position:absolute;right:0;top:-1px;width:calc(50% - 7px);height:2px;background:#222936;}
          .share-years-grid.holding-split {margin-top:0;padding-top:0;border-top:0;}
          .share-years-wide.holding-split {grid-template-columns:repeat(4,minmax(0,1fr));}
          .holding-metrics {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px 9px;margin-top:8px;}
          .holding-metrics>div {min-width:0;}
          .holding-metrics>div:nth-child(even) {text-align:right;}
          .holding-metrics>div>span {display:block;font-size:9px;color:#94a3b8;line-height:1.35;margin-bottom:2px;}
          .holding-metrics>div>strong {display:block;font-size:11px;line-height:1.35;overflow-wrap:anywhere;}
          .holding-date {display:block;font-size:9px;color:#94a3b8;margin-top:3px;line-height:1.3;}
          .holding-headline {margin:4px 0 9px;gap:9px;}
          .holding-headline>div>strong {font-size:14px;font-family:"DM Mono",monospace;}
          .pension-column-title {display:flex;justify-content:space-between;align-items:baseline;gap:3px;min-height:15px;}
          .pension-column-title>strong {font-size:10px;white-space:nowrap;}
          .pension-split .holding-metrics {grid-template-columns:repeat(2,minmax(0,1fr));gap:7px 3px;margin-top:13px;padding:7px 4px;border:1px solid rgba(203,213,225,.07);border-radius:7px;}
          .pension-split .holding-metrics>div {text-align:left;}
          .pension-split .holding-metrics>div:nth-child(2) {text-align:right;}
          .pension-split .holding-metrics>div:last-child {grid-column:1/-1;text-align:center;}
          .pension-split .holding-metrics>div>strong {font-size:9px;white-space:nowrap;overflow-wrap:normal;}
          @media(max-width:430px) {.holding-headline>div>strong {font-size:11px;}}
          .holding-year {border-top:1px solid rgba(255,255,255,.08);padding-top:6px;margin-top:8px;}
          .holding-year>span {font-size:10px;color:#cbd5e1;}
          .holding-year .holding-metrics {margin-top:4px;}
          .holding-result-compact {padding:5px!important;margin-top:auto!important;}
          .holding-split .holding-result-compact {border-top:6px solid transparent;background-clip:padding-box;}
          .holding-result-compact>strong {display:flex!important;align-items:baseline;gap:4px;white-space:nowrap;font-size:10px!important;}
          .holding-result-compact small {display:inline;margin:0!important;font-size:8px!important;}
          .holding-split {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:10px;padding-top:9px;border-top:1px solid rgba(255,255,255,.09);}
          .holding-split>div {min-width:0;display:flex;flex-direction:column;}
          .holding-split>div+div {border-left:1px solid rgba(255,255,255,.09);padding-left:8px;}
          .holding-split>div>strong:first-child {display:block;font-size:11px;color:#cbd5e1;margin-bottom:0;}
          .holding-split>div>span {display:block;font-size:9px;color:#94a3b8;margin-top:6px;}
          .holding-split>div>strong:first-child+span {margin-top:0;font-size:7px;line-height:1.2;}
          .holding-split>div>strong:first-child+span+span {margin-top:9px;}
          .holding-split>div>strong:not(:first-child) {display:block;font-size:11px;font-family:"DM Mono",monospace;overflow-wrap:anywhere;}
          .share-purchase .holding-metrics {gap:3px;margin-top:8px;}
          .share-purchase .holding-metrics>div>span {font-size:7px;}
          .share-purchase .holding-metrics>div>strong {font-size:9px;white-space:nowrap;overflow-wrap:normal;}
          .share-award {display:flex;justify-content:space-between;align-items:baseline;gap:3px;}
          .share-award>strong {font-size:11px;}
          .share-award>small {font-size:7px;color:#94a3b8;text-align:right;}
          .holding-initial strong {font-size:9px;overflow-wrap:anywhere;}
          .holding-result {margin-top:8px;padding:7px 8px;border-radius:9px;background:rgba(255,255,255,.035);}
          .holding-result>span {display:block;font-size:10px;color:#94a3b8;font-weight:400;}
          .holding-result>strong {display:flex;justify-content:space-between;align-items:baseline;gap:4px;white-space:nowrap;font-size:13px;margin-top:2px;}
          .holding-result small {font-size:10px;white-space:nowrap;margin-left:4px;}
          .holding-result>.holding-result-note {font-size:9px;margin-top:3px;}
          .tracker-totals {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-bottom:8px;}
          .tracker-totals>div {padding:9px 11px;border:1px solid rgba(255,255,255,.08);border-radius:12px;background:rgba(255,255,255,.035);min-width:0;}
          .tracker-totals span {display:block;font-size:11px;color:#94a3b8;margin-bottom:3px;}
          .tracker-totals strong {font-family:"DM Mono",monospace;font-size:17px;overflow-wrap:anywhere;}
          .tracker-section-note {font-size:11px;color:#94a3b8;line-height:1.5;margin-bottom:8px;}
          .tracker-ink-paid {color:#dca982;font-weight:650;}
          .tracker-ink-gain {color:#34d399;font-weight:650;}
          .tracker-ink-due {color:#fb923c;font-weight:650;}
          .tracker-ink-value {color:#7dd3fc;font-weight:650;}
          .tracker-ink-company {color:#c4b5fd;font-weight:650;}
          .tracker-ink-tfr {color:#fde68a;font-weight:650;}
          .tracker-ink-loss {color:#f87171;font-weight:650;}
          .tracker-ink-muted {color:#94a3b8;font-weight:500;}
          .st-key-personal-tracker-auto .tracker-card {padding:9px;}
          .st-key-personal-tracker-auto .tracker-card>strong {margin-bottom:4px;font-size:12px;}
          .st-key-personal-tracker-auto .tracker-value {font-size:15px;margin:3px 0 5px;}
          .st-key-personal-tracker-auto .tracker-note {font-size:10px;margin-top:4px;line-height:1.35;}
          .car-overview {min-width:0;padding:9px 3px;color:#cbd5e1;align-self:start;font-size:10px;}
          .car-overview>strong {display:block;color:#e2e8f0;font-size:12px;margin-bottom:7px;}
          .car-overview-note {font-size:10px;color:#94a3b8;line-height:1.4;margin:4px 0;}
          .car-overview-monthly {color:#34d399;font-size:14px;font-weight:700;line-height:1.4;}
          .car-overview-due {font-size:10px;color:#cbd5e1;line-height:1.4;margin-top:7px;}
          .car-overview-due>strong {color:#fb923c;}
          .car-overview-footnote {font-size:9px;color:#94a3b8;line-height:1.35;margin-top:6px;}
          .car-paid-total {float:right;max-width:46%;margin:3px 0 5px 7px;text-align:right;line-height:1.3;}
          .car-paid-total>span {display:block;font-size:9px;color:#94a3b8;}
          .car-paid-total>strong {display:block;font-size:11px;font-family:"DM Mono",monospace;}
          .car-mini-history {clear:both;margin-top:7px;padding-top:5px;border-top:1px solid rgba(255,255,255,.08);}
          .car-history-heading {font-size:11px;color:#94a3b8;}
          .car-history-latest {display:flex;justify-content:space-between;gap:5px;font-size:10px;color:#94a3b8;margin-top:3px;}
          .car-history-empty {min-height:65px;margin-top:7px;padding-top:5px;border-top:1px solid rgba(255,255,255,.08);font-size:11px;color:#94a3b8;}
          .car-history-empty span {display:block;margin-top:6px;line-height:1.5;}
          .tracker-progress {height:5px;border-radius:5px;background:rgba(255,255,255,.12);overflow:hidden;margin:8px 0;}
          .tracker-progress>span {display:block;height:100%;background:#34d399;}
        </style>""")
        if items:
            st.html(cards_html(records, kind, today))
        else:
            st.info("Nessuna voce. Aggiungila dal menu qui sotto.")
        with st.expander("Aggiungi o modifica", expanded=False):
            identifiers = ["__new__"] + [item["id"] for item in items]
            lookup = {item["id"]: item for item in items}
            # Header slots share one row. Draft widgets below still belong to the
            # save form through its active context; selecting a record remains immediate.
            header = st.columns(3, gap="small")
            with header[0]:
                selection = st.selectbox("Voce", identifiers, format_func=lambda key: "＋ Nuova voce" if key == "__new__" else lookup[key]["name"], key=f"tracker_select_{kind}")
            name_slot = header[1].empty()
            date_slot = header[2].empty()
            old = lookup.get(selection, dict(id="", kind=kind, name="", notes=""))
            prefix = f"tracker_{kind}_{selection}"
            if kind == "investments":
                old = investment_record(old)
                mode = old["contribution_mode"]
            edit_form = st.form(prefix, border=False, enter_to_submit=False)
            with edit_form:
                item = deepcopy(old)
                # The header placeholders are outside the form's visual block.
                # Give them its form metadata so edits stay batched until Save.
                name_slot._form_data = edit_form._form_data
                date_slot._form_data = edit_form._form_data
                with name_slot.container():
                    item["name"] = st.text_input("Nome", value=old["name"], max_chars=80)
                with date_slot.container():
                    if kind == "auto":
                        item["next_date"] = _date_input("Scadenza", old.get("next_date") or car_deadline(old), prefix+"next")
                    elif kind == "installments":
                        item["start_date"] = _date_input("Prima rata", old.get("start_date"), prefix+"start")
                    elif mode == "monthly":
                        item["contribution_start"] = _date_input("Primo versamento", old.get("contribution_start"), prefix+"contribution_start")
                    elif mode == "annual":
                        item["redemption_date"] = _date_input("Ritiro previsto", old.get("redemption_date"), prefix+"redemption")
                    else:
                        item["value_date"] = _date_input("Valore al", old.get("value_date"), prefix+"value_date")
                paid_today = None
                if kind == "auto":
                    estimate = car_history.forecast(old)
                    cost, interval, reminder = st.columns(3, gap="small")
                    with cost:
                        planned_input = st.number_input("Costo previsto (€)", min_value=0., value=estimate['amount'], step=10., help="Se vuoto, uso l’ultimo pagamento. Registra il costo reale nello storico.")
                    item['planned_amount'] = None if old.get('planned_amount') is None and planned_input == estimate['amount'] else planned_input
                    with interval:
                        item["interval_months"] = st.number_input("Ogni quanti mesi", min_value=0, max_value=120, value=int(old.get("interval_months", 12)), help="Zero disattiva la ripetizione. Revisione: prima dopo quattro anni, poi ogni 24 mesi.")
                    with reminder:
                        item["reminder_days"] = st.number_input("Preavviso (giorni)", min_value=0, max_value=365, value=int(old.get("reminder_days", 30)))
                elif kind == "investments":
                    paid_today = _investment_fields(item, old, prefix, mode, today)
                else:
                    amount_col, count_col, day_col = st.columns(3, gap="small")
                    with amount_col:
                        item["amount"] = st.number_input("Rata (€)", min_value=0., value=old.get("amount"), step=10.)
                    with count_col:
                        item["count"] = st.number_input("Numero rate", min_value=1, max_value=1200, value=old.get("count"), help="In alternativa, imposta la data dell’ultima rata.")
                    with day_col:
                        item["due_day"] = st.number_input("Giorno pagamento", min_value=1, max_value=31, value=old.get("due_day"), help="Vuoto = giorno della prima rata. Nei mesi più corti si usa l’ultimo giorno.")
                    end_col, correction_col = st.columns(2, gap="small")
                    with end_col:
                        item["end_date"] = _date_input("Ultima rata (alternativa)", old.get("end_date"), prefix+"end")
                    with correction_col:
                        submit = st.form_submit_button("💾 Salva", type="primary", use_container_width=True)
                        delete = st.form_submit_button("Elimina", disabled=selection == "__new__", use_container_width=True)
                if kind == "auto":
                    table, actions = st.columns([3, 1], gap="small", vertical_alignment="bottom")
                    with table:
                        edited_payments, payment_draft = car_history.payment_fields(old, today)
                    with actions:
                        confirm_payment = st.form_submit_button("✅ Registra pagamento", type="primary", use_container_width=True) if payment_draft else False
                        submit = st.form_submit_button("Salva modifiche" if payment_draft else "💾 Salva", type="primary", use_container_width=True)
                        submit = submit or confirm_payment
                        delete = st.form_submit_button("Elimina", disabled=selection == "__new__", use_container_width=True)
                elif kind == "investments":
                    c1, c2 = st.columns(2, gap="small")
                    with c1:submit = st.form_submit_button("💾 Salva", type="primary", use_container_width=True)
                    with c2:delete = st.form_submit_button("Elimina", disabled=selection == "__new__", use_container_width=True)
            if submit:
                try:
                    item["id"] = old.get("id") or uuid4().hex
                    if kind == "auto":
                        submitted_rows, settle_payment = car_history.submitted_payments(edited_payments, payment_draft, confirm_payment)
                        rows = car_history.validate_payments(submitted_rows, today)
                        if rows != car_history.car_record(old, today)["payments"] or settle_payment:
                            planned_date, planned_amount = item.get("next_date"), item.get("planned_amount")
                            item = car_history.with_payments(item, rows, today, settle=settle_payment)
                            # A single save may settle the old bill and schedule the next one.
                            if (as_date(planned_date) != as_date(old.get("next_date") or car_deadline(old))
                                    and as_date(planned_date) and (not item.get("last_date") or as_date(planned_date) > as_date(item["last_date"]))):
                                item.update(next_date=planned_date, planned_amount=planned_amount)
                    item = validate_record(item, today, paid_today)
                    if save_record(item["id"], item):saved("Voce salvata.")
                except (ValueError, TypeError) as exc:
                    st.error(str(exc))
            if delete and save_record(old["id"], None):
                st.session_state.pop(f"tracker_select_{kind}", None)
                saved("Voce eliminata.")
