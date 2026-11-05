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
    for index, name in enumerate(["Moneyfarm", "Alleanza Assicurazioni", "Cometa", "Leonardo · JP Morgan"]):
        records.append(investment_record(dict(id=f"investment-{index}", kind="investments", name=name,
                            invested=None, current_value=None, withdrawn=0., notes="")))
    for index, name in enumerate(["Mutuo casa", "Assicurazione vita mutuo", "Rata auto", "Finanziamento cucina"]):
        records.append(dict(id=f"instalment-{index}", kind="installments", name=name,
                            start_date=None, end_date=None, count=None, due_day=None, amount=None, paid_offset=0, notes=""))
    return records


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
    return result


def encode_rows(records):
    rows = [{"ID": "__settings__", "Sezione": "settings", "Nome": "Registro personale", "Dati": '{"version":1}'}]
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
    for field in ("last_date", "next_date", "start_date", "end_date"):
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
    explicit = as_date(record.get("next_date"))
    last = as_date(record.get("last_date"))
    months = int(record.get("interval_months", 0))
    return explicit or (month_date(last, months) if last and months else None)


def deadline_status(deadline, today, notice=30):
    if deadline is None:
        return "Da impostare", "#94a3b8"
    days = (deadline-today).days
    if days < 0:
        return f"Scaduta da {-days} giorni", "#f87171"
    if days == 0:
        return "Scade oggi", "#fbbf24"
    return f"Tra {days} giorni", "#fbbf24" if days <= notice else "#34d399"


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
        item["opening_personal"] = (item.get("opening_personal") or 0.)+(item.get("opening_company") or 0.)
        item["opening_company"] = 0.
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


def validate_investment(record, today, paid_today=None):
    item = investment_record(record)
    mode = item["contribution_mode"]
    if mode not in {"manual", "monthly", "annual"}:
        raise ValueError("Tipo di versamento non valido.")
    item["product"] = str(item.get("product", ""))[:60]
    for key in ("monthly_personal", "monthly_company", "opening_personal", "opening_company", "opening_unallocated"):
        item[key] = _number(item.get(key))
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
            rows.append(dict(year=year, date=due.isoformat() if due else None,
                             personal=_number(row.get("personal")), company=_number(row.get("company")),
                             source=str(row.get("source", ""))[:120], confirmed=confirmed))
        item["annual_contributions"] = sorted(rows, key=lambda row: row["year"])
        if item.get("redemption_year") and seen and max(seen) > item["redemption_year"]:
            raise ValueError("Il ritiro previsto precede un anno di versamento.")
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


def date_label(value):
    day = as_date(value)
    return day.strftime("%d/%m/%Y") if day else "—"


def _card(item, content):
    notes = '<div class="tracker-note">'+escape(item.get("notes", ""))+'</div>' if item.get("notes") else ""
    return '<div class="tracker-card"><strong>'+escape(item["name"])+"</strong>"+content+notes+'</div>'


def cards_html(records, kind, today):
    cards = []
    for item in records:
        if item["kind"] != kind:
            continue
        if kind == "auto":
            due = car_deadline(item)
            status, color = deadline_status(due, today, item.get("reminder_days", 30))
            content = f'<div class="tracker-value">{date_label(due)}</div><div style="color:{color};">{status}</div>'
            content += f'<div class="tracker-note">Ultima volta: {date_label(item.get("last_date"))}<br>Importo: {money(item.get("amount"))}</div>'
            if item.get("planned_amount") is not None and item.get("next_date"):
                planned_color = "#fb923c" if as_date(item["next_date"]) > today else car_history.card_color(item)
                planned_label = "Costo previsto" if as_date(item["next_date"]) > today else "Costo da confermare"
                content += f'<div class="tracker-note" style="color:{planned_color}">{planned_label}: {money(item["planned_amount"])}</div>'
            content += car_history.chart_html(item, today)
        elif kind == "investments":
            item = investment_record(item)
            summary = investment_summary(item, today)
            gain = summary["gain"]
            content = f'<div class="tracker-note">{escape(item["product"])} · versato</div><div class="tracker-value">{money(summary["total"])}</div>'
            if summary["mode"] != "manual":
                content += f'<div class="tracker-note">Tu: {money(summary["personal"])}' + (f'<br>Azienda: {money(summary["company"])}' if not item.get("personal_only") else '') + '</div>'
                if item.get("name", "").casefold() == "cometa":
                    content += f'<div class="tracker-note">TFR: {money(summary["tfr"])}</div>'
                    if summary["tfr_incomplete"]:
                        content += '<div class="tracker-note">Totale parziale · TFR da completare con Sincronizza Drive</div>'
                if summary["unallocated"]:
                    content += f'<div class="tracker-note">Precedenti da ripartire: {money(summary["unallocated"])}</div>'
            if summary["mode"] == "monthly":
                monthly = (item.get("monthly_personal") or 0.)+(item.get("monthly_company") or 0.)+(item.get("monthly_tfr") or 0.)
                breakdown = '' if item.get("personal_only") else f'<br>Tu {money(item.get("monthly_personal"))} · azienda {money(item.get("monthly_company"))}'
                if item.get("name", "").casefold() == "cometa":
                    breakdown += f'<br>TFR {money(item.get("monthly_tfr"))}'
                content += f'<div class="tracker-note">Ogni mese: {money(monthly)}{breakdown}<br>{summary["count"]} versamenti conteggiati</div>'
                if item.get("monthly_source_month") and item.get("cometa_auto_quotes", True):
                    content += f'<div class="tracker-note">Quote da cedolino: {escape(item["monthly_source_month"])}</div>'
                    if item.get("monthly_tfr_source_month"):
                        content += f'<div class="tracker-note">TFR da cedolino: {escape(item["monthly_tfr_source_month"])}</div>'
                if item.get("contribution_start"):
                    content += f'<div class="tracker-note">Prossimo: {date_label(summary["next_due"])}' + (f'<br>Fine versamenti: {date_label(item["contribution_end"])}' if item.get("contribution_end") else '<br>Accumulo senza scadenza') + '</div>'
                elif item.get("_pension_history") and (summary["unallocated"] or item.get("opening_personal") or item.get("opening_company") or item.get("opening_tfr")):
                    content += '<div class="tracker-note">Imposta la data iniziale per aggiornare il saldo precedente</div>'
                elif not item.get("_pension_history"):
                    content += '<div class="tracker-note">Imposta la data del primo versamento</div>'
            elif summary["mode"] == "annual":
                for row in summary["schedule"]:
                    amount = (row.get("personal") or 0.)+(row.get("company") or 0.)
                    label = ("conteggiato" if row["counted"] else "data da impostare" if not row.get("date")
                             else "importo da impostare" if row.get("personal") is None and row.get("company") is None else "previsto")
                    content += f'<div class="tracker-note">{row["year"]}: {money(amount) if row.get("personal") is not None or row.get("company") is not None else "—"} · {label}<br>Tu {money(row.get("personal"))} · azienda {money(row.get("company"))}</div>'
                    if row.get("source"):
                        content += f'<div class="tracker-note">Fonte: {escape(row["source"])}</div>'
                redemption = date_label(item["redemption_date"]) if item.get("redemption_date") else str(item.get("redemption_year") or "Da impostare")
                content += f'<div class="tracker-note">Ritiro previsto: {redemption}</div>'
            content += f'<div>Valore attuale: {money(item.get("current_value"))}</div>'
            if item.get("value_date"):
                content += f'<div class="tracker-note">Aggiornato al {date_label(item["value_date"])}</div>'
            if gain is not None:
                content += f'<div style="color:{"#34d399" if gain >= 0 else "#f87171"};">Risultato: {money(gain)}</div>'
            if item.get("withdrawn"):
                content += f'<div class="tracker-note">Prelevato: {money(item["withdrawn"])}</div>'
        else:
            summary = instalment_summary(item, today)
            if summary is None:
                content = '<div class="tracker-note">Imposta prima rata e durata</div>'
            else:
                pct = summary["progress"]*100
                content = f'<div class="tracker-value">{summary["paid"]} / {summary["total"]} rate</div>'
                content += f'<div class="tracker-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="{pct:.1f}"><span style="width:{pct:.2f}%;"></span></div>'
                content += f'<div>Ancora {summary["remaining"]} rate · {money(summary["remaining_amount"])}</div>'
                content += f'<div class="tracker-note">Pagato da calendario: {money(summary["paid_amount"])}<br>Rata: {money(item.get("amount"))}<br>Prossima: {date_label(summary["next_due"])}<br>Fine: {date_label(summary["end"])}</div>'
                if item.get("paid_offset"):
                    content += '<div class="tracker-note">Conteggio corretto manualmente</div>'
        cards.append(_card(item, content))
    return '<div class="tracker-grid">'+"".join(cards)+'</div>'


def _date_input(label, value, key):
    return st.date_input(label, value=as_date(value), min_value=date(1980, 1, 1), max_value=date(2100, 12, 31), format="DD/MM/YYYY", key=key)


def _investment_fields(item, old, prefix, mode, today):
    """All money/date inputs are drafts inside the caller's save form."""
    item["contribution_mode"] = mode
    item["product"] = st.text_input("Tipo di piano", value=old["product"], max_chars=60)
    paid_today = None
    if mode == "monthly":
        if old.get("personal_only"):
            item["monthly_personal"] = st.number_input("Versamento mensile (€)", min_value=0., value=old.get("monthly_personal"), step=10.)
        else:
            c1, c2 = st.columns(2)
            with c1:item["monthly_personal"] = st.number_input("Quota tua mensile (€)", min_value=0., value=old.get("monthly_personal"), step=10., disabled=old.get("cometa_auto_quotes", False))
            with c2:item["monthly_company"] = st.number_input("Quota azienda mensile (€)", min_value=0., value=old.get("monthly_company"), step=10., disabled=old.get("cometa_auto_quotes", False))
        if old.get("name", "").casefold() == "cometa":
            item["monthly_tfr"] = st.number_input("Quota TFR mensile al fondo (€)", min_value=0., value=old.get("monthly_tfr"), step=10., disabled=old.get("cometa_auto_quotes", False))
        item["contribution_start"] = _date_input("Primo versamento da conteggiare", old.get("contribution_start"), prefix+"contribution_start")
        item["contribution_end"] = _date_input("Ultimo versamento, solo se interrompi il piano", old.get("contribution_end"), prefix+"contribution_end")
        st.caption("Il versamento si ripete ogni mese nello stesso giorno; nei mesi più corti, l’ultimo giorno. Lascia vuota la fine per continuare ad accumulare.")
        if not old.get("cometa_auto_quotes", False):
            paid_today = st.number_input("Correggi versamenti già conteggiati, facoltativo", min_value=0, max_value=1500, value=None,
                help="Lascia vuoto per il conteggio automatico. Una correzione resta valida e il conteggio continua nei mesi successivi.")
    elif mode == "annual":
        st.caption("I tre versamenti entrano nel totale alla data indicata. Per quelli futuri puoi già inserire gli importi previsti.")
        rows = old.get("annual_contributions") or [dict(year=year, date=None, personal=None, company=None) for year in (2025, 2026, 2027)]
        item["annual_contributions"] = []
        for index, row in enumerate(rows):
            st.markdown(f"**Versamento {index+1}**")
            c1, c2 = st.columns(2)
            with c1:year = st.number_input("Anno", min_value=1980, max_value=2100, value=int(row["year"]), key=prefix+f"year{index}")
            with c2:due = _date_input("Data versamento", row.get("date"), prefix+f"annual_date{index}")
            c1, c2 = st.columns(2)
            with c1:personal = st.number_input("Tu · premio di produzione (€)", min_value=0., value=row.get("personal"), step=100., key=prefix+f"personal{index}")
            with c2:company = st.number_input("Quota azienda (€)", min_value=0., value=row.get("company"), step=100., key=prefix+f"company{index}")
            source = st.text_input("Provenienza", value=row.get("source", ""), max_chars=120, key=prefix+f"source{index}")
            confirmed = st.checkbox("Versamento già effettuato", value=bool(row.get("confirmed", False)), key=prefix+f"confirmed{index}",
                help="Spuntalo se hai già versato ma non ricordi la data esatta. Altrimenti il totale segue la data indicata.")
            item["annual_contributions"].append(dict(year=year, date=due, personal=personal, company=company, source=source, confirmed=confirmed))
        item["redemption_year"] = st.number_input("Anno del ritiro previsto", min_value=1980, max_value=2100, value=old.get("redemption_year") or 2028)
        item["redemption_date"] = _date_input("Data del ritiro, se già nota", old.get("redemption_date"), prefix+"redemption")
    if mode == "manual":
        baseline = old.get("invested") if old["contribution_mode"] == "manual" else investment_summary(old, today)["total"]
        item["invested"] = st.number_input("Totale versato (€)", min_value=0., value=baseline, step=100.)
    else:
        st.markdown("**Versato prima del conteggio**")
        st.caption("Inserisci qui solo gli importi precedenti alla prima data conteggiata, per non contarli due volte.")
        if old.get("personal_only"):
            item["opening_personal"] = st.number_input("Già versato da te (€)", min_value=0., value=float(old.get("opening_personal") or 0.), step=100.)
        else:
            c1, c2 = st.columns(2)
            with c1:item["opening_personal"] = st.number_input("Già versato da te (€)", min_value=0., value=float(old.get("opening_personal") or 0.), step=100.)
            with c2:item["opening_company"] = st.number_input("Già versato dall’azienda (€)", min_value=0., value=float(old.get("opening_company") or 0.), step=100.)
        if old.get("name", "").casefold() == "cometa":
            item["opening_tfr"] = st.number_input("TFR già versato prima del conteggio (€)", min_value=0., value=float(old.get("opening_tfr") or 0.), step=100.)
        if old.get("opening_unallocated"):
            allocation_help = ("È il totale che avevi già salvato. Spostalo in ‘Già versato da te’ e azzera questo campo, oppure tienilo qui senza duplicarlo."
                               if old.get("personal_only") else "È il totale che avevi già salvato. Ripartiscilo fra le due quote qui sopra e azzera questo campo, oppure tienilo qui senza duplicarlo.")
            item["opening_unallocated"] = st.number_input("Versamenti precedenti da ripartire (€)", min_value=0., value=float(old["opening_unallocated"]), step=100.,
                help=allocation_help)
    item["current_value"] = st.number_input("Valore attuale (€), facoltativo", min_value=0., value=old.get("current_value"), step=100.)
    if item["current_value"] != old.get("current_value"):
        item["value_date"] = today.isoformat() if item["current_value"] is not None else None
    item["withdrawn"] = st.number_input("Totale prelevato (€)", min_value=0., value=float(old.get("withdrawn") or 0.), step=100.)
    return paid_today


@st.fragment
def render_tracker(records, kind, save_record, saved, today):
    with st.container(key=f"personal-tracker-{kind}"):
        title = {"auto": "🚗 Auto · scadenze e manutenzione", "investments": "📈 Investimenti", "installments": "🗓️ Rate e finanziamenti"}[kind]
        st.subheader(title)
        if records is None:
            st.info("Registro non disponibile. Riprova il caricamento prima di modificare i dati.")
            return
        items = [investment_record(item) if kind == "investments" else car_history.car_record(item, today) if kind == "auto" else item for item in records if item["kind"] == kind]
        if kind == "auto":
            upcoming = sorted((car_deadline(item), item["name"]) for item in items if car_deadline(item) is not None)
            if upcoming:
                st.caption(f"Prima scadenza: {upcoming[0][1]} · {date_label(upcoming[0][0])}. Gli avvisi compaiono qui nell’app.")
        elif kind == "investments":
            summaries = [investment_summary(item, today) for item in items]
            totals = [row["total"] for row in summaries if row["total"] is not None]
            st.caption(f"Versato complessivo: {money(sum(totals)) if totals else '—'} · quote mensili e annuali conteggiate alle date previste")
        else:
            summaries = [instalment_summary(item, today) for item in items]
            remaining = [row["remaining_amount"] for row in summaries if row and row["remaining_amount"] is not None]
            st.caption(f"Rate residue complessive: {money(sum(remaining)) if remaining else '—'} · conteggio automatico alle date previste")
        st.html("""<style>
          [class*="st-key-personal-tracker-"] [data-testid="stCaptionContainer"] {color:#94a3b8;}
          [class*="st-key-personal-tracker-"] [data-testid="stWidgetLabel"] p {white-space:normal!important;font-size:11px!important;color:#cbd5e1!important;}
          .tracker-grid {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;}
          .tracker-card {min-width:0;padding:12px;border:.5px solid rgba(255,255,255,.12);border-radius:14px;background:rgba(255,255,255,.05);font-size:12px;color:#cbd5e1;overflow-wrap:anywhere;}
          .tracker-card>strong {display:block;color:#e2e8f0;font-size:13px;margin-bottom:7px;}
          .tracker-value {font-family:"DM Mono",monospace;color:#60a5fa;font-size:17px;margin:5px 0 8px;}
          .tracker-note {font-size:11px;color:#94a3b8;line-height:1.5;margin-top:7px;white-space:pre-line;}
          .car-mini-history {margin-top:12px;padding-top:8px;border-top:1px solid rgba(255,255,255,.08);}
          .car-history-heading {font-size:11px;color:#94a3b8;}
          .car-history-latest {display:flex;justify-content:space-between;gap:5px;font-size:11px;color:#94a3b8;margin-top:3px;}
          .car-history-empty {min-height:110px;margin-top:12px;padding-top:8px;border-top:1px solid rgba(255,255,255,.08);font-size:11px;color:#94a3b8;}
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
            selection = st.selectbox("Voce", identifiers, format_func=lambda key: "＋ Nuova voce" if key == "__new__" else lookup[key]["name"], key=f"tracker_select_{kind}")
            old = lookup.get(selection, dict(id="", kind=kind, name="", notes=""))
            prefix = f"tracker_{kind}_{selection}"
            if kind == "investments":
                old = investment_record(old)
                modes = {"monthly": "Versamenti mensili", "annual": "Tre versamenti annuali", "manual": "Totale manuale"}
                mode = st.selectbox("Versamenti", list(modes), index=list(modes).index(old["contribution_mode"]),
                                    format_func=modes.get, key=prefix+"mode")
                if old["contribution_mode"] == "manual" and mode != "manual":
                    old.update(opening_personal=0., opening_company=0., opening_unallocated=old.get("invested") or 0.)
                if old.get("name", "").casefold() == "cometa":
                    old["cometa_auto_quotes"] = st.checkbox("Usa le quote dei cedolini sincronizzati", value=old.get("cometa_auto_quotes", True), key=prefix+"cometa_auto")
            with st.form(prefix, border=False, enter_to_submit=False):
                item = deepcopy(old)
                item["name"] = st.text_input("Nome", value=old["name"], max_chars=80)
                paid_today = None
                if kind == "auto":
                    item["next_date"] = _date_input("Prossima scadenza", old.get("next_date") or car_deadline(old), prefix+"next")
                    item["planned_amount"] = st.number_input("Costo previsto (€)", min_value=0., value=old.get("planned_amount"), step=10.)
                    item["interval_months"] = st.number_input("Ripeti ogni quanti mesi", min_value=0, max_value=120, value=int(old.get("interval_months", 12)), help="Serve a calcolare la scadenza successiva quando registri un pagamento nello storico. Zero disattiva la ripetizione.")
                    item["reminder_days"] = st.number_input("Avvisa quanti giorni prima", min_value=0, max_value=365, value=int(old.get("reminder_days", 30)))
                elif kind == "investments":
                    paid_today = _investment_fields(item, old, prefix, mode, today)
                else:
                    item["amount"] = st.number_input("Importo della rata (€)", min_value=0., value=old.get("amount"), step=10.)
                    item["start_date"] = _date_input("Data della prima rata", old.get("start_date"), prefix+"start")
                    item["count"] = st.number_input("Numero totale di rate, oppure compila la data finale", min_value=1, max_value=1200, value=old.get("count"))
                    item["end_date"] = _date_input("Data ultima rata, alternativa al numero di rate", old.get("end_date"), prefix+"end")
                    item["due_day"] = st.number_input("Giorno del mese in cui paghi", min_value=1, max_value=31, value=old.get("due_day"), help="Vuoto = giorno della prima rata. Nei mesi più corti si usa l'ultimo giorno.")
                    current = instalment_summary(old, today)
                    if current:st.caption(f"Pagate da calendario: {current['paid']} su {current['total']} · fine {date_label(current['end'])}")
                    paid_today = st.number_input("Correggi il numero di rate già pagate, facoltativo", min_value=0, max_value=1200, value=None, help="Lascia vuoto per conservare il conteggio. Dopo una correzione, il contatore continua automaticamente dalle scadenze successive.")
                    st.caption("Gli importi residui sono la somma delle rate previste, inclusi gli eventuali interessi già presenti nella rata.")
                item["notes"] = st.text_area("Note", value=old.get("notes", ""), max_chars=1000, height=75)
                if kind == "auto":
                    edited_payments, settle_payment = car_history.payment_fields(old, today)
                c1, c2 = st.columns(2)
                with c1:submit = st.form_submit_button("💾 Salva", type="primary", use_container_width=True)
                with c2:delete = st.form_submit_button("Elimina voce", disabled=selection == "__new__", use_container_width=True)
            if submit:
                try:
                    item["id"] = old.get("id") or uuid4().hex
                    if kind == "auto":
                        rows = car_history.validate_payments(car_history.payment_rows(edited_payments), today)
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
        if kind != "investments" and items:
            with st.expander("Promemoria nel calendario", expanded=False):
                selected = st.selectbox("Scadenza", items, format_func=lambda item: item["name"], key=f"tracker_calendar_{kind}")
                content = calendar_reminder(selected, today)
                if content:
                    st.download_button("Scarica promemoria (.ics)", content, file_name=f"promemoria-{selected['id']}.ics", mime="text/calendar", on_click="ignore", use_container_width=True)
                    st.caption("Importa il file nel tuo calendario per ricevere l’avviso. L’app non invia notifiche in autonomia.")
                else:st.caption("Imposta prima una scadenza.")
