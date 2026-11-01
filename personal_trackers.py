"""Personal records for deadlines, holdings and monthly instalments.

No banking connection, price lookup or changes to the existing expense budget.
"""
from __future__ import annotations
from calendar import monthrange
from copy import deepcopy
from datetime import date, datetime, timedelta
from html import escape
import json
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
                            last_date=None, next_date=None, interval_months=interval, reminder_days=30, amount=None, notes=""))
    for index, name in enumerate(["Moneyfarm", "Allianz / Alleanza", "Cometa", "Leonardo · JP Morgan"]):
        records.append(dict(id=f"investment-{index}", kind="investments", name=name,
                            invested=None, current_value=None, withdrawn=0., notes=""))
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
        result.append(dict(record, id=identifier, kind=kind, name=name))
    return result


def encode_rows(records):
    rows = [{"ID": "__settings__", "Sezione": "settings", "Nome": "Registro personale", "Dati": '{"version":1}'}]
    for item in records:
        data = {key: value for key, value in item.items() if key not in {"id", "kind", "name"}}
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
    for field in ("amount", "invested", "current_value", "withdrawn"):
        if field in item:
            item[field] = _number(item[field])
    for field in ("last_date", "next_date", "start_date", "end_date"):
        if field in item:
            parsed = as_date(item[field])
            item[field] = parsed.isoformat() if parsed else None
    if item["kind"] == "auto":
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


def holding_result(record):
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
        elif kind == "investments":
            gain = holding_result(item)
            content = f'<div class="tracker-note">Versato</div><div class="tracker-value">{money(item.get("invested"))}</div>'
            content += f'<div>Valore attuale: {money(item.get("current_value"))}</div>'
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


@st.fragment
def render_tracker(records, kind, save_record, saved, today):
    with st.container(key=f"personal-tracker-{kind}"):
        title = {"auto": "🚗 Auto · scadenze e manutenzione", "investments": "📈 Investimenti", "installments": "🗓️ Rate e finanziamenti"}[kind]
        st.subheader(title)
        if records is None:
            st.info("Registro non disponibile. Riprova il caricamento prima di modificare i dati.")
            return
        items = [item for item in records if item["kind"] == kind]
        if kind == "auto":
            upcoming = sorted((car_deadline(item), item["name"]) for item in items if car_deadline(item) is not None)
            if upcoming:
                st.caption(f"Prima scadenza: {upcoming[0][1]} · {date_label(upcoming[0][0])}. Gli avvisi compaiono qui nell’app.")
        elif kind == "investments":
            known = [item for item in items if item.get("invested") is not None]
            st.caption(f"Versato complessivo: {money(sum(item['invested'] for item in known)) if known else '—'} · {len(known)} strumenti compilati")
        else:
            summaries = [instalment_summary(item, today) for item in items]
            remaining = [row["remaining_amount"] for row in summaries if row and row["remaining_amount"] is not None]
            st.caption(f"Rate residue complessive: {money(sum(remaining)) if remaining else '—'} · conteggio automatico alle date previste")
        st.html("""<style>
          [class*="st-key-personal-tracker-"] [data-testid="stCaptionContainer"] {color:#94a3b8;}
          .tracker-grid {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;}
          .tracker-card {min-width:0;padding:12px;border:.5px solid rgba(255,255,255,.12);border-radius:14px;background:rgba(255,255,255,.05);font-size:12px;color:#cbd5e1;overflow-wrap:anywhere;}
          .tracker-card>strong {display:block;color:#e2e8f0;font-size:13px;margin-bottom:7px;}
          .tracker-value {font-family:"DM Mono",monospace;color:#60a5fa;font-size:17px;margin:5px 0 8px;}
          .tracker-note {font-size:11px;color:#94a3b8;line-height:1.5;margin-top:7px;white-space:pre-line;}
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
            with st.form(prefix, border=False, enter_to_submit=False):
                item = deepcopy(old)
                item["name"] = st.text_input("Nome", value=old["name"], max_chars=80)
                paid_today = None
                if kind == "auto":
                    c1, c2 = st.columns(2)
                    with c1:item["last_date"] = _date_input("Ultima volta", old.get("last_date"), prefix+"last")
                    with c2:item["next_date"] = _date_input("Prossima scadenza", old.get("next_date"), prefix+"next")
                    item["interval_months"] = st.number_input("Ripeti ogni quanti mesi", min_value=0, max_value=120, value=int(old.get("interval_months", 12)), help="Se la prossima scadenza è vuota, viene calcolata dall'ultima data. Zero disattiva la ripetizione.")
                    item["reminder_days"] = st.number_input("Avvisa quanti giorni prima", min_value=0, max_value=365, value=int(old.get("reminder_days", 30)))
                    item["amount"] = st.number_input("Importo (€)", min_value=0., value=old.get("amount"), step=10.)
                elif kind == "investments":
                    item["invested"] = st.number_input("Totale versato (€)", min_value=0., value=old.get("invested"), step=100.)
                    item["current_value"] = st.number_input("Valore attuale (€), facoltativo", min_value=0., value=old.get("current_value"), step=100.)
                    item["withdrawn"] = st.number_input("Totale prelevato (€)", min_value=0., value=float(old.get("withdrawn") or 0.), step=100.)
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
                c1, c2 = st.columns(2)
                with c1:submit = st.form_submit_button("💾 Salva", type="primary", use_container_width=True)
                with c2:delete = st.form_submit_button("Elimina voce", disabled=selection == "__new__", use_container_width=True)
            if submit:
                try:
                    item["id"] = old.get("id") or uuid4().hex
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
