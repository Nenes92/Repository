import altair as alt
import streamlit as st
import streamlit.components.v1 as components
import mysql.connector
import pandas as pd
import json
import os
from datetime import datetime, timedelta
import calendar
import time
import io
import html
import urllib.request
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

# Google Sheets imports
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSHEETS_AVAILABLE = True
except ImportError:
    GSHEETS_AVAILABLE = False

# ─── GOOGLE SHEETS CONFIG ───────────────────────────────────────────────────
SHEET_URL = st.secrets["SHEET_URL"]

CREDENTIALS_INFO = {
    "type": st.secrets["gcp_service_account"]["type"],
    "project_id": st.secrets["gcp_service_account"]["project_id"],
    "private_key_id": st.secrets["gcp_service_account"]["private_key_id"],
    "private_key": st.secrets["gcp_service_account"]["private_key"],
    "client_email": st.secrets["gcp_service_account"]["client_email"],
    "client_id": st.secrets["gcp_service_account"]["client_id"],
    "auth_uri": st.secrets["gcp_service_account"]["auth_uri"],
    "token_uri": st.secrets["gcp_service_account"]["token_uri"],
    "auth_provider_x509_cert_url": st.secrets["gcp_service_account"]["auth_provider_x509_cert_url"],
    "client_x509_cert_url": st.secrets["gcp_service_account"]["client_x509_cert_url"],
    "universe_domain": st.secrets["gcp_service_account"]["universe_domain"]
}

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

@st.cache_resource
def get_gsheet_client():
    if not GSHEETS_AVAILABLE:
        return None
    try:
        creds = Credentials.from_service_account_info(CREDENTIALS_INFO, scopes=SCOPES)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        return None

GSHEETS_CACHE_TTL_SECONDS = 1800
GSHEETS_BACKOFF_SECONDS = 90
GSHEETS_BACKOFF_LABEL = "circa 90 secondi"


def _worksheet_cache_key(worksheet_name):
    return f"gsheets_worksheet::{worksheet_name}"


def _gsheets_backoff_until_key():
    return "gsheets_backoff_until"


def _is_quota_error(error):
    text = str(error)
    return "429" in text or "Quota exceeded" in text or "Read requests per minute" in text


def _set_gsheets_backoff():
    st.session_state[_gsheets_backoff_until_key()] = time.time() + GSHEETS_BACKOFF_SECONDS


def _is_gsheets_in_backoff():
    return time.time() < st.session_state.get(_gsheets_backoff_until_key(), 0)


def _show_gsheets_warning_once(message):
    key = f"gsheets_warning::{message}"
    if not st.session_state.get(key):
        st.warning(message)
        st.session_state[key] = True


def get_or_create_worksheet(client, sheet_url, worksheet_name, headers):
    if _is_gsheets_in_backoff():
        return st.session_state.get(_worksheet_cache_key(worksheet_name))
    cached_worksheet = st.session_state.get(_worksheet_cache_key(worksheet_name))
    if cached_worksheet is not None:
        return cached_worksheet
    try:
        spreadsheet = client.open_by_url(sheet_url)
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=20)
            worksheet.append_row(headers)
        st.session_state[_worksheet_cache_key(worksheet_name)] = worksheet
        return worksheet
    except Exception as e:
        if _is_quota_error(e):
            _set_gsheets_backoff()
            _show_gsheets_warning_once(f"Google Sheets ha raggiunto il limite temporaneo di letture. Uso i dati in cache e riprovo tra {GSHEETS_BACKOFF_LABEL}.")
        else:
            st.error(f"Errore connessione Google Sheets: {e}")
        return None


def _gsheets_cache_key(worksheet_name):
    return f"gsheets_cache::{worksheet_name}"


def _copy_df(df):
    return df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()


def _format_gsheet_value(header, value):
    if pd.isna(value):
        return ""
    if header == "Mese" and hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    if header == "Data" and hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _set_gsheets_cache(worksheet_name, df):
    st.session_state[_gsheets_cache_key(worksheet_name)] = {
        "time": time.time(),
        "data": _copy_df(df),
    }


def _get_gsheets_cache(worksheet_name, allow_stale=False):
    cached = st.session_state.get(_gsheets_cache_key(worksheet_name))
    if not cached:
        return None
    is_fresh = (time.time() - cached.get("time", 0)) < GSHEETS_CACHE_TTL_SECONDS
    if is_fresh or allow_stale:
        return _copy_df(cached.get("data"))
    return None


def load_data_gsheets(worksheet_name, headers, force_reload=False):
    if _is_gsheets_in_backoff():
        cached = _get_gsheets_cache(worksheet_name, allow_stale=True)
        return cached if cached is not None else pd.DataFrame(columns=headers)

    if not force_reload:
        cached = _get_gsheets_cache(worksheet_name)
        if cached is not None:
            return cached

    client = get_gsheet_client()
    if not client:
        cached = _get_gsheets_cache(worksheet_name, allow_stale=True)
        return cached if cached is not None else pd.DataFrame(columns=headers)
    try:
        worksheet = get_or_create_worksheet(client, SHEET_URL, worksheet_name, headers)
        if not worksheet:
            cached = _get_gsheets_cache(worksheet_name, allow_stale=True)
            return cached if cached is not None else pd.DataFrame(columns=headers)
        records = worksheet.get_all_records()
        if not records:
            df = pd.DataFrame(columns=headers)
            _set_gsheets_cache(worksheet_name, df)
            return df
        df = pd.DataFrame(records)
        if "Mese" in df.columns:
            df["Mese"] = pd.to_datetime(df["Mese"], errors="coerce")
            df = df.dropna(subset=["Mese"])
            df = df.sort_values(by="Mese").reset_index(drop=True)
        _set_gsheets_cache(worksheet_name, df)
        return df
    except Exception as e:
        cached = _get_gsheets_cache(worksheet_name, allow_stale=True)
        if cached is not None:
            if _is_quota_error(e):
                _set_gsheets_backoff()
                _show_gsheets_warning_once(f"Google Sheets ha raggiunto il limite temporaneo di letture. Uso l'ultima copia caricata in memoria e riprovo tra {GSHEETS_BACKOFF_LABEL}.")
            else:
                st.warning(f"Google Sheets non risponde ora ({worksheet_name}). Uso l'ultima copia caricata in memoria.")
            return cached
        if _is_quota_error(e):
            _set_gsheets_backoff()
            _show_gsheets_warning_once(f"Google Sheets ha raggiunto il limite temporaneo di letture. Alcuni dati saranno vuoti finche la quota si sblocca: attendi {GSHEETS_BACKOFF_LABEL}.")
        else:
            st.error(f"Errore caricamento dati: {e}")
        return pd.DataFrame(columns=headers)

def save_data_gsheets(worksheet_name, headers, data):
    if _is_gsheets_in_backoff():
        _show_gsheets_warning_once(f"Google Sheets e in pausa temporanea per quota letture. Riprova il salvataggio tra {GSHEETS_BACKOFF_LABEL}.")
        return False
    client = get_gsheet_client()
    if not client:
        return False
    try:
        worksheet = get_or_create_worksheet(client, SHEET_URL, worksheet_name, headers)
        if not worksheet:
            return False
        if data is None or data.empty:
            data = pd.DataFrame(columns=headers)
        data = data.copy()
        for h in headers:
            if h not in data.columns:
                data[h] = ""
        data = data[headers]
        rows = [headers]
        for _, row in data.iterrows():
            rows.append([_format_gsheet_value(h, row.get(h, "")) for h in headers])
        worksheet.clear()
        try:
            worksheet.update(values=rows, range_name="A1")
        except TypeError:
            worksheet.update("A1", rows)
        _set_gsheets_cache(worksheet_name, data)
        return True
    except Exception as e:
        if _is_quota_error(e):
            _set_gsheets_backoff()
            _show_gsheets_warning_once(f"Google Sheets ha raggiunto il limite temporaneo. Salvataggio non eseguito, riprova tra {GSHEETS_BACKOFF_LABEL}.")
        else:
            st.error(f"Errore salvataggio: {e}")
        return False
# ─────────────────────────────────────────────────────────────────────────────


st.set_page_config(layout="wide", page_title="Finance Dashboard", page_icon="💎")

# =============================================
# MODERN GLASSMORPHISM UI - CSS INJECTION
# =============================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap');

html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }

.stApp {
    background: linear-gradient(135deg, #0d1117 0%, #161b27 40%, #0d1f35 100%);
    min-height: 100vh;
}

h1 {
    font-size: 2rem !important;
    font-weight: 600 !important;
    background: linear-gradient(90deg, #60a5fa, #a78bfa, #34d399);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    letter-spacing: -0.5px;
    padding-bottom: 0.5rem;
}

h2, h3 {
    font-weight: 500 !important;
    color: rgba(255,255,255,0.85) !important;
    letter-spacing: -0.3px;
}

[data-testid="stNumberInput"] label {
    font-size: 11px !important;
    font-weight: 500 !important;
    color: rgba(255,255,255,0.45) !important;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    margin-bottom: 4px;
}

[data-testid="stNumberInput"] input {
    background: rgba(255, 255, 255, 0.07) !important;
    border: 0.5px solid rgba(255, 255, 255, 0.18) !important;
    border-radius: 10px !important;
    color: rgba(255, 255, 255, 0.92) !important;
    font-family: 'DM Mono', monospace !important;
    font-size: 15px !important;
    font-weight: 500 !important;
    padding: 10px 14px !important;
    transition: all 0.2s ease;
}

[data-testid="stNumberInput"] input:focus {
    border: 0.5px solid rgba(96, 165, 250, 0.55) !important;
    background: rgba(255, 255, 255, 0.10) !important;
    box-shadow: 0 0 0 3px rgba(96, 165, 250, 0.10) !important;
    outline: none !important;
}

[data-testid="stNumberInput"] button {
    background: rgba(255,255,255,0.06) !important;
    border: 0.5px solid rgba(255,255,255,0.12) !important;
    color: rgba(255,255,255,0.6) !important;
    border-radius: 8px !important;
    transition: all 0.2s;
}
[data-testid="stNumberInput"] button:hover {
    background: rgba(255,255,255,0.12) !important;
    color: white !important;
}

[data-testid="stSelectbox"] label {
    font-size: 11px !important;
    font-weight: 500 !important;
    color: rgba(255,255,255,0.45) !important;
    text-transform: uppercase;
    letter-spacing: 0.8px;
}

[data-testid="stSelectbox"] > div > div {
    background: rgba(255, 255, 255, 0.07) !important;
    border: 0.5px solid rgba(255, 255, 255, 0.18) !important;
    border-radius: 10px !important;
    color: rgba(255, 255, 255, 0.9) !important;
    transition: all 0.2s;
}

[data-testid="stSelectbox"] > div > div:hover {
    border-color: rgba(96, 165, 250, 0.4) !important;
    background: rgba(255,255,255,0.10) !important;
}

[data-testid="stButton"] > button {
    background: rgba(96, 165, 250, 0.12) !important;
    border: 0.5px solid rgba(96, 165, 250, 0.35) !important;
    border-radius: 10px !important;
    color: #93c5fd !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    padding: 10px 18px !important;
    transition: all 0.2s ease !important;
    letter-spacing: 0.2px;
}

[data-testid="stButton"] > button:hover {
    background: rgba(96, 165, 250, 0.22) !important;
    border-color: rgba(96, 165, 250, 0.55) !important;
    color: #bfdbfe !important;
    transform: translateY(-1px);
}

[data-testid="stButton"] > button:active {
    transform: translateY(0px) scale(0.98) !important;
}

[data-testid="stDownloadButton"] > button {
    background: rgba(52, 211, 153, 0.10) !important;
    border: 0.5px solid rgba(52, 211, 153, 0.30) !important;
    border-radius: 10px !important;
    color: #6ee7b7 !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    transition: all 0.2s ease !important;
}

[data-testid="stDownloadButton"] > button:hover {
    background: rgba(52, 211, 153, 0.20) !important;
    border-color: rgba(52, 211, 153, 0.50) !important;
    color: #a7f3d0 !important;
}

[data-testid="stDataFrame"] {
    background: rgba(255,255,255,0.03) !important;
    border: 0.5px solid rgba(255,255,255,0.08) !important;
    border-radius: 12px !important;
    overflow: hidden;
}

hr {
    border: none !important;
    border-top: 0.5px solid rgba(255,255,255,0.10) !important;
    margin: 1.5rem 0 !important;
}

[data-testid="stMetric"] {
    background: rgba(255,255,255,0.05);
    border: 0.5px solid rgba(255,255,255,0.10);
    border-radius: 14px;
    padding: 1rem 1.25rem;
}

[data-testid="stMetric"] label {
    font-size: 11px !important;
    color: rgba(255,255,255,0.45) !important;
    text-transform: uppercase;
    letter-spacing: 0.7px;
}

[data-testid="stMetric"] [data-testid="stMetricValue"] {
    font-family: 'DM Mono', monospace !important;
    font-size: 22px !important;
    font-weight: 500 !important;
    color: rgba(255,255,255,0.92) !important;
}

.vega-embed { background: transparent !important; }
.vega-embed canvas, .vega-embed svg { background: transparent !important; }

.section-pill {
    display: inline-block;
    background: rgba(96,165,250,0.12);
    border: 0.5px solid rgba(96,165,250,0.25);
    border-radius: 20px;
    padding: 4px 14px;
    font-size: 11px;
    font-weight: 500;
    color: #93c5fd;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 0.5rem;
}

.kpi-card {
    background: rgba(255,255,255,0.05);
    border: 0.5px solid rgba(255,255,255,0.12);
    border-radius: 14px;
    padding: 1rem 1.25rem;
    margin-bottom: 10px;
}
.kpi-label {
    font-size: 11px;
    color: rgba(255,255,255,0.4);
    text-transform: uppercase;
    letter-spacing: 0.9px;
    margin-bottom: 4px;
}
.kpi-value {
    font-family: 'DM Mono', monospace;
    font-size: 21px;
    font-weight: 500;
}

.salary-input-label {
    font-size: 11px;
    font-weight: 800;
    letter-spacing: .8px;
    text-transform: uppercase;
    color: rgba(255,255,255,.54);
    margin: 0 0 6px;
}

.budget-memory-card {
    background: linear-gradient(135deg, rgba(20,184,166,.12), rgba(96,165,250,.08));
    border: 1px solid rgba(45,212,191,.20);
    border-radius: 14px;
    padding: 12px 14px 10px;
    margin-top: 0;
    min-height: 106px;
}
.budget-memory-title {
    font-size: 12px;
    font-weight: 800;
    letter-spacing: .9px;
    text-transform: uppercase;
    color: #99f6e4;
    margin-bottom: 5px;
}
.budget-memory-row {
    display: flex;
    justify-content: space-between;
    gap: 14px;
    align-items: baseline;
    padding: 6px 0;
    border-top: 1px solid rgba(255,255,255,.08);
}
.budget-memory-row:first-of-type {
    border-top: 0;
}
.budget-memory-label {
    color: rgba(255,255,255,.72);
    font-size: 11px;
    line-height: 1.25;
}
.budget-memory-value {
    color: #fef3c7;
    font-family: 'DM Mono', monospace;
    font-size: 15px;
    font-weight: 800;
    white-space: nowrap;
}
.budget-memory-note {
    color: rgba(255,255,255,.45);
    font-size: 10.5px;
    line-height: 1.35;
    margin-top: 7px;
}

[data-testid="stNumberInput"] input {
    background: linear-gradient(135deg, rgba(30,64,105,.72), rgba(24,31,48,.92)) !important;
    border: 1px solid rgba(96,165,250,.28) !important;
    border-radius: 12px !important;
    color: rgba(255,255,255,.94) !important;
    font-family: 'DM Mono', monospace !important;
    font-weight: 700 !important;
    min-height: 40px !important;
}

[data-testid="stNumberInput"] button {
    background: rgba(15,23,42,.84) !important;
    border-color: rgba(96,165,250,.22) !important;
    color: #bfdbfe !important;
    min-height: 40px !important;
}

::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: rgba(255,255,255,0.02); }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.15); border-radius: 10px; }
::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.25); }
</style>
""", unsafe_allow_html=True)


# Flag per controllare se la configurazione della pagina è già stata impostata
page_config_set = False

def set_page_config():
    pass # Rimuoviamo il contenuto di questa funzione, non è più necessario

# /////  
# Variabili inizializzate
input_stipendio_originale=2350
input_risparmi_mese_precedente=0
input_stipendio_scelto=2350
input_stipendio_percepito = input_stipendio_originale
input_budget_da_stipendio = input_stipendio_scelto
totale_entrate_target_oltre_lo_stipendio= 0.9
budget_mensile_disponibile_ideale = 2615
budget_mensile_disponibile_ideale_precedente = 2515
risparmio_mensile_desiderato = 200

percentuale_limite_da_spendere=0.15
limite_da_spendere=80
max_spese_quotidiane=370
decisione_budget_bollette_mensili=180

emergenze_compleanni=0.15
viaggi=0.07

# ─── MISURE COLONNE DASHBOARD ───────────────────────────────────────────────
# Modifica questi numeri per decidere quanto spazio dare alle varie sezioni.
# Funziona a proporzioni: [1, 2, 1] significa centro largo il doppio dei lati.
LAYOUT_COLONNE = {
    "titolo_dashboard": [1, 2, 1],
    "header_stipendi_note": [0.78, 0.78, 1.3, 2.15],
    "dashboard_principale": [0.92, 2.70, 1.78],  # Spese fisse | Variabili/Entrate | Risparmi/Carte/Turni
    "turni_calendario_riepilogo": [1.55, 0.55],
    "turni_frecce_titolo": [0.16, 0.68, 0.16],
    "centrale_variabili_altre": [1.05, 0.95],
    "spese_fisse_lista": [1, 1],
    "variabili_quote_budget": [1, 1],
    "variabili_kpi_grafico": [1.15, 2.05],
    "altre_entrate_obiettivo": [1.06, 1.04],
    "altre_entrate_kpi_grafico": [1.10, 1.90],
    "destra_risparmi_carte": [1.00, 1.00],
    "risparmi_kpi_grafico": [1.18, 1.12],
    "dettaglio_spese_fisse": [0.07, 0.42, 0.62, 0.90],
    "storico_form_chart": [1, 1, 2],
    "storico_tabella_grafico": [1.3, 3],
    "storico_kpi": [1.3, 1, 1],
    "bollette_form_chart": [1, 1, 2],
    "bollette_tabella_grafico": [1, 3],
    "form_nome_importo": [1.4, 0.8],
    "bottone_salva_note": [3, 1],
}

triangolino_verde_BNL = '<span style="display:inline-block; width:0; height:0; border-top:5px solid transparent; border-bottom:5px solid transparent; border-right:5px solid green; margin-left:10px;"></span>'
triangolino_arancione_ING = '<span style="display:inline-block; width:0; height:0; border-top:5px solid transparent; border-bottom:5px solid transparent; border-right:5px solid #D2691E; margin-left:10px;"></span>'
triangolino_blu_Revolut = '<span style="display:inline-block; width:0; height:0; border-top:5px solid transparent; border-bottom:5px solid transparent; border-right:5px solid #89CFF0; margin-left:10px;"></span>'
# /////  

SPESE = {
    "Fisse": {
        "Mutuo": 435,
        "Bollette": decisione_budget_bollette_mensili,
        "Condominio": 45,
        "Altro": 0,
        "Cucina": 0, #315,
        "Pulizia Casa": 40,
        "MoneyFarm - PAC 5": 100,
        "Alleanza - PAC": 100,
        "Macchina": 180,
        "Trasporti": 165,
        "Sport": 70,
        "Psicologo": 100,
        "Cane": 135,
        "World Food Programme": 30,
        "Beneficienza": 10,
        "Netflix": 8.5,
        "Spotify": 3.5,
        "Disney+": 4,
        "BNL C.C.": 7.4,
        "ING C.C.": 2
    },
    "Variabili": {
        "Emergenze/Compleanni": emergenze_compleanni,
        "Viaggi": viaggi,
        "Da spendere": percentuale_limite_da_spendere,
        "Spese quotidiane": 0
    },
    "Revolut": ["Trasporti", "Sport", "Bollette", "Pulizia Casa", "Psicologo", "Cane", "Beneficienza", "Netflix", "Spotify", "Disney+", "Emergenze/Compleanni", "Viaggi", "Da spendere", "Spese quotidiane"],
    "ING": ["Condominio", "Altro", "Cucina", "MoneyFarm - PAC 5", "Alleanza - PAC", "World Food Programme", "Macchina", "ING C.C."],
    "BNL": ["Mutuo", "BNL C.C."],
}

ALTRE_ENTRATE = {
    "Macchina (Mamma)": 100,
    "2° Entr. dal mese prec.": 0,
    "Altro": 0
}

SPESE_FISSE_HEADERS = ["Voce", "Importo", "Categoria", "Carta", "Gruppo"]
SPESE_FISSE_WORKSHEET = "SpeseFisse"
ALTRE_ENTRATE_HEADERS = ["Voce", "Importo"]
ALTRE_ENTRATE_WORKSHEET = "AltreEntrate"

SPESA_FISSA_CATEGORIE = ["Casa", "Investimenti", "Macchina", "Salute", "Donazioni", "Abbonamenti"]
SPESA_FISSA_CATEGORIA_COLORI = {
    "Casa": "#F08080",
    "Investimenti": "#89CFF0",
    "Macchina": "#E6C48C",
    "Salute": "#80E6E6",
    "Donazioni": "#D8BFD8",
    "Abbonamenti": "#CC7722",
}
SPESA_FISSA_CARTE = ["Revolut", "ING", "BNL"]
SPESA_FISSA_CARTA_COLORI = {
    "Revolut": "#89CFF0",
    "ING": "#D2691E",
    "BNL": "green",
}
SPESA_FISSA_GRUPPI_VISIVI = [
    ("Casa", ["Mutuo", "Bollette", "Condominio", "Altro", "Cucina", "Pulizia Casa"]),
    ("Piani e personali", ["MoneyFarm - PAC 5", "Alleanza - PAC", "Cometa", "Macchina", "Psicologo"]),
    ("Abbonamenti", ["Netflix", "Spotify", "Disney+", "BNL C.C.", "ING C.C."]),
    ("Vita e cura", ["World Food Programme", "Beneficienza", "Trasporti", "Sport", "Cane"]),
]
SPESA_FISSA_GRUPPI_BASE = [nome for nome, _ in SPESA_FISSA_GRUPPI_VISIVI]
SPESE_VARIABILI_CARTE = {
    "Revolut": ["Emergenze/Compleanni", "Viaggi", "Da spendere", "Spese quotidiane"],
    "ING": [],
    "BNL": [],
}


def _infer_spesa_fissa_categoria(voce):
    if voce in ["World Food Programme", "Beneficienza"]:
        return "Donazioni"
    if voce in ["MoneyFarm - PAC 5", "Alleanza - PAC", "Cometa"]:
        return "Investimenti"
    if voce in ["Netflix", "Spotify", "Disney+", "BNL C.C.", "ING C.C."]:
        return "Abbonamenti"
    if voce in ["Sport", "Psicologo", "Cane"]:
        return "Salute"
    if voce in ["Trasporti", "Macchina"]:
        return "Macchina"
    return "Casa"


def _infer_spesa_fissa_carta(voce):
    for carta in SPESA_FISSA_CARTE:
        if voce in SPESE.get(carta, []):
            return carta
    return "Revolut"


def _infer_spesa_fissa_gruppo(voce):
    for gruppo, voci in SPESA_FISSA_GRUPPI_VISIVI:
        if voce in voci:
            return gruppo
    return "Casa"


def _spesa_fissa_gruppi_disponibili(metadata=None):
    metadata = metadata or {}
    gruppi = list(SPESA_FISSA_GRUPPI_BASE)
    for meta in metadata.values():
        gruppo = str(meta.get("Gruppo", "")).strip()
        if gruppo and gruppo not in gruppi:
            gruppi.append(gruppo)
    return gruppi


def _ordered_spesa_fissa_groups(settings, metadata):
    gruppi = []
    for gruppo in SPESA_FISSA_GRUPPI_BASE:
        if any(metadata.get(voce, {}).get("Gruppo", _infer_spesa_fissa_gruppo(voce)) == gruppo for voce in settings):
            gruppi.append(gruppo)
    for voce in settings:
        gruppo = metadata.get(voce, {}).get("Gruppo", _infer_spesa_fissa_gruppo(voce))
        if gruppo and gruppo not in gruppi:
            gruppi.append(gruppo)
    return gruppi


def _triangle_for_card(carta):
    colore = SPESA_FISSA_CARTA_COLORI.get(carta, "#89CFF0")
    return (
        '<span style="display:inline-block;width:0;height:0;'
        'border-top:5px solid transparent;border-bottom:5px solid transparent;'
        f'border-right:5px solid {colore};margin-left:10px;"></span>'
    )


def _spesa_fissa_row_html(voce, importo, categoria, carta):
    colore = SPESA_FISSA_CATEGORIA_COLORI.get(categoria, "#ffffff")
    return (
        '<div style="font-size:15px;line-height:1.6;margin:2px 0;">'
        f'<span style="color:{colore};">- {voce}: €{float(importo):.2f}</span>{_triangle_for_card(carta)}'
        '</div>'
    )


def _spesa_variabile_row_html(voce, importo, colore, didascalia):
    return _money_row_html(voce, importo, colore, triangolino_blu_Revolut, didascalia)


def _money_row_html(voce, importo, colore, marker="", didascalia=""):
    valore = pd.to_numeric(importo, errors="coerce")
    importo_float = 0.0 if pd.isna(valore) else float(valore)
    didascalia_html = (
        f'<div style="font-size:12px;color:rgba(255,255,255,.44);margin-left:10px;margin-top:1px;">{didascalia}</div>'
        if didascalia else ""
    )
    return (
        '<div style="margin:4px 0 8px;line-height:1.28;">'
        '<div style="font-size:15px;font-weight:500;">'
        f'<span style="color:{colore};">- {voce}: €{importo_float:.2f}</span>{marker}'
        '</div>'
        f'{didascalia_html}'
        '</div>'
    )


def _history_table_html(df, columns, colors):
    if df.empty:
        return '<div class="kpi-card" style="color:rgba(255,255,255,.62);">Nessun dato storico disponibile.</div>'

    table_rows = []
    for _, row in df.sort_values("Mese", ascending=False).iterrows():
        mese_raw = pd.to_datetime(row.get("Mese"), errors="coerce")
        mese = mese_raw.strftime("%B %Y") if not pd.isna(mese_raw) else str(row.get("Mese", ""))
        values_html = ""
        for col in columns:
            value = pd.to_numeric(row.get(col, 0), errors="coerce")
            value = 0.0 if pd.isna(value) else float(value)
            color = colors.get(col, "#9ca3af")
            values_html += (
                '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;'
                'padding:5px 0;border-top:1px solid rgba(255,255,255,.055);">'
                f'<span style="display:flex;align-items:center;gap:7px;color:rgba(255,255,255,.66);">'
                f'<span style="width:7px;height:7px;border-radius:999px;background:{color};display:inline-block;"></span>'
                f'{html.escape(col)}</span>'
                f'<span style="font-family:DM Mono, monospace;color:{color};font-weight:700;">€{value:,.2f}</span>'
                '</div>'
            )
        table_rows.append(
            '<div style="padding:10px 12px;margin-bottom:8px;border-radius:10px;'
            'background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.075);">'
            f'<div style="font-weight:800;color:rgba(255,255,255,.88);margin-bottom:6px;">{html.escape(mese)}</div>'
            f'{values_html}</div>'
        )

    return (
        '<div style="max-height:360px;overflow-y:auto;padding-right:4px;'
        'scrollbar-color:rgba(148,163,184,.55) transparent;">'
        + "".join(table_rows) +
        '</div>'
    )


def _apply_spese_fisse_settings(settings, metadata):
    SPESE["Fisse"].clear()
    SPESE["Fisse"].update({voce: float(importo) for voce, importo in settings.items()})
    for carta in SPESA_FISSA_CARTE:
        fisse_carta = [voce for voce in settings if metadata.get(voce, {}).get("Carta") == carta]
        SPESE[carta] = fisse_carta + SPESE_VARIABILI_CARTE.get(carta, [])


def _normalize_spese_fisse_df(df):
    if df.empty:
        return pd.DataFrame(columns=SPESE_FISSE_HEADERS)
    for col in SPESE_FISSE_HEADERS:
        if col not in df.columns:
            df[col] = ""
    df = df[SPESE_FISSE_HEADERS].copy()
    df["Voce"] = df["Voce"].astype(str).replace({"Altro/C": "Cane"})
    df["Importo"] = pd.to_numeric(df["Importo"], errors="coerce").fillna(0.0)
    df["Categoria"] = df.apply(
        lambda row: row["Categoria"] if row["Categoria"] in SPESA_FISSA_CATEGORIE else _infer_spesa_fissa_categoria(row["Voce"]),
        axis=1
    )
    df["Carta"] = df.apply(
        lambda row: row["Carta"] if row["Carta"] in SPESA_FISSA_CARTE else _infer_spesa_fissa_carta(row["Voce"]),
        axis=1
    )
    df["Gruppo"] = df.apply(
        lambda row: str(row["Gruppo"]).strip() if str(row["Gruppo"]).strip() else _infer_spesa_fissa_gruppo(row["Voce"]),
        axis=1
    )
    return df


def load_spese_fisse_settings():
    if "spese_fisse_settings" not in st.session_state:
        df = _normalize_spese_fisse_df(load_data_gsheets(SPESE_FISSE_WORKSHEET, SPESE_FISSE_HEADERS))
        if df.empty:
            settings = SPESE["Fisse"].copy()
            metadata = {
                voce: {
                    "Categoria": _infer_spesa_fissa_categoria(voce),
                    "Carta": _infer_spesa_fissa_carta(voce),
                    "Gruppo": _infer_spesa_fissa_gruppo(voce),
                }
                for voce in settings
            }
        else:
            settings = {}
            metadata = {}
        for _, row in df.iterrows():
            voce = row["Voce"]
            if voce:
                settings[voce] = float(row["Importo"])
                metadata[voce] = {"Categoria": row["Categoria"], "Carta": row["Carta"], "Gruppo": row["Gruppo"]}
        st.session_state.spese_fisse_settings = settings
        st.session_state.spese_fisse_metadata = metadata
    if "spese_fisse_metadata" not in st.session_state:
        st.session_state.spese_fisse_metadata = {
            voce: {
                "Categoria": _infer_spesa_fissa_categoria(voce),
                "Carta": _infer_spesa_fissa_carta(voce),
                "Gruppo": _infer_spesa_fissa_gruppo(voce),
            }
            for voce in st.session_state.spese_fisse_settings
        }
    _apply_spese_fisse_settings(st.session_state.spese_fisse_settings, st.session_state.spese_fisse_metadata)


def save_spese_fisse_settings(settings, metadata=None):
    metadata = metadata or st.session_state.get("spese_fisse_metadata", {})
    rows = []
    cleaned_settings = {}
    cleaned_metadata = {}
    for voce, importo in settings.items():
        voce = str(voce).strip()
        if not voce:
            continue
        cleaned_settings[voce] = float(importo)
        row_meta = metadata.get(voce, {})
        categoria = row_meta.get("Categoria") if row_meta.get("Categoria") in SPESA_FISSA_CATEGORIE else _infer_spesa_fissa_categoria(voce)
        carta = row_meta.get("Carta") if row_meta.get("Carta") in SPESA_FISSA_CARTE else _infer_spesa_fissa_carta(voce)
        gruppo = str(row_meta.get("Gruppo", "")).strip() or _infer_spesa_fissa_gruppo(voce)
        cleaned_metadata[voce] = {"Categoria": categoria, "Carta": carta, "Gruppo": gruppo}
        rows.append({"Voce": voce, "Importo": float(importo), "Categoria": categoria, "Carta": carta, "Gruppo": gruppo})
    df = pd.DataFrame(rows)
    ok = save_data_gsheets(SPESE_FISSE_WORKSHEET, SPESE_FISSE_HEADERS, df)
    if ok:
        st.session_state.spese_fisse_settings = cleaned_settings.copy()
        st.session_state.spese_fisse_metadata = cleaned_metadata.copy()
        _apply_spese_fisse_settings(cleaned_settings, cleaned_metadata)
    return ok


def _normalize_voce_importo_df(df, headers):
    if df.empty:
        return pd.DataFrame(columns=headers)
    for col in headers:
        if col not in df.columns:
            df[col] = ""
    df = df[headers].copy()
    df["Voce"] = df["Voce"].astype(str)
    df["Importo"] = pd.to_numeric(df["Importo"], errors="coerce").fillna(0.0)
    return df


def load_altre_entrate_settings():
    if "altre_entrate_settings" not in st.session_state:
        df = _normalize_voce_importo_df(load_data_gsheets(ALTRE_ENTRATE_WORKSHEET, ALTRE_ENTRATE_HEADERS), ALTRE_ENTRATE_HEADERS)
        settings = ALTRE_ENTRATE.copy() if df.empty else {}
        for _, row in df.iterrows():
            voce = row["Voce"]
            if voce:
                settings[voce] = float(row["Importo"])
        st.session_state.altre_entrate_settings = settings
    ALTRE_ENTRATE.clear()
    ALTRE_ENTRATE.update(st.session_state.altre_entrate_settings)


def save_altre_entrate_settings(settings):
    cleaned = {str(voce).strip(): float(importo) for voce, importo in settings.items() if str(voce).strip()}
    df = pd.DataFrame([{"Voce": voce, "Importo": importo} for voce, importo in cleaned.items()])
    ok = save_data_gsheets(ALTRE_ENTRATE_WORKSHEET, ALTRE_ENTRATE_HEADERS, df)
    if ok:
        st.session_state.altre_entrate_settings = cleaned.copy()
        ALTRE_ENTRATE.clear()
        ALTRE_ENTRATE.update(cleaned)
    return ok


def calcola_target_budget_dinamico(spese_fisse_totali):
    quota_fissa_variabili = emergenze_compleanni + viaggi
    base_dopo_quote = max(0, 1 - quota_fissa_variabili)
    coeff_da_spendere = percentuale_limite_da_spendere * base_dopo_quote
    coeff_spese_quotidiane = base_dopo_quote * (1 - percentuale_limite_da_spendere)

    soglie = []
    if coeff_da_spendere > 0:
        soglie.append(limite_da_spendere / coeff_da_spendere)
    if coeff_spese_quotidiane > 0:
        soglie.append(max_spese_quotidiane / coeff_spese_quotidiane)

    budget_dopo_spese_fisse_target = max(soglie) if soglie else 0
    da_spendere_reale = coeff_da_spendere * budget_dopo_spese_fisse_target
    spese_quotidiane_reali = coeff_spese_quotidiane * budget_dopo_spese_fisse_target
    risparmio_auto_variabili = max(0, da_spendere_reale - limite_da_spendere) + max(0, spese_quotidiane_reali - max_spese_quotidiane)

    return {
        "budget_disponibile_target": spese_fisse_totali + budget_dopo_spese_fisse_target,
        "budget_dopo_spese_fisse_target": budget_dopo_spese_fisse_target,
        "risparmio_auto_variabili": risparmio_auto_variabili,
        "da_spendere_reale": da_spendere_reale,
        "spese_quotidiane_reali": spese_quotidiane_reali,
    }

@st.cache_data
def create_charts(stipendio_scelto, risparmiabili, df_altre_entrate):

    df_fisse = pd.DataFrame.from_dict(SPESE["Fisse"], orient="index", columns=["Importo"]).reset_index().rename(columns={"index": "Voce"})
    spese_meta = st.session_state.get("spese_fisse_metadata", {})
    df_fisse["Categoria"] = df_fisse["Voce"].apply(lambda voce: spese_meta.get(voce, {}).get("Categoria", _infer_spesa_fissa_categoria(voce)))
    df_fisse = df_fisse.groupby("Categoria", as_index=False)["Importo"].sum()

    df_variabili = pd.DataFrame.from_dict(SPESE["Variabili"], orient="index", columns=["Importo"]).reset_index().rename(columns={"index": "Categoria"})
    df_altre_entrate = pd.DataFrame.from_dict(ALTRE_ENTRATE, orient="index", columns=["Importo"]).reset_index().rename(columns={"index": "Categoria"})
    df_variabili['Percentuale'] = (df_variabili['Importo'] / risparmiabili).map('{:.2%}'.format)

    totali = [df_fisse["Importo"].sum(), df_variabili["Importo"].sum(), df_altre_entrate["Importo"].sum(), stipendio_scelto]
    categorie = ["Spese Fisse", "Spese Variabili", "Altre Entrate", "Budget da Stipendio"]
    df_totali = pd.DataFrame({"Totale": totali, "Categoria": categorie})

    color_map = {
        "Mutuo": "#CD5C5C",
        "Bollette": "#CD5C5C",
        "Condominio": "#CD5C5C",
        "Altro": "#CD5C5C",
        "Cucina": "#CD5C5C",
        "Pulizia Casa": "#CD5C5C",
        "MoneyFarm - PAC 5": "#6495ED",
        "Alleanza - PAC": "#6495ED",
        "Macchina": "#D2B48C",
        "Trasporti": "#D2B48C",
        "Sport": "#40E0D0",
        "Psicologo": "#40E0D0",
        "Cane": "#40E0D0",
        "World Food Programme": "#B57EDC",
        "Beneficienza": "#B57EDC",
        "Netflix": "#D2691E",
        "Spotify": "#D2691E",
        "Disney+": "#D2691E",
        "BNL C.C.": "#D2691E",
        "ING C.C.": "#D2691E",
        "Emergenze/Compleanni": "#4ADE80",
        "Viaggi": "#166534", 
        "Da spendere": "#FACC15", 
        "Spese quotidiane": "#FB923C",
        "Macchina (Mamma)": "#D2B48C",
        "2° Entr. dal mese prec.": "#D8BFD8",
        "Stipendio Percepito": "#5792E8",
        "Budget Mensile": "#6CBCD0",
        "Altre Entrate": "#77DD77",
        "Spese Fisse": "#FF6961",
        "Spese Variabili": "#FFFF99",
        "Risparmi": "#A2E88A",
    }

    color_map["Donazioni"] = "#B57EDC"
    color_map["Investimenti"] = "#6495ED"
    color_map["Abbonamenti"] = "#D2691E"
    color_map["Salute"] = "#40E0D0"
    color_map["Macchina"] = "#D2B48C"
    color_map["Casa"] = "#CD5C5C"

    df_fisse['Percentuale'] = (df_fisse['Importo'] / stipendio_scelto).map('{:.2%}'.format)

    # FIX 3: Donut labels outside with connector lines for Spese Fisse
    categorie_presenti = df_fisse["Categoria"].unique()    
    chart_fisse = alt.Chart(df_fisse).mark_arc(
        innerRadius=40, outerRadius=70
    ).encode(
        theta=alt.Theta(field="Importo", type="quantitative"),
        color=alt.Color(
            field="Categoria",
            type="nominal",
            scale=alt.Scale(
                domain=categorie_presenti,
                range=[color_map.get(c, "#999999") for c in categorie_presenti]
            ),
            legend=alt.Legend(
                title=None,
                orient='right',
                direction='vertical',
                columns=1,
                labelColor='rgba(255,255,255,0.85)',
                labelFontSize=11,
                symbolSize=40,
                padding=2,
                offset=5
            )
        ),
        tooltip=[
            "Categoria",
            "Importo",
            alt.Tooltip(field="Percentuale", title="Percentuale")
        ]
    ).properties(
        title="🏠 Distribuzione Spese Fisse",
        width=200,
        height=220
    ).configure_title(
        anchor='middle'
    ).configure_view(
        strokeWidth=0,
        fill='transparent'
    )

    # FIX 3: Donut labels outside with connector lines for Spese Variabili
    variabili_color_scale = alt.Scale(
        domain=['Emergenze/Compleanni', 'Viaggi', 'Da spendere', 'Spese quotidiane'],
        range=['#4ADE80', '#166534', '#FACC15', '#FB923C']
    )
    chart_variabili_arc = alt.Chart(df_variabili, title='Distribuzione Spese Variabili').mark_arc(
        outerRadius=100, innerRadius=40
    ).encode(
        theta=alt.Theta(field="Importo", type="quantitative"),
        color=alt.Color(field="Categoria", type="nominal", scale=variabili_color_scale, legend=alt.Legend(
            title=None,
            orient='right',
            direction='vertical',
            labelColor='rgba(255,255,255,0.85)',
            labelFontSize=12,
            symbolType='circle',
            symbolSize=100,
            padding=6
        )),
        tooltip=["Categoria", "Importo", alt.Tooltip(field="Percentuale", title="Percentuale")]
    )
    chart_variabili = chart_variabili_arc.properties(title='💸 Distribuzione Spese Variabili', width=160, height=160).interactive()
    df_altre_entrate['Percentuale'] = (df_altre_entrate['Importo'] / stipendio_scelto).map('{:.2%}'.format)

    # Altre Entrate donut — no legend, tooltip only
    df_altre_entrate_chart = df_altre_entrate[df_altre_entrate["Importo"] > 0].copy()
    if df_altre_entrate_chart.empty:
        df_altre_entrate_chart = df_altre_entrate.copy()

    ae_cats = df_altre_entrate_chart["Categoria"].tolist()
    ae_colors_map = {
        "Macchina (Mamma)": "#D2B48C",
        "2° Entr. dal mese prec.": "#D8BFD8",
        "Altro": "#89CFF0",
    }
    ae_domains = ae_cats
    ae_ranges = [ae_colors_map.get(c, "#888888") for c in ae_cats]

    ae_arc = alt.Chart(df_altre_entrate_chart).mark_arc(outerRadius=80, innerRadius=30).encode(
        theta=alt.Theta(field="Importo", type="quantitative"),
        color=alt.Color(
            field="Categoria", type="nominal",
            scale=alt.Scale(domain=ae_domains, range=ae_ranges),
            legend=alt.Legend(
                title=None,
                orient='right',
                direction='vertical',
                labelColor='rgba(255,255,255,0.85)',
                labelFontSize=12,
                symbolType='circle',
                symbolSize=100,
                padding=6
            )
        ),
        tooltip=["Categoria", "Importo", "Percentuale"]
    )
    chart_altre_entrate = ae_arc.properties(
        title='➕ Distribuzione Altre Entrate'
    ).interactive()

    return chart_fisse, chart_variabili, chart_altre_entrate, df_fisse, df_variabili, df_altre_entrate, color_map


def color_text(text, color):
    return f'<span style="color:{color}">{text}</span>'






st.markdown("""
<style>
.turni-grid-scroll {
    max-height: 365px;
    overflow-y: auto;
    padding-right: 8px;
}
.turni-compact-row [data-testid="stDateInput"] label,
.turni-compact-row [data-testid="stRadio"] label,
.turni-compact-row [data-testid="stCheckbox"] label {
    font-size: 11px !important;
}
.turni-calendar-wrap [data-testid="stButton"] button {
    min-height: 36px !important;
    padding: 6px 6px !important;
}
.turni-calendar-wrap [data-testid="stButton"] button p {
    white-space: nowrap !important;
    font-size: 14px !important;
    line-height: 1 !important;
    text-align: center !important;
    width: 100%;
    color: #d8dee9 !important;
    font-weight: 650 !important;
}
.turni-calendar-wrap [data-testid="stButton"] button p strong {
    font-size: 21px !important;
    font-weight: 1000 !important;
    letter-spacing: 0 !important;
    filter: saturate(1.8) brightness(1.35);
    text-shadow:
        0 0 1px currentColor,
        0 0 8px currentColor,
        0 0 14px rgba(255,255,255,0.30),
        0 1px 1px rgba(0,0,0,0.75);
}
.turni-card-small {
    background: rgba(255,255,255,0.045);
    border: 0.5px solid rgba(255,255,255,0.10);
    border-left: 5px solid rgba(255,255,255,0.25);
    border-radius: 12px;
    padding: 7px 9px;
    margin-bottom: 6px;
}
.turni-card-small .date {
    font-size: 12px;
    color: rgba(255,255,255,0.58);
}
.turni-card-small .title {
    font-size: 14px;
    font-weight: 600;
    margin-top: 2px;
}
.turni-card-small .meta {
    font-size: 11px;
    color: rgba(255,255,255,0.42);
    margin-top: 3px;
}
.turni-mattina { border-left-color:#60a5fa; }
.turni-pomeriggio { border-left-color:#fb923c; }
.turni-notte { border-left-color:#64748b; }
.turni-ferie { border-left-color:#34d399; }
.turni-riposo { border-left-color:#cbd5e1; }
</style>
""", unsafe_allow_html=True)
# ─── MODULO CONTATORE GUADAGNI TURNI ─────────────────────────────────────────
TURNI_HEADERS = ["Data", "Turno", "Festivo"]
TURNI_WORKSHEET = "TurniGuadagni"
CALENDAR_ICAL_URL = ""
CALENDAR_ICAL_URLS = {
    "Mattina": "https://calendar.google.com/calendar/ical/4581152ea8ed2d32562d91d4e737ef9e0b71ebda1b7984291d81a339c40eaf55%40group.calendar.google.com/private-9299d392e110b4681e0e42d13b4df12e/basic.ics",
    "Pomeriggio": "https://calendar.google.com/calendar/ical/5583372b5741bf9b7015849d7b23349d7151cd2d0763c83144a65071404b7e04%40group.calendar.google.com/private-18967b67ddc0bedbe98b08c2ccd3af9c/basic.ics",
    "Notte": "https://calendar.google.com/calendar/ical/bbe8a74b626dddc4b57dd69d6ab1e0f0760b971d95eb029ef7d525525c113250%40group.calendar.google.com/private-15677dcf429c1ce645b8e78d3687768a/basic.ics",
    "Ferie": "https://calendar.google.com/calendar/ical/c3406a4e631b5c206ccd07c267a9346b089f22a9fd7f4dc0cc7ff24140be54c0%40group.calendar.google.com/private-a8aaf23582ab3d900f656dc389edf856/basic.ics",
}

TURNI_ORARI = {
    "Mattina": ("06:00", "14:00"),
    "Pomeriggio": ("14:00", "22:00"),
    "Notte": ("22:00", "06:00"),
    "Ferie": ("06:00", "14:00"),
    "Riposo": ("00:00", "00:00"),
}

DEFAULT_TURNI_RULES = {
    "paga_oraria": 12.60,
    "quota_fissa_mensile": 0.0,
    "m_p_feriale_pct": 20.0,
    "m_p_festivo_giorno_pct": 50.0,
    "notte_feriale_pct": 50.0,
    "festivo_sera_notte_pct": 60.0,
    "ind_m_p_feriale": 6.0,
    "ind_notte_feriale": 15.0,
    "ind_m_p_festivo": 15.0,
    "ind_notte_festiva": 25.0,
}


def _money_turni(value):
    try:
        return f"€{float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "€0,00"


def _now_italy():
    if ZoneInfo is None:
        return datetime.now()
    return datetime.now(ZoneInfo("Europe/Rome")).replace(tzinfo=None)


def _parse_bool_turni(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ["true", "1", "sì", "si", "yes", "festivo"]


def _normalize_turni_df(df):
    if df.empty:
        return pd.DataFrame(columns=TURNI_HEADERS)
    for col in TURNI_HEADERS:
        if col not in df.columns:
            df[col] = ""
    df = df[TURNI_HEADERS].copy()
    df["Data"] = pd.to_datetime(df["Data"], errors="coerce")
    df = df.dropna(subset=["Data"])
    df["Data"] = df["Data"].dt.strftime("%Y-%m-%d")
    df["Turno"] = df["Turno"].astype(str)
    df["Festivo"] = df["Festivo"].apply(_parse_bool_turni)
    return df.sort_values("Data").reset_index(drop=True)


def load_turni_data(force_reload=False):
    """Carica i turni una sola volta in sessione.
    Così cliccare più giorni nel calendario non fa una read Google Sheets ogni volta.
    """
    if force_reload or "turni_df_draft" not in st.session_state:
        df = load_data_gsheets(TURNI_WORKSHEET, TURNI_HEADERS, force_reload=force_reload)
        st.session_state.turni_df_draft = _normalize_turni_df(df)
        st.session_state.turni_dirty = False
    return st.session_state.turni_df_draft.copy()


def set_turni_draft(df):
    st.session_state.turni_df_draft = _normalize_turni_df(df)
    st.session_state.turni_dirty = True


def color_turni_google_sheet(df):
    """Colora le righe del foglio TurniGuadagni in base al turno.
    Non è indispensabile per il calcolo: se Google limita la formattazione, il salvataggio resta valido.
    """
    client = get_gsheet_client()
    if not client:
        return
    try:
        worksheet = get_or_create_worksheet(client, SHEET_URL, TURNI_WORKSHEET, TURNI_HEADERS)
        if not worksheet:
            return
        formats = [{
            "range": "A1:C1",
            "format": {
            "backgroundColor": {"red": 0.05, "green": 0.10, "blue": 0.16},
            "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True}
            },
        }]
        colors = {
            "Mattina": {"red": 0.18, "green": 0.46, "blue": 0.75},
            "Pomeriggio": {"red": 0.95, "green": 0.52, "blue": 0.22},
            "Notte": {"red": 0.25, "green": 0.28, "blue": 0.34},
            "Ferie": {"red": 0.20, "green": 0.62, "blue": 0.35},
        }
        df_norm = _normalize_turni_df(df)
        for i, row in df_norm.reset_index(drop=True).iterrows():
            turno = str(row.get("Turno", ""))
            color = colors.get(turno, {"red": 1, "green": 1, "blue": 1})
            text_color = {"red": 1, "green": 1, "blue": 1} if turno in ["Notte"] else {"red": 0, "green": 0, "blue": 0}
            formats.append({
                "range": f"A{i+2}:C{i+2}",
                "format": {
                    "backgroundColor": color,
                    "textFormat": {"foregroundColor": text_color}
                },
            })
        if hasattr(worksheet, "batch_format"):
            worksheet.batch_format(formats)
    except Exception:
        # Evita di bloccare l'app se la quota formattazione viene superata.
        pass


def save_turni_data(df):
    if df.empty:
        df_save = pd.DataFrame(columns=TURNI_HEADERS)
    else:
        df_save = _normalize_turni_df(df)
    ok = save_data_gsheets(TURNI_WORKSHEET, TURNI_HEADERS, df_save)
    if ok:
        color_turni_google_sheet(df_save)
        st.session_state.turni_df_draft = df_save.copy()
        st.session_state.turni_dirty = False
    return ok


def get_turni_rules():
    if "turni_rules" not in st.session_state:
        st.session_state.turni_rules = DEFAULT_TURNI_RULES.copy()
    return st.session_state.turni_rules


def _dt_for_turno(data_str, time_str):
    return pd.to_datetime(f"{data_str} {time_str}").to_pydatetime()


def _shift_bounds(data_str, turno):
    start_s, end_s = TURNI_ORARI.get(turno, ("00:00", "00:00"))
    start = _dt_for_turno(data_str, start_s)
    end = _dt_for_turno(data_str, end_s)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _easter_date(year):
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime(year, month, day).date()


def _italian_public_holidays(year):
    fixed = {
        (1, 1),    # Capodanno
        (1, 6),    # Epifania
        (4, 25),   # Liberazione
        (5, 1),    # Festa del lavoro
        (6, 2),    # Festa della Repubblica
        (8, 15),   # Ferragosto
        (11, 1),   # Tutti i Santi
        (12, 8),   # Immacolata
        (12, 25),  # Natale
        (12, 26),  # Santo Stefano
    }
    dates = {datetime(year, month, day).date() for month, day in fixed}
    dates.add(_easter_date(year) + timedelta(days=1))  # Pasquetta
    return dates


def _is_italian_public_holiday(dt_obj):
    return dt_obj.date() in _italian_public_holidays(dt_obj.year)


def _is_festive_at(dt_obj, forced_festivo=False):
    return bool(forced_festivo) or dt_obj.weekday() == 6 or _is_italian_public_holiday(dt_obj)


def _pct_for_turno(turno, dt_obj, forced_festivo, rules):
    festive = _is_festive_at(dt_obj, forced_festivo)
    minutes = dt_obj.hour * 60 + dt_obj.minute
    if turno == "Mattina":
        return rules["m_p_festivo_giorno_pct"] if festive else rules["m_p_feriale_pct"]
    if turno == "Pomeriggio":
        if minutes >= 18 * 60:
            return rules["festivo_sera_notte_pct"] if festive else rules["m_p_feriale_pct"]
        return rules["m_p_festivo_giorno_pct"] if festive else rules["m_p_feriale_pct"]
    if turno == "Notte":
        return rules["festivo_sera_notte_pct"] if festive else rules["notte_feriale_pct"]
    return 0.0


def _allowance_for_turno(data_str, turno, forced_festivo, rules):
    if turno in ["Ferie", "Riposo"]:
        return 0.0
    start, _ = _shift_bounds(data_str, turno)
    festive_at_start = _is_festive_at(start, forced_festivo)
    if turno == "Notte":
        return rules["ind_notte_festiva"] if festive_at_start else rules["ind_notte_feriale"]
    return rules["ind_m_p_festivo"] if festive_at_start else rules["ind_m_p_feriale"]


def _next_rate_checkpoint(now, end):
    checkpoints = []
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= end:
        for hour in (0, 6, 18, 22):
            checkpoint = day + timedelta(hours=hour)
            if now < checkpoint < end:
                checkpoints.append(checkpoint)
        day += timedelta(days=1)
    return min(checkpoints) if checkpoints else None


def compute_turno(data_str, turno, forced_festivo, rules, until=None, only_day=None):
    now = _now_italy() if until is None else until
    paga = float(rules["paga_oraria"])

    if turno == "Riposo":
        return {"total": 0.0, "base": 0.0, "extra": 0.0, "hours": 0.0, "rate_min": 0.0}

    if turno == "Ferie":
        start = _dt_for_turno(data_str, "06:00")
        if only_day is not None and data_str != only_day:
            return {"total": 0.0, "base": 0.0, "extra": 0.0, "hours": 0.0, "rate_min": 0.0}
        if now < start:
            return {"total": 0.0, "base": 0.0, "extra": 0.0, "hours": 0.0, "rate_min": 0.0}
        base = paga * 8
        return {"total": base, "base": base, "extra": 0.0, "hours": 8.0, "rate_min": 0.0}

    start, end = _shift_bounds(data_str, turno)
    effective_end = min(end, now)

    if only_day is not None:
        day_start = _dt_for_turno(only_day, "00:00")
        day_end = day_start + timedelta(days=1)
        start = max(start, day_start)
        effective_end = min(effective_end, day_end)

    if effective_end <= start:
        return {"total": 0.0, "base": 0.0, "extra": 0.0, "hours": 0.0, "rate_min": 0.0}

    base = 0.0
    extra = 0.0
    hours = 0.0
    t = start
    while t < effective_end:
        nxt = min(t + timedelta(minutes=1), effective_end)
        h = (nxt - t).total_seconds() / 3600
        pct = _pct_for_turno(turno, t, forced_festivo, rules)
        base += paga * h
        extra += paga * pct / 100 * h
        hours += h
        t = nxt

    allowance = _allowance_for_turno(data_str, turno, forced_festivo, rules)
    if only_day is not None and data_str != only_day:
        allowance = 0.0
    extra += allowance

    rate_min = 0.0
    current_now = _now_italy()
    if start <= current_now <= end:
        rate_min = paga * (1 + _pct_for_turno(turno, current_now, forced_festivo, rules) / 100) / 60

    return {"total": base + extra, "base": base, "extra": extra, "hours": hours, "rate_min": rate_min}


def _turni_current_prev_months():
    now = _now_italy()
    current = now.strftime("%Y-%m")
    prev = (pd.Timestamp(now.replace(day=1)) - pd.DateOffset(months=1)).strftime("%Y-%m")
    return current, prev


def compute_turni_dashboard(df_turni, rules):
    now = _now_italy()
    today = now.strftime("%Y-%m-%d")
    current_m, prev_m = _turni_current_prev_months()

    live_month = 0.0
    current_base_full = 0.0
    prev_extras = 0.0
    live_today = 0.0
    expected_today = 0.0
    hours_live = 0.0
    rate_min = 0.0
    current_shift = "—"
    current_turno = ""
    current_shift_date = ""
    current_shift_start_date = ""
    current_shift_end = None
    current_rate_change_at = None
    next_shift_start = None
    next_shift_label = "—"
    next_shift_total = 0.0
    last_shift_end = None
    last_shift_label = "—"
    last_shift_total = 0.0
    turno_kpi_label = "Turno — live / totale turno"
    work_days_done = 0
    work_days_total = 0
    ferie_days_total = 0

    for _, row in df_turni.iterrows():
        data = row["Data"]
        turno = row["Turno"]
        festivo = bool(row["Festivo"])
        has_turno = turno in TURNI_ORARI and turno != ""

        if has_turno and turno == "Ferie" and data[:7] == current_m:
            ferie_days_total += 1

        if has_turno and turno not in ["Ferie", "Riposo"] and data[:7] == current_m:
            work_days_total += 1
            start_day, _ = _shift_bounds(data, turno)
            if start_day <= now:
                work_days_done += 1

        if has_turno and data[:7] == current_m:
            calc_live = compute_turno(data, turno, festivo, rules, until=now)
            live_month += calc_live["total"]
            hours_live += calc_live["hours"]
            calc_full = compute_turno(data, turno, festivo, rules, until=datetime.max.replace(tzinfo=None))
            current_base_full += calc_full["base"]
            start, end = _shift_bounds(data, turno)
            if turno not in ["Ferie", "Riposo"] and start <= now < end:
                rate_min = calc_live["rate_min"]
                current_shift = f"{turno} {start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
                current_turno = turno
                current_shift_date = _turni_short_date_label(start)
                current_shift_start_date = data
                current_shift_end = end
                current_rate_change_at = _next_rate_checkpoint(now, end)
                live_today = calc_live["total"]
                expected_today = compute_turno(data, turno, festivo, rules, until=datetime.max.replace(tzinfo=None))["total"]

        if has_turno and data[:7] == prev_m:
            calc_prev = compute_turno(data, turno, festivo, rules, until=datetime.max.replace(tzinfo=None))
            prev_extras += calc_prev["extra"]

        if not has_turno:
            continue
        start, end = _shift_bounds(data, turno)
        if turno not in ["Ferie", "Riposo"] and start > now and (next_shift_start is None or start < next_shift_start):
            next_shift_start = start
            next_shift_label = f"{turno} {start.strftime('%d/%m %H:%M')}"
            next_shift_total = compute_turno(data, turno, festivo, rules, until=datetime.max.replace(tzinfo=None))["total"]
        if turno not in ["Ferie", "Riposo"] and end <= now and (last_shift_end is None or end > last_shift_end):
            last_shift_end = end
            last_shift_label = f"{turno} {start.strftime('%d/%m %H:%M')}"
            last_shift_total = compute_turno(data, turno, festivo, rules, until=datetime.max.replace(tzinfo=None))["total"]
        if current_shift_end is None and start.strftime("%Y-%m-%d") <= today <= end.strftime("%Y-%m-%d"):
            live_today += compute_turno(data, turno, festivo, rules, until=now, only_day=today)["total"]
            expected_today += compute_turno(data, turno, festivo, rules, until=datetime.max.replace(tzinfo=None), only_day=today)["total"]

    if current_shift_end is None:
        live_today = last_shift_total
        expected_today = next_shift_total
        turno_kpi_label = "Ultimo / prossimo turno"

    live_month += rules["quota_fissa_mensile"]
    payslip_estimate = rules["quota_fissa_mensile"] + current_base_full + prev_extras

    return {
        "live_month": live_month,
        "payslip_estimate": payslip_estimate,
        "live_today": live_today,
        "expected_today": expected_today,
        "current_base_full": current_base_full,
        "prev_extras": prev_extras,
        "hours_live": hours_live,
        "rate_min": rate_min,
        "current_shift": current_shift,
        "current_turno": current_turno,
        "current_shift_date": current_shift_date,
        "current_shift_start_date": current_shift_start_date,
        "turno_kpi_label": turno_kpi_label,
        "last_shift_label": last_shift_label,
        "is_on_shift": bool(current_shift_end),
        "current_shift_end": current_shift_end.isoformat() if current_shift_end else "",
        "current_rate_change_at": current_rate_change_at.isoformat() if current_rate_change_at else "",
        "next_shift_start": next_shift_start.isoformat() if next_shift_start else "",
        "next_shift_label": next_shift_label,
        "work_days_done": work_days_done,
        "work_days_total": work_days_total,
        "ferie_days_total": ferie_days_total,
    }




def _turno_color_info(turno):
    mapping = {
        "Mattina": {"emoji": "🔵", "short": "M", "class": "turni-mattina", "color": "#60a5fa", "md_color": "blue"},
        "Pomeriggio": {"emoji": "🟠", "short": "P", "class": "turni-pomeriggio", "color": "#fb923c", "md_color": "orange"},
        "Notte": {"emoji": "⚫", "short": "N", "class": "turni-notte", "color": "#64748b", "md_color": "grey"},
        "Ferie": {"emoji": "🟢", "short": "F", "class": "turni-ferie", "color": "#34d399", "md_color": "green"},
        "Riposo": {"emoji": "⚪", "short": "R", "class": "turni-riposo", "color": "#cbd5e1", "md_color": "gray"},
    }
    return mapping.get(str(turno), {"emoji": "—", "short": "—", "class": "", "color": "rgba(255,255,255,0.45)", "md_color": "gray"})


def _segmenti_turno(data_str, turno, forced_festivo):
    if turno == "Ferie":
        return "8h base"
    if turno == "Riposo":
        return "riposo"
    try:
        start, end = _shift_bounds(data_str, turno)
    except Exception:
        return "—"
    feriali = 0.0
    festivi = 0.0
    t = start
    while t < end:
        nxt = min(t + timedelta(minutes=1), end)
        h = (nxt - t).total_seconds() / 3600
        if _is_festive_at(t, forced_festivo):
            festivi += h
        else:
            feriali += h
        t = nxt
    parts = []
    if feriali > 0:
        parts.append(f"{feriali:.0f}h fer.")
    if festivi > 0:
        parts.append(f"{festivi:.0f}h fest.")
    return " / ".join(parts) if parts else "—"


def _add_months_turni(date_value, months):
    month_index = date_value.month - 1 + months
    year = date_value.year + month_index // 12
    month = month_index % 12 + 1
    return datetime(year, month, 1).date()


def _turni_month_label(date_value):
    mesi = [
        "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
        "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"
    ]
    return f"{mesi[date_value.month - 1]} {date_value.year}"


def _turni_short_date_label(dt_obj):
    giorni = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
    mesi = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
    return f"{giorni[dt_obj.weekday()]} {dt_obj.day} {mesi[dt_obj.month - 1]}"


def _unfold_ics_lines(text):
    lines = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line.startswith((" ", "\t")) and lines:
            lines[-1] += raw_line[1:]
        else:
            lines.append(raw_line)
    return lines


def _parse_ics_datetime(value):
    value = value.strip()
    if len(value) == 8 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d")
    is_utc = value.endswith("Z")
    value = value.rstrip("Z")
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            parsed = datetime.strptime(value, fmt)
            if is_utc and ZoneInfo is not None:
                parsed = parsed.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Europe/Rome")).replace(tzinfo=None)
            return parsed
        except ValueError:
            pass
    return None


def _calendar_turno_from_summary(summary):
    summary_l = summary.strip().lower()
    mapping = [
        ("mattina", "Mattina"),
        ("pomeriggio", "Pomeriggio"),
        ("notte", "Notte"),
        ("ferie", "Ferie"),
    ]
    for token, turno in mapping:
        if token in summary_l:
            return turno
    if summary_l in ["m", "m.", "morning"]:
        return "Mattina"
    if summary_l in ["p", "p.", "evening"]:
        return "Pomeriggio"
    if summary_l in ["n", "n.", "night"]:
        return "Notte"
    return ""


@st.cache_data(ttl=300, show_spinner=False)
def load_google_calendar_ics(ical_url):
    with urllib.request.urlopen(ical_url, timeout=15) as response:
        return response.read().decode("utf-8", errors="replace")


def import_turni_from_calendar_ics(ical_url, selected_month, fixed_turno=""):
    ical_text = load_google_calendar_ics(ical_url)
    events = []
    current = None
    for line in _unfold_ics_lines(ical_text):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current:
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.split(";", 1)[0].upper()
        if key in ["SUMMARY", "DTSTART"]:
            current[key] = value

    rows = []
    month_key = selected_month.strftime("%Y-%m")
    seen_dates = set()
    for event in events:
        summary = event.get("SUMMARY", "")
        turno = fixed_turno or _calendar_turno_from_summary(summary)
        start = _parse_ics_datetime(event.get("DTSTART", ""))
        if not turno or not start:
            continue
        data_str = start.strftime("%Y-%m-%d")
        if not data_str.startswith(month_key) or data_str in seen_dates:
            continue
        seen_dates.add(data_str)
        rows.append({
            "Data": data_str,
            "Turno": turno,
            "Festivo": "festivo" in summary.lower(),
        })
    return _normalize_turni_df(pd.DataFrame(rows, columns=TURNI_HEADERS))


def import_turni_from_calendar_sources(calendar_sources, selected_month):
    frames = []
    errors = []
    for turno, ical_url in calendar_sources.items():
        if not ical_url:
            continue
        fixed_turno = turno if turno in TURNI_ORARI else ""
        try:
            imported = import_turni_from_calendar_ics(ical_url, selected_month, fixed_turno=fixed_turno)
        except Exception as e:
            errors.append(f"{turno}: {e}")
            continue
        if not imported.empty:
            frames.append(imported)
    if not frames:
        return pd.DataFrame(columns=TURNI_HEADERS), errors
    df = pd.concat(frames, ignore_index=True)
    df["turno_priority"] = df["Turno"].map({"Mattina": 1, "Pomeriggio": 2, "Notte": 3, "Ferie": 4}).fillna(9)
    df = df.sort_values(["Data", "turno_priority"]).drop_duplicates(subset=["Data"], keep="first")
    return _normalize_turni_df(df.drop(columns=["turno_priority"])), errors


def sync_turni_month_from_calendar(df_turni, calendar_sources, selected_month):
    imported, errors = import_turni_from_calendar_sources(calendar_sources, selected_month)
    if imported.empty:
        return df_turni.copy(), 0, errors
    month_key = selected_month.strftime("%Y-%m")
    other_months = df_turni[~df_turni["Data"].str.startswith(month_key)].copy()
    manual_festivi = df_turni[
        df_turni["Data"].str.startswith(month_key)
        & (~df_turni["Turno"].isin(TURNI_ORARI.keys()) | (df_turni["Turno"] == ""))
        & (df_turni["Festivo"] == True)
    ].copy()
    synced = pd.concat([other_months, manual_festivi, imported], ignore_index=True)
    return _normalize_turni_df(synced), len(imported), errors


def _default_calendar_ical_url():
    try:
        secret_url = st.secrets.get("GOOGLE_CALENDAR_ICAL_URL", "")
    except Exception:
        secret_url = ""
    return CALENDAR_ICAL_URL or secret_url


def _default_calendar_ical_urls():
    urls = {turno: url for turno, url in CALENDAR_ICAL_URLS.items() if url}
    try:
        secret_urls = st.secrets.get("GOOGLE_CALENDAR_ICAL_URLS", {})
        if hasattr(secret_urls, "items"):
            for turno, url in secret_urls.items():
                if turno in TURNI_ORARI and url:
                    urls[turno] = url
    except Exception:
        pass
    single_url = _default_calendar_ical_url()
    if single_url:
        urls["Auto"] = single_url
    return urls


def render_live_turni_kpis(stats):
    live_month = float(stats["live_month"])
    live_today = float(stats["live_today"])
    rate_min = float(stats["rate_min"])
    rate_sec = rate_min / 60
    payslip_estimate = _money_turni(stats["payslip_estimate"])
    expected_today = _money_turni(stats["expected_today"])
    current_shift = str(stats["current_shift"]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    current_turno = str(stats.get("current_turno", "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    current_shift_date = str(stats.get("current_shift_date", "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    turno_kpi_label = str(stats.get("turno_kpi_label", "Turno — live / totale turno")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    is_on_shift = bool(stats.get("is_on_shift", False))
    status_color = "#22c55e" if is_on_shift else "#64748b"
    status_shadow = "0 0 12px rgba(34,197,94,0.75)" if is_on_shift else "none"
    status_text = f"In turno · {current_turno} · {current_shift_date}" if is_on_shift else "Fuori turno"
    current_shift_end = stats.get("current_shift_end", "")
    current_rate_change_at = stats.get("current_rate_change_at", "")
    next_shift_start = stats.get("next_shift_start", "")
    next_shift_label = str(stats.get("next_shift_label", "—")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    work_days_done = int(stats.get("work_days_done", 0))
    work_days_total = int(stats.get("work_days_total", 0))
    ferie_days_total = int(stats.get("ferie_days_total", 0))
    month_days_total = work_days_total + ferie_days_total
    ferie_suffix = f" + {ferie_days_total} ferie = {month_days_total}" if ferie_days_total else ""
    components.html(f"""
    <div class="turni-live-grid">
      <div class="kpi-card" style="border-color:rgba(52,211,153,0.25);">
        <div class="kpi-label">Mese corrente — live / stimato cedolino</div>
        <div class="kpi-value" style="color:#34d399;"><span id="turni-live-month"></span> / {payslip_estimate}</div>
        <div class="turni-subline">Giorni lavorati: {work_days_done} / {work_days_total}{ferie_suffix}</div>
      </div>
      <div class="kpi-card" style="border-color:rgba(96,165,250,0.25);">
        <div class="kpi-label">{turno_kpi_label}</div>
        <div class="kpi-value" style="color:#60a5fa;"><span id="turni-live-today"></span> / {expected_today}</div>
        <div id="turni-hours-left" class="turni-subline">Ore mancanti: —</div>
      </div>
      <div class="kpi-card" style="border-color:rgba(254,243,199,0.25);">
        <div class="kpi-label">Stato turno</div>
        <div class="turni-status-row">
          <span id="turni-status-dot" class="turni-status-dot" style="background:{status_color}; box-shadow:{status_shadow};"></span>
          <span id="turni-status-text">{status_text}</span>
        </div>
        <div id="turni-rate-min" class="kpi-value" style="color:#fef3c7;">{rate_min:.3f} €/min</div>
        <div id="turni-shift-label" style="font-size:11px;color:rgba(255,255,255,0.35);margin-top:4px;">{current_shift}</div>
      </div>
    </div>
    <style>
      body {{
        margin: 0;
        background: transparent;
        font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      .turni-live-grid {{
        display: grid;
        grid-template-columns: 1fr 1fr 1fr;
        gap: 12px;
        margin-bottom: 12px;
      }}
      .kpi-card {{
        background: rgba(255,255,255,0.045);
        border: 0.5px solid rgba(255,255,255,0.10);
        border-radius: 12px;
        padding: 14px 16px;
        min-height: 72px;
        box-sizing: border-box;
      }}
      .kpi-label {{
        font-size: 11px;
        font-weight: 500;
        color: rgba(255,255,255,0.45);
        text-transform: uppercase;
        letter-spacing: 0.8px;
        margin-bottom: 6px;
      }}
      .kpi-value {{
        font-size: 23px;
        line-height: 1.15;
        font-weight: 600;
      }}
      .turni-status-row {{
        display: flex;
        align-items: center;
        gap: 7px;
        color: rgba(255,255,255,0.82);
        font-size: 13px;
        font-weight: 600;
        margin-bottom: 6px;
      }}
      .turni-status-dot {{
        display: inline-block;
        width: 10px;
        height: 10px;
        border-radius: 999px;
      }}
      .turni-subline {{
        font-size: 12px;
        color: rgba(255,255,255,0.42);
        margin-top: 5px;
      }}
    </style>
    <script>
      const start = Date.now();
      const startMonth = {live_month:.8f};
      const startToday = {live_today:.8f};
      const rateSec = {rate_sec:.10f};
      const shiftEnd = {json.dumps(current_shift_end)};
      const rateChangeAt = {json.dumps(current_rate_change_at)};
      const nextShiftStart = {json.dumps(next_shift_start)};
      const nextShiftLabel = {json.dumps(next_shift_label)};
      const isInitiallyOnShift = {json.dumps(is_on_shift)};
      const monthEl = document.getElementById("turni-live-month");
      const todayEl = document.getElementById("turni-live-today");
      const dotEl = document.getElementById("turni-status-dot");
      const statusEl = document.getElementById("turni-status-text");
      const rateEl = document.getElementById("turni-rate-min");
      const shiftEl = document.getElementById("turni-shift-label");
      const hoursLeftEl = document.getElementById("turni-hours-left");
      let refreshQueued = false;

      function money(value) {{
        return new Intl.NumberFormat("it-IT", {{
          style: "currency",
          currency: "EUR",
          minimumFractionDigits: 2,
          maximumFractionDigits: 2
        }}).format(value);
      }}

      function elapsedSeconds() {{
        if (!rateSec || !shiftEnd) return 0;
        const now = Date.now();
        const end = Date.parse(shiftEnd);
        return Math.max(0, Math.min(now, end) - start) / 1000;
      }}

      function remainingLabel() {{
        const target = shiftEnd || nextShiftStart;
        if (!target) return isInitiallyOnShift ? "Ore mancanti: —" : "Prossimo turno: —";
        const remainingMs = Math.max(0, Date.parse(target) - Date.now());
        const totalMinutes = Math.ceil(remainingMs / 60000);
        const days = Math.floor(totalMinutes / 1440);
        const clockHours = Math.floor((totalMinutes % 1440) / 60);
        const minutes = totalMinutes % 60;
        if (!isInitiallyOnShift) {{
          const dayPart = days ? `${{days}}g ` : "";
          return `Prossimo turno tra: ${{dayPart}}${{clockHours}}h ${{String(minutes).padStart(2, "0")}}m`;
        }}
        const totalHours = Math.floor(totalMinutes / 60);
        return `Ore mancanti: ${{totalHours}}h ${{String(minutes).padStart(2, "0")}}m`;
      }}

      function refreshParentSoon() {{
        if (refreshQueued) return;
        refreshQueued = true;
        setTimeout(() => {{
          try {{
            window.parent.location.reload();
          }} catch (e) {{
            window.location.reload();
          }}
        }}, 1200);
      }}

      function tick() {{
        const ended = shiftEnd && Date.now() >= Date.parse(shiftEnd);
        const rateChanged = rateChangeAt && Date.now() >= Date.parse(rateChangeAt);
        const shouldStart = !isInitiallyOnShift && nextShiftStart && Date.now() >= Date.parse(nextShiftStart);
        const extra = elapsedSeconds() * rateSec;
        monthEl.textContent = money(startMonth + extra);
        todayEl.textContent = money(startToday + extra);
        hoursLeftEl.textContent = remainingLabel();
        if (!isInitiallyOnShift && nextShiftLabel && nextShiftLabel !== "—") {{
          shiftEl.textContent = `Prossimo: ${{nextShiftLabel}}`;
        }}
        if (shouldStart) {{
          refreshParentSoon();
          return;
        }}
        if (rateChanged) {{
          hoursLeftEl.textContent = "Aggiorno fascia turno...";
          refreshParentSoon();
          return;
        }}
        if (ended) {{
          dotEl.style.background = "#64748b";
          dotEl.style.boxShadow = "none";
          statusEl.textContent = "Fuori turno";
          rateEl.textContent = "0.000 €/min";
          shiftEl.textContent = "—";
          hoursLeftEl.textContent = "Aggiorno stato turno...";
          refreshParentSoon();
        }}
      }}

      tick();
      setInterval(tick, 1000);
    </script>
    """, height=126)


def render_turni_guadagni_section():
    st.markdown('<div class="section-pill">⏱️ Guadagni Turni</div>', unsafe_allow_html=True)
    rules = get_turni_rules()
    if "turni_calendar_month" not in st.session_state:
        today_month = _now_italy().date()
        st.session_state.turni_calendar_month = datetime(today_month.year, today_month.month, 1).date()
    selected_month = st.session_state.turni_calendar_month
    month_key = selected_month.strftime("%Y-%m")

    df_turni = load_turni_data()
    auto_calendar_sources = _default_calendar_ical_urls()
    auto_sync_key = f"turni_calendar_autosync::{month_key}"
    if auto_calendar_sources and not st.session_state.get(auto_sync_key, False):
        synced_df, imported_count, calendar_errors = sync_turni_month_from_calendar(df_turni, auto_calendar_sources, selected_month)
        st.session_state[auto_sync_key] = True
        if imported_count > 0:
            st.session_state.turni_df_draft = synced_df.copy()
            st.session_state.turni_dirty = False
            df_turni = synced_df.copy()
        if calendar_errors:
            st.warning("Alcuni calendari non sono raggiungibili: " + " | ".join(calendar_errors))

    stats = compute_turni_dashboard(df_turni, rules)
    current_work_day = stats.get("current_shift_start_date", "") if stats.get("is_on_shift", False) else ""

    render_live_turni_kpis(stats)

    tab_cal, tab_rules = st.tabs(["📅 Turni", "⚙️ Regole"])

    with tab_cal:
        st.markdown('<div class="turni-compact-row">', unsafe_allow_html=True)
        festivo_manual = st.checkbox("Modifica festivo manuale", key="turni_festivo_manual")
        st.markdown('</div>', unsafe_allow_html=True)

        year, month = selected_month.year, selected_month.month

        cal_col, summary_col = st.columns(LAYOUT_COLONNE["turni_calendario_riepilogo"], gap="medium")

        with cal_col:
            st.markdown('<div class="turni-calendar-wrap">', unsafe_allow_html=True)
            prev_col, title_col, next_col = st.columns(LAYOUT_COLONNE["turni_frecce_titolo"], gap="small")
            with prev_col:
                if st.button("←", key="turni_prev_month", use_container_width=True):
                    st.session_state.turni_calendar_month = _add_months_turni(selected_month, -1)
                    st.rerun()
            with title_col:
                st.markdown(f"#### 📅 Calendario · {_turni_month_label(selected_month)}")
            with next_col:
                if st.button("→", key="turni_next_month", use_container_width=True):
                    st.session_state.turni_calendar_month = _add_months_turni(selected_month, 1)
                    st.rerun()
            weekdays = ["L", "M", "M", "G", "V", "S", "D"]
            cols = st.columns(7)
            for c, wd in zip(cols, weekdays):
                c.markdown(f"<div style='text-align:center;color:rgba(255,255,255,0.45);font-size:12px;'>{wd}</div>", unsafe_allow_html=True)

            cal = calendar.Calendar(firstweekday=0)
            for week in cal.monthdatescalendar(year, month):
                cols = st.columns(7)
                for c, day in zip(cols, week):
                    if day.month != month:
                        c.markdown("<div style='height:34px;opacity:.2;'> </div>", unsafe_allow_html=True)
                        continue
                    day_str = day.strftime("%Y-%m-%d")
                    row = df_turni[df_turni["Data"] == day_str]
                    if row.empty:
                        current_label = ""
                    else:
                        turno_corrente = row.iloc[0]["Turno"]
                        info = _turno_color_info(turno_corrente)
                        current_label = f" :{info['md_color']}[**{info['short']}**]" if turno_corrente in TURNI_ORARI and turno_corrente else ""
                    day_is_festive = (
                        day.weekday() == 6
                        or _is_italian_public_holiday(datetime(day.year, day.month, day.day))
                        or (not row.empty and bool(row.iloc[0]["Festivo"]))
                    )
                    day_label = f":red[{day.day}]" if day_is_festive else str(day.day)
                    if day_str == current_work_day:
                        day_label = f":orange[•] {day_label}"
                    clicked_day = c.button(f"{day_label}{current_label}", key=f"turno_day_{day_str}", use_container_width=True)
                    if clicked_day:
                        if festivo_manual:
                            df_new = df_turni[df_turni["Data"] != day_str].copy()
                            turno_esistente = "" if row.empty else str(row.iloc[0].get("Turno", ""))
                            nuovo_festivo = not (not row.empty and bool(row.iloc[0].get("Festivo", False)))
                            df_new = pd.concat([df_new, pd.DataFrame([{
                                "Data": day_str,
                                "Turno": turno_esistente if turno_esistente in TURNI_ORARI else "",
                                "Festivo": nuovo_festivo
                            }])], ignore_index=True)
                            df_new = _normalize_turni_df(df_new)
                            st.session_state.turni_df_draft = df_new.copy()
                            if save_turni_data(df_new):
                                st.session_state.turni_dirty = False
                                st.rerun()
                            else:
                                st.session_state.turni_dirty = True
                                st.error("Festivo manuale aggiornato in bozza, ma non salvato su Google Sheets.")

            st.markdown("""
            <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; font-size:12px; color:rgba(255,255,255,0.55);">
              <span style="border-bottom:4px solid #60a5fa;">Mattina</span>
              <span style="border-bottom:4px solid #fb923c;">Pomeriggio</span>
              <span style="border-bottom:4px solid #64748b;">Notte</span>
              <span style="border-bottom:4px solid #34d399;">Ferie</span>
              <span style="color:#ef4444;">Numero rosso = festivo</span>
            </div>
            """, unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        with summary_col:
            st.markdown("#### 🗓️ Riepilogo turni del mese")
            month_df = df_turni[df_turni["Data"].str.startswith(month_key)].copy()
            month_df = month_df[month_df["Turno"].isin(TURNI_ORARI.keys()) & (month_df["Turno"] != "")]
            if month_df.empty:
                st.info("Nessun turno inserito per il mese selezionato.")
            else:
                month_df = month_df.sort_values("Data")
                today_key = _now_italy().strftime("%Y-%m-%d")
                focus_candidates = month_df[month_df["Data"] >= today_key]
                focus_date = focus_candidates.iloc[0]["Data"] if not focus_candidates.empty else month_df.iloc[-1]["Data"]
                cards = ['<div class="turni-grid-scroll">']
                for _, r in month_df.iterrows():
                    turno = r["Turno"]
                    info = _turno_color_info(turno)
                    calc = compute_turno(r["Data"], turno, bool(r["Festivo"]), rules, until=datetime.max.replace(tzinfo=None))
                    seg = _segmenti_turno(r["Data"], turno, bool(r["Festivo"]))
                    data_dt = pd.to_datetime(r["Data"]).to_pydatetime()
                    festivo_txt = " · festivo" if _is_italian_public_holiday(data_dt) else (" · festivo manuale" if bool(r["Festivo"]) else "")
                    focus_attr = ' id="turni-focus-card"' if r["Data"] == focus_date else ""
                    cards.append(
                        f'<div{focus_attr} class="turni-card-small {info["class"]}">'
                        f'<div class="date">{r["Data"]}{festivo_txt}</div>'
                        f'<div class="title" style="color:{info["color"]};">{info["emoji"]} {turno}</div>'
                        f'<div class="meta">{seg} · Totale {_money_turni(calc["total"])}</div>'
                        f'<div class="meta">Base {_money_turni(calc["base"])} · Extra {_money_turni(calc["extra"])}</div>'
                        f'</div>'
                    )
                cards.append("</div>")
                components.html(f"""
                <style>
                  body {{
                    margin: 0;
                    background: transparent;
                    font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                  }}
                  .turni-grid-scroll {{
                    max-height: 365px;
                    overflow-y: auto;
                    padding-right: 8px;
                  }}
                  .turni-card-small {{
                    background: rgba(255,255,255,0.045);
                    border: 0.5px solid rgba(255,255,255,0.10);
                    border-left: 5px solid rgba(255,255,255,0.25);
                    border-radius: 12px;
                    padding: 7px 9px;
                    margin-bottom: 6px;
                  }}
                  .turni-card-small .date {{
                    font-size: 12px;
                    color: rgba(255,255,255,0.58);
                  }}
                  .turni-card-small .title {{
                    font-size: 14px;
                    font-weight: 600;
                    margin-top: 2px;
                  }}
                  .turni-card-small .meta {{
                    font-size: 11px;
                    color: rgba(255,255,255,0.42);
                    margin-top: 3px;
                  }}
                  .turni-mattina {{ border-left-color:#60a5fa; }}
                  .turni-pomeriggio {{ border-left-color:#fb923c; }}
                  .turni-notte {{ border-left-color:#64748b; }}
                  .turni-ferie {{ border-left-color:#34d399; }}
                  #turni-focus-card {{
                    outline: 1px solid rgba(96,165,250,0.45);
                    outline-offset: -1px;
                  }}
                </style>
                {"".join(cards)}
                <script>
                  const focusCard = document.getElementById("turni-focus-card");
                  const scroller = document.querySelector(".turni-grid-scroll");
                  if (focusCard && scroller) {{
                    scroller.scrollTop = Math.max(0, focusCard.offsetTop - 6);
                  }}
                </script>
                """, height=370)

        if st.session_state.get("turni_dirty", False):
            st.warning("Festivo manuale modificato in bozza: Google Sheets non ha confermato il salvataggio.")

    with tab_rules:
        c1, c2 = st.columns(2)
        with c1:
            rules["paga_oraria"] = st.number_input("Paga oraria base", value=float(rules["paga_oraria"]), step=0.10, key="turni_paga")
            rules["quota_fissa_mensile"] = st.number_input("Quota fissa mensile opzionale", value=float(rules["quota_fissa_mensile"]), step=10.0, key="turni_quota")
            rules["m_p_feriale_pct"] = st.number_input("Mattina/Pomeriggio feriale %", value=float(rules["m_p_feriale_pct"]), step=1.0, key="turni_mp_feriale")
            rules["m_p_festivo_giorno_pct"] = st.number_input("Mattina/Pomeriggio festivo 06-18 %", value=float(rules["m_p_festivo_giorno_pct"]), step=1.0, key="turni_mp_festivo")
            rules["notte_feriale_pct"] = st.number_input("Notte feriale %", value=float(rules["notte_feriale_pct"]), step=1.0, key="turni_notte_feriale")
            rules["festivo_sera_notte_pct"] = st.number_input("Festivo sera/notte %", value=float(rules["festivo_sera_notte_pct"]), step=1.0, key="turni_festivo_notte")
        with c2:
            rules["ind_m_p_feriale"] = st.number_input("Indennità M/P feriale", value=float(rules["ind_m_p_feriale"]), step=1.0, key="turni_ind_mp_f")
            rules["ind_notte_feriale"] = st.number_input("Indennità notte feriale", value=float(rules["ind_notte_feriale"]), step=1.0, key="turni_ind_n_f")
            rules["ind_m_p_festivo"] = st.number_input("Indennità M/P festiva", value=float(rules["ind_m_p_festivo"]), step=1.0, key="turni_ind_mp_fe")
            rules["ind_notte_festiva"] = st.number_input("Indennità notte festiva", value=float(rules["ind_notte_festiva"]), step=1.0, key="turni_ind_n_fe")
            st.markdown("""
            <div class="kpi-card">
                <div class="kpi-label">Regole applicate</div>
                <div style="font-size:12px;color:rgba(255,255,255,0.65);line-height:1.5;">
                M 06-14: 20% / 50% + 6€/15€<br>
                P 14-18: 20% / 50% + 6€/15€<br>
                P 18-22: 20% / 60%, senza seconda indennità<br>
                N 22-06: 50% / 60% + 15€/25€<br>
                Ferie: 8 ore base
                </div>
            </div>
            """, unsafe_allow_html=True)

        st.session_state.turni_rules = rules
        st.caption("Le regole sono salvate nella sessione Streamlit. I turni arrivano da Google Calendar; il festivo manuale viene salvato subito su Google Sheets quando lo modifichi.")
# ─────────────────────────────────────────────────────────────────────────────

def main():
    load_spese_fisse_settings()
    load_altre_entrate_settings()

    col_left, col_center, col_right = st.columns(LAYOUT_COLONNE["titolo_dashboard"], gap="large")
    with col_left:
        st.markdown('<div class="section-pill">💎 Dashboard Finanziaria</div>', unsafe_allow_html=True)
    with col_center:
        st.markdown("<h1 style='text-align: center;'>Calcolatore di Spese Personali</h1>", unsafe_allow_html=True)

    st.markdown('<div style="height:4px;"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-pill">💶 Impostazioni Mese</div>', unsafe_allow_html=True)
    col_stip_inserimento1, col_stip_inserimento2, col_stip_inserimento3, col_stip_inserimento4 = st.columns(LAYOUT_COLONNE["header_stipendi_note"], gap="large")
    col1, col2, col3 = st.columns(LAYOUT_COLONNE["dashboard_principale"], gap="large")

    with col_stip_inserimento1:
        st.markdown('<div class="salary-input-label">Stipendio percepito</div>', unsafe_allow_html=True)
        stipendio_percepito = st.number_input("Inserisci lo stipendio effettivamente percepito:", min_value=input_stipendio_percepito, step=50, label_visibility="collapsed")
        st.markdown('<div style="height:10px;"></div><div class="salary-input-label">Risparmio mese prec.</div>', unsafe_allow_html=True)
        risparmi_mese_precedente = st.number_input("Inserisci quanto hai risparmiato nel mese precedente:", min_value=input_risparmi_mese_precedente, step=50, label_visibility="collapsed")
    with col_stip_inserimento2:
        st.markdown('<div class="salary-input-label">Quota stipendio scelta</div>', unsafe_allow_html=True)
        budget_da_stipendio = st.number_input("Inserisci la parte dello stipendio che scegli di usare:", min_value=input_budget_da_stipendio, step=50, label_visibility="collapsed")
        st.markdown('<div style="font-size:11px;color:rgba(255,255,255,.42);margin-top:4px;">Il resto va nei risparmi.</div>', unsafe_allow_html=True)
    altre_entrate_totali = sum(ALTRE_ENTRATE.values())
    entrate_mensili_totali = stipendio_percepito + altre_entrate_totali
    budget_mensile_disponibile = budget_da_stipendio + altre_entrate_totali

    # Alias temporanei per mantenere compatibile il codice esistente mentre la nomenclatura viene ripulita.
    stipendio_originale = stipendio_percepito
    stipendio_scelto = budget_da_stipendio
    tot_stipendio = entrate_mensili_totali
    tot_utilizzare = budget_mensile_disponibile
    stipendio = budget_mensile_disponibile
    stipendio_totale = entrate_mensili_totali
    stipendio_utilizzare = budget_mensile_disponibile

    with col_stip_inserimento3:
        _ts = f"€{entrate_mensili_totali:,.2f}"
        _tu = f"€{budget_mensile_disponibile:,.2f}"
    
        # ───────── Divisione in 2 colonne ─────────
        col_stip_inserimento3_1, col_stip_inserimento3_2 = st.columns(2, gap="medium")
    
        # ───────── Prima card ─────────
        with col_stip_inserimento3_1:
            st.markdown(f"""
            <div class="kpi-card">
                <div class="kpi-label">Entrate mensili totali</div>
                <div class="kpi-value" style="color:#77DD77;">{_ts}</div>
                <div style="font-size:12px;color:rgba(255,255,255,0.42);margin-top:3px;">
                    Stipendio percepito + altre entrate
                </div>
            </div>
            """, unsafe_allow_html=True)
    
        # ───────── Seconda card ─────────
        with col_stip_inserimento3_2:
            st.markdown(f"""
            <div class="kpi-card">
                <div class="kpi-label">Budget mensile disponibile</div>
                <div class="kpi-value" style="color:#60a5fa;">{_tu}</div>
                <div style="font-size:12px;color:rgba(255,255,255,0.42);margin-top:3px;">
                    Quota stipendio scelta + altre entrate
                </div>
            </div>
            """, unsafe_allow_html=True)

    with col_stip_inserimento4:
            # ───────── STILE POST-IT ─────────
            st.markdown("""
            <style>
textarea {
    background-color: rgba(255, 241, 118, 0.35) !important;
    color: black !important;
    border-radius: 12px !important;
    border: none !important;
    box-shadow: 3px 3px 10px rgba(0,0,0,0.25) !important;
    padding: 10px !important;
    resize: none !important;
}

[data-testid="stPopover"] button {
    background: rgba(255, 241, 118, 0.18) !important;
    border: 0.5px solid rgba(255, 241, 118, 0.35) !important;
    color: #fde68a !important;
    border-radius: 10px !important;
    min-height: 46px;
    width: 100%;
}

[data-testid="stExpander"] details {
    background: rgba(148,163,184,.10) !important;
    border: 0.5px solid rgba(148,163,184,.28) !important;
    border-radius: 10px !important;
}

[data-testid="stExpander"] summary {
    color: #cbd5e1 !important;
    font-weight: 800 !important;
}

.memo-card {
    min-height: 118px;
    border-radius: 12px;
    padding: 12px 13px;
    margin: 0 0 8px;
    background:
        linear-gradient(135deg, rgba(255,241,118,.20), rgba(255,241,118,.10)),
        rgba(255,255,255,.035);
    border: 0.5px solid rgba(255,241,118,.28);
    box-shadow: 0 10px 24px rgba(0,0,0,.18);
}

.memo-card-title {
    font-size: 12px;
    font-weight: 900;
    letter-spacing: .8px;
    text-transform: uppercase;
    color: #fde68a;
    margin-bottom: 7px;
}

.memo-card-preview {
    min-height: 62px;
    color: rgba(255,255,255,.78);
    font-size: 12px;
    line-height: 1.35;
    white-space: pre-wrap;
}

.memo-card-empty {
    color: rgba(255,255,255,.38);
    font-style: italic;
}
            </style>
            """, unsafe_allow_html=True)
        
            # ───────── CONFIG ─────────
            NOTE_HEADERS = ["id", "nota1", "nota2", "nota3", "nota4", "budget_ideale", "risparmio_desiderato"]
            worksheet_name = "Note e Obiettivo"
            legacy_worksheet_name = "Note"

            if "note_df_draft" not in st.session_state:
                df_note = load_data_gsheets(worksheet_name, NOTE_HEADERS)
                note_loaded_from_sheet = not df_note.empty
                if df_note.empty:
                    legacy_df_note = load_data_gsheets(legacy_worksheet_name, NOTE_HEADERS)
                    if not legacy_df_note.empty:
                        df_note = legacy_df_note.copy()
                        note_loaded_from_sheet = True
                if "testo" in df_note.columns and "nota1" not in df_note.columns:
                    df_note["nota1"] = df_note["testo"]
                for col in NOTE_HEADERS:
                    if col not in df_note.columns:
                        df_note[col] = ""
                if df_note.empty:
                    df_note = pd.DataFrame([{
                        "id": 1,
                        "nota1": "",
                        "nota2": "",
                        "nota3": "",
                        "nota4": "",
                        "budget_ideale": budget_mensile_disponibile_ideale,
                        "risparmio_desiderato": risparmio_mensile_desiderato
                    }])
                st.session_state.note_df_draft = df_note[NOTE_HEADERS].copy()
                st.session_state.note_loaded_from_sheet = note_loaded_from_sheet

            if "note_loaded_from_sheet" not in st.session_state:
                st.session_state.note_loaded_from_sheet = True

            df_note = st.session_state.note_df_draft.copy()
            if df_note.empty:
                df_note = pd.DataFrame([{
                    "id": 1,
                    "nota1": "",
                    "nota2": "",
                    "nota3": "",
                    "nota4": "",
                    "budget_ideale": budget_mensile_disponibile_ideale,
                    "risparmio_desiderato": risparmio_mensile_desiderato
                }])
            nota_corrente = df_note.iloc[0]

            def _nota_value(key):
                value = nota_corrente.get(key, "")
                return "" if pd.isna(value) else str(value)

            def _nota_number(key, default):
                value = nota_corrente.get(key, default)
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return float(default)

            def _build_note_df(nota1_value, nota2_value, nota3_value, nota4_value, risparmio_value):
                return pd.DataFrame([{
                    "id": 1,
                    "nota1": nota1_value,
                    "nota2": nota2_value,
                    "nota3": nota3_value,
                    "nota4": nota4_value,
                    "budget_ideale": budget_disponibile_target,
                    "risparmio_desiderato": risparmio_value
                }])
        
            # ───────── UI BUDGET ─────────
            risparmio_desiderato_corrente = _nota_number("risparmio_desiderato", risparmio_mensile_desiderato)
            target_budget = calcola_target_budget_dinamico(sum(SPESE["Fisse"].values()))
            budget_disponibile_target = target_budget["budget_disponibile_target"]
            risparmio_auto_variabili_target = target_budget["risparmio_auto_variabili"]

            budget_card_col, obiettivi_col, budget_spacer = st.columns([1.06, 0.54, 1.10], gap="small")
            with budget_card_col:
                entrate_totali_target = budget_disponibile_target + max(0, risparmio_desiderato_corrente - risparmio_auto_variabili_target)
                gap_budget_ideale = max(0, budget_disponibile_target - budget_mensile_disponibile)
                gap_entrate_ideali = max(0, entrate_totali_target - entrate_mensili_totali)
                budget_status = "ok" if gap_budget_ideale <= 0 else f"-€{gap_budget_ideale:,.2f}"
                entrate_status = "ok" if gap_entrate_ideali <= 0 else f"-€{gap_entrate_ideali:,.2f}"
                st.markdown(f"""
                <div class="budget-memory-card">
                    <div class="budget-memory-title">Promemoria budget</div>
                    <div class="budget-memory-row">
                        <div class="budget-memory-label">Budget mensile desiderato<br><span style="color:rgba(255,255,255,.42);">target €{budget_disponibile_target:,.0f} per coprire spese fisse + variabili</span></div>
                        <div class="budget-memory-value">{budget_status}</div>
                    </div>
                    <div class="budget-memory-row">
                        <div class="budget-memory-label">Entrate mensili totali desiderate<br><span style="color:rgba(255,255,255,.42);">target €{entrate_totali_target:,.0f} · per risparmiare €{risparmio_desiderato_corrente:,.0f}</span></div>
                        <div class="budget-memory-value">{entrate_status}</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            with obiettivi_col:
                with st.expander("Obiettivo risparmi", expanded=False):
                    risparmio_desiderato_corrente = st.number_input(
                        "Risparmio desiderato",
                        min_value=0.0,
                        value=float(risparmio_desiderato_corrente),
                        step=25.0,
                        help="Quanto vuoi riuscire a mettere da parte oltre al budget del mese.",
                        key="risparmio_desiderato_promemoria"
                    )
                    salva_obiettivo = st.button(
                        "💾 Salva obiettivo",
                        use_container_width=True,
                        key="save_obiettivo_risparmi",
                        disabled=not st.session_state.get("note_loaded_from_sheet", True)
                    )
                    if salva_obiettivo:
                        df_note = _build_note_df(
                            _nota_value("nota1"),
                            _nota_value("nota2"),
                            _nota_value("nota3"),
                            _nota_value("nota4"),
                            risparmio_desiderato_corrente
                        )
                        if save_data_gsheets(worksheet_name, NOTE_HEADERS, df_note):
                            st.session_state.note_df_draft = df_note.copy()
                            st.session_state.note_loaded_from_sheet = True
                            st.success("Obiettivo salvato")
                        else:
                            st.error("Errore salvataggio obiettivo")
            if not st.session_state.get("note_loaded_from_sheet", True):
                st.warning("Note non caricate da Google Sheets: salvataggio disabilitato per evitare di sovrascriverle vuote.")
            # Le note vengono mostrate piu sotto, accanto al dettaglio spese fisse.

    spese_fisse_totali = sum(SPESE["Fisse"].values())
    risparmiabili = stipendio - spese_fisse_totali
    if risparmiabili < 0:
        risparmiabili = 0

    percentuali_variabili = {"Emergenze/Compleanni": emergenze_compleanni, "Viaggi": viaggi}
    for voce, percentuale in percentuali_variabili.items():
        SPESE["Variabili"][voce] = percentuale * risparmiabili

    da_spendere_senza_limite = percentuale_limite_da_spendere * (risparmiabili - sum(percentuali_variabili.values()) * risparmiabili)
    SPESE["Variabili"]["Da spendere"] = min(da_spendere_senza_limite, limite_da_spendere)

    spese_quotidiane_senza_limite = risparmiabili - sum(percentuali_variabili.values()) * risparmiabili - da_spendere_senza_limite
    SPESE["Variabili"]["Spese quotidiane"] = min(spese_quotidiane_senza_limite, max_spese_quotidiane)
    
    risparmi_mensili = stipendio_originale - stipendio_scelto
    da_spendere = SPESE["Variabili"]["Da spendere"]
    spese_quotidiane = SPESE["Variabili"]["Spese quotidiane"]

    if spese_quotidiane_senza_limite > max_spese_quotidiane:
        eccesso_spese_quotidiane = spese_quotidiane_senza_limite - max_spese_quotidiane
        risparmi_mensili += eccesso_spese_quotidiane
    if da_spendere_senza_limite > limite_da_spendere:
        eccesso_da_spendere = da_spendere_senza_limite - limite_da_spendere
        risparmi_mensili += eccesso_da_spendere

    risparmio_stipendi = stipendio_originale - stipendio_scelto
    risparmio_da_spendere = da_spendere_senza_limite - da_spendere if da_spendere_senza_limite > limite_da_spendere else 0
    risparmio_spese_quotidiane = spese_quotidiane_senza_limite - spese_quotidiane if spese_quotidiane_senza_limite > max_spese_quotidiane else 0

    revolut_expenses = sum(
        SPESE["Fisse"].get(voce, 0) + SPESE["Variabili"].get(voce, 0)
        for voce in SPESE["Revolut"]
    )
    revolut_expenses -= risparmi_mese_precedente
    risparmi_mensili += risparmi_mese_precedente

    df_altre_entrate = pd.DataFrame.from_dict(ALTRE_ENTRATE, orient="index", columns=["Importo"]).reset_index().rename(columns={"index": "Categoria"})

    with st.spinner("Creazione dei grafici..."):
        chart_fisse, chart_variabili, chart_altre_entrate, df_fisse, df_variabili, df_altre_entrate, color_map = create_charts(stipendio, risparmiabili, df_altre_entrate)

        df_totali_impilati = pd.DataFrame({
            "Categoria": ["Spese Fisse", "Spese Variabili", "Entrate Mensili Totali", "Entrate Mensili Totali", 
                        "Risparmi", "Budget Mensile", "Budget Mensile"],
            "Tipo": ["Spese Fisse", "Spese Variabili", "Stipendio Percepito", "Altre Entrate", 
                    "Risparmi", "Budget da Stipendio", "Altre Entrate"],
            "Totale": [
                df_fisse["Importo"].sum(),
                df_variabili["Importo"].sum(),
                stipendio_originale,
                sum(ALTRE_ENTRATE.values()),
                risparmi_mensili,
                stipendio_scelto, 
                sum(ALTRE_ENTRATE.values()) 
            ]
        })

        ordine_categorie = ["Entrate Mensili Totali", "Budget Mensile", "Spese Fisse", "Spese Variabili", "Risparmi"]
        # Fix: use absolute values for scale to avoid negative display issues
        valore_massimo = df_totali_impilati['Totale'].abs().max()
        margine = valore_massimo * 0.3
        limite_superiore = valore_massimo + margine
        # Replace negative values with 0 for display
        df_totali_impilati['Totale'] = df_totali_impilati['Totale'].clip(lower=0)

        base = alt.Chart(df_totali_impilati, title='Confronto Totali per Categoria').transform_stack(
            stack='Totale',
            groupby=['Categoria'],
            sort=[{'field': 'Tipo', 'order': 'descending'}],
            as_=['lower', 'upper']
        )

        bars = base.mark_bar().encode(
            x=alt.X('Categoria:N', sort=ordine_categorie, title="Categoria", axis=alt.Axis(labelAngle=0)),
            y=alt.Y('lower:Q', title="Totale", scale=alt.Scale(domain=[0, limite_superiore])),
            y2='upper:Q',
            color=alt.Color('Tipo:N',
                            scale=alt.Scale(domain=[
                                "Stipendio Percepito", "Altre Entrate", "Budget da Stipendio", 
                                "Spese Fisse", "Spese Variabili", "Risparmi"
                            ],
                            range=[
                                color_map["Stipendio Percepito"], 
                                color_map["Altre Entrate"], 
                                color_map["Budget Mensile"], 
                                color_map["Spese Fisse"], 
                                color_map["Spese Variabili"], 
                                color_map["Risparmi"]
                            ]),
                            legend=alt.Legend(title=None)),
            tooltip=['Categoria', 'Tipo', 'Totale']
        )

        labels = base.transform_filter('datum.Totale > 0').transform_calculate(
            mid="(datum.lower + datum.upper) / 2"
        ).mark_text(align='center', baseline='middle', color='black').encode(
            x=alt.X('Categoria:N', sort=ordine_categorie),
            y=alt.Y('mid:Q'),
            text=alt.Text('Totale:Q', format='.2f')
        )

        chart_barre = (bars + labels).properties(title='📊 Confronto Totali per Categoria')

    df_fisse_percentuali = df_fisse.rename(columns={'Importo': 'Valore €'})
    df_fisse['Valore €'] = df_fisse['Importo'].apply(lambda x: f"€ {x:.2f}")
    
    # --- COLONNA 1: SPESE FISSE ---
    with col1:
        st.markdown('<div class="section-pill">🏠 Spese Fisse</div>', unsafe_allow_html=True)
        tab_spese_fisse, tab_decisioni_fisse = st.tabs(["📋 Spese", "⚙️ Decisioni"])

        with tab_decisioni_fisse:
            settings = SPESE["Fisse"].copy()
            metadata = st.session_state.get("spese_fisse_metadata", {})
            gruppi_disponibili = _spesa_fissa_gruppi_disponibili(metadata)

            st.markdown("#### Aggiungi spesa")
            add_nome_col, add_importo_col = st.columns(LAYOUT_COLONNE["form_nome_importo"])
            with add_nome_col:
                nuova_spesa_nome = st.text_input("Nome nuova spesa", key="nuova_spesa_fissa_nome")
            with add_importo_col:
                nuova_spesa_importo = st.number_input("Importo nuova spesa", min_value=0.0, value=0.0, step=5.0, key="nuova_spesa_fissa_importo")

            nuovo_gruppo = st.text_input(
                "Nuovo gruppo visivo da aggiungere",
                key="nuovo_gruppo_spese_fisse",
                placeholder="Es. Animali, Viaggi, Donazioni..."
            ).strip()
            if nuovo_gruppo and nuovo_gruppo not in gruppi_disponibili:
                gruppi_disponibili.append(nuovo_gruppo)
            add_meta_col1, add_meta_col2 = st.columns(2)
            with add_meta_col1:
                nuova_spesa_categoria = st.selectbox("Colore categoria nuova spesa", SPESA_FISSA_CATEGORIE, key="nuova_spesa_fissa_categoria")
            with add_meta_col2:
                nuova_spesa_carta = st.selectbox("Carta nuova spesa", SPESA_FISSA_CARTE, key="nuova_spesa_fissa_carta")
            nuova_spesa_gruppo = st.selectbox("Gruppo visivo nuova spesa", gruppi_disponibili, key="nuova_spesa_fissa_gruppo")

            st.markdown("#### Elimina spesa")
            elimina_spesa = st.selectbox("Voce da eliminare", [""] + list(settings.keys()), key="elimina_spesa_fissa")
            st.markdown("#### Modifica spese esistenti")

            editor_cols = st.columns(2)
            editable_settings = {}
            editable_metadata = {}
            for idx, (voce, importo) in enumerate(settings.items()):
                with editor_cols[idx % 2]:
                    st.markdown(f"**{voce}**")
                    editable_settings[voce] = st.number_input(
                        "Importo",
                        min_value=0.0,
                        value=float(importo),
                        step=5.0,
                        key=f"spesa_fissa_importo_{voce}"
                    )
                    current_categoria = metadata.get(voce, {}).get("Categoria", _infer_spesa_fissa_categoria(voce))
                    current_carta = metadata.get(voce, {}).get("Carta", _infer_spesa_fissa_carta(voce))
                    current_gruppo = metadata.get(voce, {}).get("Gruppo", _infer_spesa_fissa_gruppo(voce))
                    if current_gruppo not in gruppi_disponibili:
                        gruppi_disponibili.append(current_gruppo)
                    editable_metadata[voce] = {
                        "Categoria": st.selectbox(
                            "Colore categoria",
                            SPESA_FISSA_CATEGORIE,
                            index=SPESA_FISSA_CATEGORIE.index(current_categoria) if current_categoria in SPESA_FISSA_CATEGORIE else 0,
                            key=f"spesa_fissa_categoria_{voce}"
                        ),
                        "Carta": st.selectbox(
                            "Carta",
                            SPESA_FISSA_CARTE,
                            index=SPESA_FISSA_CARTE.index(current_carta) if current_carta in SPESA_FISSA_CARTE else 0,
                            key=f"spesa_fissa_carta_{voce}"
                        ),
                        "Gruppo": st.selectbox(
                            "Gruppo visivo",
                            gruppi_disponibili,
                            index=gruppi_disponibili.index(current_gruppo) if current_gruppo in gruppi_disponibili else 0,
                            key=f"spesa_fissa_gruppo_{voce}"
                        ),
                    }
                    st.markdown("---")

            save_col, delete_col = st.columns(2)
            with save_col:
                if st.button("💾 Salva spese fisse", use_container_width=True, key="save_spese_fisse"):
                    nome_nuova = nuova_spesa_nome.strip()
                    if nome_nuova:
                        editable_settings[nome_nuova] = float(nuova_spesa_importo)
                        editable_metadata[nome_nuova] = {
                            "Categoria": nuova_spesa_categoria,
                            "Carta": nuova_spesa_carta,
                            "Gruppo": nuova_spesa_gruppo,
                        }
                    if save_spese_fisse_settings(editable_settings, editable_metadata):
                        st.success("Spese fisse salvate")
                        st.rerun()
                    else:
                        st.error("Errore salvataggio spese fisse")
            with delete_col:
                if st.button("🗑️ Elimina spesa", use_container_width=True, key="delete_spesa_fissa", disabled=not bool(elimina_spesa)):
                    editable_settings.pop(elimina_spesa, None)
                    editable_metadata.pop(elimina_spesa, None)
                    if save_spese_fisse_settings(editable_settings, editable_metadata):
                        st.success("Spesa eliminata")
                        st.rerun()
                    else:
                        st.error("Errore eliminazione spesa")

        with tab_spese_fisse:
            st.subheader("Spese Fisse:")

            col_left, col_right = st.columns(LAYOUT_COLONNE["spese_fisse_lista"], gap="large")
            spese_meta = st.session_state.get("spese_fisse_metadata", {})
            rendered_voci = set()
            group_columns = [col_left, col_right]
            for group_index, group_name in enumerate(_ordered_spesa_fissa_groups(SPESE["Fisse"], spese_meta)):
                group_items = [
                    (voce, importo)
                    for voce, importo in SPESE["Fisse"].items()
                    if spese_meta.get(voce, {}).get("Gruppo", _infer_spesa_fissa_gruppo(voce)) == group_name
                ]
                if not group_items:
                    continue
                with group_columns[group_index % 2]:
                    if group_index > 1:
                        st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)
                    st.markdown(
                        f'<div style="font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:rgba(255,255,255,.46);margin:8px 0 3px;">{group_name}</div>',
                        unsafe_allow_html=True
                    )
                    for voce, importo in group_items:
                        categoria = spese_meta.get(voce, {}).get("Categoria", _infer_spesa_fissa_categoria(voce))
                        carta = spese_meta.get(voce, {}).get("Carta", _infer_spesa_fissa_carta(voce))
                        st.markdown(_spesa_fissa_row_html(voce, importo, categoria, carta), unsafe_allow_html=True)
                        rendered_voci.add(voce)

            altre_voci = [(voce, importo) for voce, importo in SPESE["Fisse"].items() if voce not in rendered_voci]
            if altre_voci:
                with col_right:
                    st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)
                    st.markdown(
                        '<div style="font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:rgba(255,255,255,.46);margin:8px 0 3px;">Altre</div>',
                        unsafe_allow_html=True
                    )
                    for voce, importo in altre_voci:
                        categoria = spese_meta.get(voce, {}).get("Categoria", _infer_spesa_fissa_categoria(voce))
                        carta = spese_meta.get(voce, {}).get("Carta", _infer_spesa_fissa_carta(voce))
                        st.markdown(_spesa_fissa_row_html(voce, importo, categoria, carta), unsafe_allow_html=True)

        st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)
        _sf = f"€{spese_fisse_totali:.2f}"
        _sfp = f"{(spese_fisse_totali)/stipendio*100:.1f}"
        _sfpo = f"{(spese_fisse_totali)/tot_stipendio*100:.1f}"
        _ri = f"€{risparmiabili:.2f}"
        _rip = f"{(risparmiabili)/stipendio*100:.1f}"
        _ripo = f"{(risparmiabili)/tot_stipendio*100:.1f}"
        st.markdown(f"""
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:0.5rem;">
            <div class="kpi-card">
                <div class="kpi-label">Totale Spese Fisse</div>
                <div class="kpi-value" style="color:#f87171;">{_sf}</div>
                <div style="font-size:11px;color:rgba(255,255,255,0.34);margin-top:3px;">{_sfp}% del budget mensile disponibile</div>
                <div style="font-size:11px;color:rgba(255,255,255,0.34);margin-top:3px;">{_sfpo}% delle entrate mensili totali</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Budget dopo spese fisse</div>
                <div class="kpi-value" style="color:#fef3c7;">{_ri}</div>
                <div style="font-size:11px;color:rgba(255,255,255,0.34);margin-top:3px;">{_rip}% del budget mensile disponibile</div>
                <div style="font-size:11px;color:rgba(255,255,255,0.34);margin-top:3px;">{_ripo}% delle entrate mensili totali</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        df_totale = pd.DataFrame({
            'Component': ['Spese Fisse', 'Budget dopo spese fisse', 'Risparmio Stipendi'],
            'Value': [spese_fisse_totali, risparmiabili, risparmio_stipendi]
        })
        df_utilizzare = pd.DataFrame({
            'Component': ['Spese Fisse', 'Budget dopo spese fisse'],
            'Value': [spese_fisse_totali, stipendio_utilizzare - spese_fisse_totali]
        })

        df_totale["Percentuale"] = (df_totale["Value"] / df_totale["Value"].sum()) * 100
        df_utilizzare["Percentuale"] = (df_utilizzare["Value"] / df_utilizzare["Value"].sum()) * 100

        # FIX 3: Entrate mensili totali donut - labels outside
        chart_totale = alt.Chart(df_totale).mark_arc(innerRadius=35, outerRadius=60).encode(
            theta=alt.Theta(field="Value", type="quantitative"),
            color=alt.Color(
                field="Component", type="nominal", 
                scale=alt.Scale(
                    domain=['Spese Fisse', 'Budget dopo spese fisse', 'Risparmio Stipendi'], 
                    range=['rgba(255, 100, 100, 0.3)', 'rgba(184, 192, 112, 0.3)', 'rgba(128, 128, 128, 0.3)']
                ),
                legend=None
            ),
            tooltip=[
                alt.Tooltip("Component:N", title="Categoria"),
                alt.Tooltip("Value:Q", title="Valore (€)", format=".2f"),
                alt.Tooltip("Percentuale:Q", title="Percentuale", format=".2f")
            ]
        ).properties(title="Entrate mensili totali", width=150, height=150)

        # Filter zero/negative values to avoid broken donuts
        df_totale_clean = df_totale[df_totale["Value"] > 0].copy()
        df_utilizzare_clean = df_utilizzare[df_utilizzare["Value"] > 0].copy()

        chart_totale_clean = alt.Chart(df_totale_clean).mark_arc(innerRadius=40, outerRadius=70).encode(
            theta=alt.Theta(field="Value", type="quantitative"),
            color=alt.Color(
                field="Component", type="nominal",
                scale=alt.Scale(
                    domain=['Spese Fisse', 'Budget dopo spese fisse', 'Risparmio Stipendi'],
                    range=['#FF6464', '#fef3c7', '#888888']
                ),
                legend=alt.Legend(
                    title=None, orient='bottom', direction='vertical',
                    labelColor='rgba(255,255,255,0.65)', labelFontSize=10,
                    symbolSize=60, padding=4
                )
            ),
            tooltip=[
                alt.Tooltip("Component:N", title="Categoria"),
                alt.Tooltip("Value:Q", title="Valore (€)", format=".2f"),
                alt.Tooltip("Percentuale:Q", title="% sulle entrate", format=".1f")
            ]
        ).properties(
            title=alt.TitleParams(
                "Entrate mensili totali",
                anchor='middle',   # <-- centra il titolo
                color='rgba(255,255,255,0.7)',
                fontSize=12
            ),
            width=160,
            height=160
        )

        chart_utilizzare_clean = alt.Chart(df_utilizzare_clean).mark_arc(innerRadius=40, outerRadius=70).encode(
            theta=alt.Theta(field="Value", type="quantitative"),
            color=alt.Color(
                field="Component", type="nominal",
                scale=alt.Scale(domain=['Spese Fisse', 'Budget dopo spese fisse'], range=['#FF6961', '#fef3c7']),
                legend=alt.Legend(
                    title=None, orient='bottom', direction='vertical',
                    labelColor='rgba(255,255,255,0.65)', labelFontSize=10,
                    symbolSize=60, padding=4
                )
            ),
            tooltip=[
                alt.Tooltip("Component:N", title="Categoria"),
                alt.Tooltip("Value:Q", title="Valore (€)", format=".2f"),
                alt.Tooltip("Percentuale:Q", title="% sul budget", format=".1f")
            ]
        ).properties(
            title=alt.TitleParams(
                "Budget mensile disponibile",
                anchor='middle',   # <-- centra il titolo
                color='rgba(255,255,255,0.7)',
                fontSize=12
            ),
            width=160,
            height=160
        )


        chart_donut = (chart_totale_clean | chart_utilizzare_clean).resolve_scale(color='independent')

        st.markdown('<div style="height: 20px;"></div>', unsafe_allow_html=True)
        st.markdown("**💶 Distribuzione entrate e budget:**")
        st.altair_chart(chart_donut, use_container_width=True)

    # --- COLONNA 2: SPESE VARIABILI ---
    with col2:
        col2_left, col2_right = st.columns(LAYOUT_COLONNE["centrale_variabili_altre"], gap="large")
        with col2_left:
            st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)
            st.markdown('<div class="section-pill">💸 Spese Variabili</div>', unsafe_allow_html=True)
            st.subheader("Spese Variabili:")
    
            da_spendere = 0
            spese_quotidiane = 0
            spese_variabili_totali = SPESE["Variabili"]["Emergenze/Compleanni"] + SPESE["Variabili"]["Viaggi"] + SPESE["Variabili"]["Da spendere"] + SPESE["Variabili"]["Spese quotidiane"]
    
            risparmio_stipendi = stipendio_originale - stipendio_scelto
            risparmio_da_spendere = 0
            risparmio_spese_quotidiane = 0

            spese_emergenze_viaggi = SPESE["Variabili"]["Emergenze/Compleanni"] + SPESE["Variabili"]["Viaggi"]
            risparmiabili_dopo_emergenze_viaggi = risparmiabili - spese_emergenze_viaggi

            variabili_quote_col, variabili_budget_col = st.columns(LAYOUT_COLONNE["variabili_quote_budget"], gap="large")
            with variabili_quote_col:
                st.markdown(
                    '<div style="font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:rgba(255,255,255,.46);margin:4px 0 4px;">Quote fisse</div>',
                    unsafe_allow_html=True
                )
                percentuale_emergenze = percentuali_variabili.get("Emergenze/Compleanni", 0) * 100
                st.markdown(
                    _spesa_variabile_row_html(
                        "Emergenze/Compleanni",
                        SPESE["Variabili"]["Emergenze/Compleanni"],
                        "#4ADE80",
                        f"{percentuale_emergenze:.2f}% del budget dopo spese fisse"
                    ),
                    unsafe_allow_html=True
                )
                percentuale_viaggi = percentuali_variabili.get("Viaggi", 0) * 100
                st.markdown(
                    _spesa_variabile_row_html(
                        "Viaggi",
                        SPESE["Variabili"]["Viaggi"],
                        "#166534",
                        f"{percentuale_viaggi:.2f}% del budget dopo spese fisse"
                    ),
                    unsafe_allow_html=True
                )

            with variabili_budget_col:
                st.markdown(
                    '<div style="font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:rgba(255,255,255,.46);margin:4px 0 4px;">Dopo le quote</div>',
                    unsafe_allow_html=True
                )
                pct_rimanente = (da_spendere_senza_limite * 100 / risparmiabili_dopo_emergenze_viaggi) if risparmiabili_dopo_emergenze_viaggi != 0 else 0
                st.markdown(
                    _spesa_variabile_row_html(
                        "Da spendere",
                        SPESE["Variabili"]["Da spendere"],
                        "#FACC15",
                        f"{pct_rimanente:.2f}% del rimanente €{risparmiabili_dopo_emergenze_viaggi:.2f}, limite €{limite_da_spendere:.2f}"
                    ),
                    unsafe_allow_html=True
                )
                da_spendere = min(da_spendere_senza_limite, limite_da_spendere)
                risparmio_da_spendere = da_spendere_senza_limite - da_spendere
                st.markdown(
                    f'<div style="font-size:12px;color:rgba(255,255,255,.36);margin:-4px 0 7px 10px;">reale €{da_spendere_senza_limite:.2f} · risparmiati €{risparmio_da_spendere:.2f}</div>',
                    unsafe_allow_html=True
                )
                st.markdown(
                    _spesa_variabile_row_html(
                        "Spese quotidiane",
                        SPESE["Variabili"]["Spese quotidiane"],
                        "#FB923C",
                        f"rimanente, con limite a €{max_spese_quotidiane:.2f}"
                    ),
                    unsafe_allow_html=True
                )
                spese_quotidiane = min(spese_quotidiane_senza_limite, max_spese_quotidiane)
                risparmio_spese_quotidiane = spese_quotidiane_senza_limite - spese_quotidiane
                st.markdown(
                    f'<div style="font-size:12px;color:rgba(255,255,255,.36);margin:-4px 0 7px 10px;">reale €{spese_quotidiane_senza_limite:.2f} · risparmiati €{risparmio_spese_quotidiane:.2f}</div>',
                    unsafe_allow_html=True
                )
    
    
            st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)
            col_spese_variabili_1, col_spese_variabili_2 = st.columns(LAYOUT_COLONNE["variabili_kpi_grafico"], gap="medium")
            with col_spese_variabili_1:
                _sv = f"€{spese_variabili_totali:.2f}"
                _sv_st_risp = f"€{spese_variabili_totali/risparmiabili*100:.1f}"
                _sv_st_util = f"€{spese_variabili_totali/stipendio_utilizzare*100:.1f}"
                _sv_st_tot = f"€{spese_variabili_totali/stipendio_totale*100:.2f}"
                st.markdown(f"""
                <div class="kpi-card">
                    <div class="kpi-label">Totale Spese Variabili</div>
                    <div class="kpi-value" style="color:#fde047;">{_sv}</div>
                    <div style="font-size:11px;color:rgba(255,255,255,0.34);margin-top:3px;">{_sv_st_risp}% del budget dopo spese fisse</div>
                    <div style="font-size:11px;color:rgba(255,255,255,0.34);margin-top:3px;">{_sv_st_util}% del budget mensile disponibile</div>
                    <div style="font-size:11px;color:rgba(255,255,255,0.34);margin-top:3px;">{_sv_st_tot}% delle entrate mensili totali</div>
                </div>
                """, unsafe_allow_html=True)

                st.markdown('<div style="height: 20px;"></div>', unsafe_allow_html=True)

                progresso_altre_entrate = spese_variabili_totali / risparmiabili if risparmiabili > 0 else 0
                progresso_altre_entrate = min(progresso_altre_entrate, 1.0)
                st.progress(progresso_altre_entrate)
                st.markdown(f"""
                <div style="font-size:12px; color:rgba(255,255,255,0.44); margin-top:5px;">
                Spese variabili rispetto al budget dopo spese fisse: €{spese_variabili_totali:,.2f} / €{risparmiabili:,.2f}
                </div>
                """, unsafe_allow_html=True)

                st.markdown('<div style="height: 20px;"></div>', unsafe_allow_html=True)
    
            with col_spese_variabili_2:
                # Creo il DataFrame per il grafico delle spese variabili
                df_spese_variabili = pd.DataFrame({
                    'Voce': ['Emergenze/Compleanni', 'Viaggi', 'Da spendere', 'Spese quotidiane'],
                    'Value': [
                        SPESE["Variabili"]["Emergenze/Compleanni"],
                        SPESE["Variabili"]["Viaggi"],
                        SPESE["Variabili"]["Da spendere"],
                        SPESE["Variabili"]["Spese quotidiane"]
                    ]
                })
                
                # Solo voci con importo > 0
                df_spese_variabili = df_spese_variabili[df_spese_variabili["Value"] > 0].copy()
                
                # Calcolo le percentuali relative alle spese variabili
                totale_spese = df_spese_variabili["Value"].sum()
                df_spese_variabili["Percentuale"] = (df_spese_variabili["Value"] / totale_spese * 100).round(1) if totale_spese != 0 else 0
                
                # Creazione del grafico
                if not df_spese_variabili.empty:
                    chart_spese_variabili = alt.Chart(df_spese_variabili).mark_arc(
                        innerRadius=40, outerRadius=70
                    ).encode(
                        theta=alt.Theta(field="Value", type="quantitative"),
                        color=alt.Color(
                            field="Voce", type="nominal",
                            scale=alt.Scale(
                                domain=['Emergenze/Compleanni', 'Viaggi', 'Da spendere', 'Spese quotidiane'],
                                range=['#4ADE80', '#166534', '#FACC15', '#FB923C']
                            ),
                            legend=alt.Legend(
                                title=None,
                                orient='right',
                                direction='vertical',
                                labelColor='rgba(255,255,255,0.65)',
                                labelFontSize=11,
                                symbolSize=40,
                                padding=2,
                                offset=5
                            )
                        ),
                        tooltip=[
                            alt.Tooltip('Voce:N', title='Voce'),
                            alt.Tooltip('Value:Q', title='Importo (€)', format='.2f'),
                            alt.Tooltip('Percentuale:Q', title='Percentuale', format='.1f')
                        ]
                    ).properties(
                        title="💸 Distribuzione Spese Variabili",
                        width=200,
                        height=220
                    ).configure_title(
                        anchor='middle'
                    ).configure_view(
                        strokeWidth=0,
                        fill='transparent'
                    )
                
                    st.altair_chart(chart_spese_variabili, use_container_width=True)
        # --- RISPARMIATI DEL MESE --- Full width after col1, col2, col3
        st.markdown('<div style="height:12px;"></div>', unsafe_allow_html=True)
    
        # Recalculate risparmi variables for this section
        risparmi_mensili_calc = stipendio_originale - stipendio_scelto
        percentuali_variabili_calc = {"Emergenze/Compleanni": emergenze_compleanni, "Viaggi": viaggi}
        for voce, percentuale in percentuali_variabili_calc.items():
            SPESE["Variabili"][voce] = percentuale * risparmiabili
        da_spendere_senza_limite_calc = percentuale_limite_da_spendere * (risparmiabili - sum(percentuali_variabili_calc.values()) * risparmiabili)
        SPESE["Variabili"]["Da spendere"] = min(da_spendere_senza_limite_calc, limite_da_spendere)
        spese_quotidiane_senza_limite_calc = risparmiabili - sum(percentuali_variabili_calc.values()) * risparmiabili - da_spendere_senza_limite_calc
        SPESE["Variabili"]["Spese quotidiane"] = min(spese_quotidiane_senza_limite_calc, max_spese_quotidiane)
        if spese_quotidiane_senza_limite_calc > max_spese_quotidiane:
            risparmi_mensili_calc += spese_quotidiane_senza_limite_calc - max_spese_quotidiane
        if da_spendere_senza_limite_calc > limite_da_spendere:
            risparmi_mensili_calc += da_spendere_senza_limite_calc - limite_da_spendere
        risparmi_mensili_calc += risparmi_mese_precedente
        risparmio_stipendi_calc = stipendio_originale - stipendio_scelto
        risparmio_da_spendere_calc = da_spendere_senza_limite_calc - min(da_spendere_senza_limite_calc, limite_da_spendere) if da_spendere_senza_limite_calc > limite_da_spendere else 0
        risparmio_spese_quotidiane_calc = spese_quotidiane_senza_limite_calc - min(spese_quotidiane_senza_limite_calc, max_spese_quotidiane) if spese_quotidiane_senza_limite_calc > max_spese_quotidiane else 0



    # --- COLONNA 3: ALTRE ENTRATE ---
        with col2_right:
            st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)
            tab_altre_view, tab_altre_decisioni = st.tabs(["➕ Altre Entrate", "⚙️ Decisioni"])

            with tab_altre_decisioni:
                altre_settings = ALTRE_ENTRATE.copy()
                editor_cols = st.columns(2)
                edited_altre = {}
                for idx, (voce, importo) in enumerate(altre_settings.items()):
                    with editor_cols[idx % 2]:
                        st.markdown(
                            f'<div style="font-size:15px;font-weight:800;color:rgba(255,255,255,.92);margin:0 0 6px;">{html.escape(str(voce))}</div>',
                            unsafe_allow_html=True
                        )
                        edited_altre[voce] = st.number_input(
                            voce,
                            min_value=0.0,
                            value=float(importo),
                            step=10.0,
                            key=f"altra_entrata_{voce}",
                            label_visibility="collapsed"
                        )
                new_col1, new_col2 = st.columns(LAYOUT_COLONNE["form_nome_importo"])
                with new_col1:
                    nuova_voce = st.text_input("Nuova entrata", key="nuova_altra_entrata_nome")
                with new_col2:
                    nuovo_importo = st.number_input("Importo", min_value=0.0, value=0.0, step=10.0, key="nuova_altra_entrata_importo")
                if nuova_voce.strip():
                    edited_altre[nuova_voce.strip()] = float(nuovo_importo)

                elimina_entrata = st.selectbox("Entrata da eliminare", [""] + list(altre_settings.keys()), key="elimina_altra_entrata")
                save_altre_col, delete_altre_col = st.columns(2)
                with save_altre_col:
                    if st.button("💾 Salva altre entrate", use_container_width=True, key="save_altre_entrate"):
                        if save_altre_entrate_settings(edited_altre):
                            st.success("Altre entrate salvate")
                            st.rerun()
                        else:
                            st.error("Errore salvataggio altre entrate")
                with delete_altre_col:
                    if st.button("🗑️ Elimina entrata", use_container_width=True, key="delete_altra_entrata", disabled=not bool(elimina_entrata)):
                        edited_altre.pop(elimina_entrata, None)
                        if save_altre_entrate_settings(edited_altre):
                            st.success("Entrata eliminata")
                            st.rerun()
                        else:
                            st.error("Errore eliminazione entrata")

            with tab_altre_view:
                col_altre_entrate_sx, col_altre_entrate_dx = st.columns(LAYOUT_COLONNE["altre_entrate_obiettivo"], gap="medium")
                totale_altre = sum(ALTRE_ENTRATE.values())
                _ae = f"€{totale_altre:.2f}"

                with col_altre_entrate_sx:
                    st.markdown('<div class="section-pill">➕ Altre Entrate</div>', unsafe_allow_html=True)
                    st.subheader("Altre Entrate:")
                    altre_entrate_colori = {
                        "Macchina (Mamma)": "#E6C48C",
                        "Altro": "#89CFF0",
                        "2° Entr. dal mese prec.": "#D8BFD8",
                    }
                    for voce, importo in ALTRE_ENTRATE.items():
                        colore = altre_entrate_colori.get(voce, "#34d399")
                        peso = (importo / totale_altre * 100) if totale_altre else 0
                        st.markdown(
                            _money_row_html(voce, importo, colore, triangolino_verde_BNL, f"{peso:.1f}% delle altre entrate"),
                            unsafe_allow_html=True
                        )

                with col_altre_entrate_dx:
                    totale_entrate_target = stipendio_originale / totale_entrate_target_oltre_lo_stipendio
                    altre_entrate_target = totale_entrate_target - stipendio_originale
                    progresso = totale_altre / altre_entrate_target if altre_entrate_target > 0 else 0
                    progresso = min(progresso, 1.0)

                    st.markdown("### 🎯 Obiettivo Entrate")
                    percentuale_stip = stipendio_originale / totale_entrate_target * 100
                    st.markdown(f"""
                    <div style="margin:4px 0 10px;line-height:1.25;">
                        <div style="font-size:12px;color:rgba(255,255,255,.44);">Entrate totali desiderate</div>
                        <div style="font-size:19px;font-weight:600;color:rgba(255,255,255,.9);">€{totale_entrate_target:,.2f}</div>
                        <div style="font-size:12px;color:rgba(255,255,255,.42);">Stipendio = {percentuale_stip:.0f}% delle entrate totali</div>
                    </div>
                    """, unsafe_allow_html=True)

                    st.markdown(f"""
                    <div style="margin:4px 0 10px;line-height:1.25;">
                        <div style="font-size:12px;color:rgba(255,255,255,.44);">Altre entrate target</div>
                        <div style="font-size:19px;font-weight:600;color:#8fe28f;">€{altre_entrate_target:,.2f}</div>
                    </div>
                    """, unsafe_allow_html=True)

                    st.markdown("<div style='margin-top:15px'></div>", unsafe_allow_html=True)
                    st.progress(progresso)
                    st.markdown(f"""
                    <div style="font-size:12px; color:rgba(255,255,255,0.44); margin-top:5px;">
                    Attuale: €{totale_altre:,.2f} / €{altre_entrate_target:,.2f}
                    </div>
                    """, unsafe_allow_html=True)

                st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)
                col_altre_entrate_1, col_altre_entrate_2 = st.columns(LAYOUT_COLONNE["altre_entrate_kpi_grafico"], gap="medium")
                percentuale_altre_su_totale_altre = totale_altre / altre_entrate_target if altre_entrate_target else 0
                _ae_ipot = f"{percentuale_altre_su_totale_altre * 100:.2f}"
                with col_altre_entrate_1:
                    st.markdown(f"""
                    <div class="kpi-card" style="border-color:rgba(52,211,153,0.2);">
                        <div class="kpi-label">Totale Altre Entrate</div>
                        <div class="kpi-value" style="color:#77DD77;">{_ae}</div>
                        <div style="font-size:11px;color:rgba(255,255,255,0.34);margin-top:3px;">{_ae_ipot}% di Obiettivo Entrate</div>
                    </div>
                    """, unsafe_allow_html=True)
                    st.markdown('<div style="height: 20px;"></div>', unsafe_allow_html=True)

                with col_altre_entrate_2:
                    df_altre_entrate = pd.DataFrame({
                        'Voce': list(ALTRE_ENTRATE.keys()),
                        'Value': list(ALTRE_ENTRATE.values())
                    })
                    df_altre_entrate = df_altre_entrate[df_altre_entrate["Value"] > 0].copy()
                    totale_entrate = df_altre_entrate["Value"].sum()
                    df_altre_entrate["Percentuale"] = (df_altre_entrate["Value"] / totale_entrate * 100).round(1) if totale_entrate != 0 else 0

                    if not df_altre_entrate.empty:
                        palette = ['#E6C48C', '#D8BFD8', '#89CFF0', '#A78BFA', '#34d399', '#fb923c', '#60a5fa']
                        chart_altre_entrate = alt.Chart(df_altre_entrate).mark_arc(
                            innerRadius=32, outerRadius=56
                        ).encode(
                            theta=alt.Theta(field="Value", type="quantitative"),
                            color=alt.Color(
                                field="Voce", type="nominal",
                                scale=alt.Scale(domain=list(ALTRE_ENTRATE.keys()), range=palette[:len(ALTRE_ENTRATE)]),
                                legend=alt.Legend(
                                    title=None,
                                    orient='right',
                                    direction='vertical',
                                    labelColor='rgba(255,255,255,0.65)',
                                    labelFontSize=11,
                                    symbolSize=40,
                                    padding=2,
                                    offset=5
                                )
                            ),
                            tooltip=[
                                alt.Tooltip('Voce:N', title='Voce'),
                                alt.Tooltip('Value:Q', title='Importo (€)', format='.2f'),
                                alt.Tooltip('Percentuale:Q', title='Percentuale', format='.1f')
                            ]
                        ).properties(
                            title="➕ Distribuzione Altre Entrate",
                            width=150,
                            height=170
                        ).configure_title(
                            anchor='middle'
                        ).configure_view(
                            strokeWidth=0,
                            fill='transparent'
                        )
                        st.altair_chart(chart_altre_entrate, use_container_width=True)

        # Visualizzazione grafici
        col_center_pill = st.columns(LAYOUT_COLONNE["titolo_dashboard"])[1]
        with col_center_pill:
            st.markdown('<div class="section-pill">🏠 Spese Fisse</div>',unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            
        col_vuoto_a, col1_1, col1_2, col_vuoto_b= st.columns(LAYOUT_COLONNE["dettaglio_spese_fisse"])
        with col1_1:
            st.altair_chart(chart_fisse, use_container_width=True)
            st.markdown(f'<span style="font-size:10pt;">Totale spese fisse:</span> <span style="color:#f87171">{_sf}</span>', unsafe_allow_html=True)


#####################################################################################################################################################################################################################################################################################
            # 📊 Costruzione barra segmentata per CATEGORIE (come il donut)

            totale = df_fisse["Importo"].sum()
            
            barra_html = '<div style="display:flex;width:100%;height:22px;border-radius:999px;overflow:hidden;margin-top:10px;background:#222;padding:2px;">'
            
            for _, row in df_fisse.iterrows():
                categoria = row["Categoria"].strip()
                valore = row["Importo"]
                perc = (valore / totale) * 100 if totale > 0 else 0
                colore = color_map.get(categoria, "#999999")
            
                barra_html += f'<div title="{categoria}: €{valore:.2f}" style="width:{perc}%;background:{colore};"></div>'
            
            barra_html += '</div>'
            
            st.markdown(barra_html, unsafe_allow_html=True)

            for _, row in df_fisse.iterrows():
                categoria = row["Categoria"].strip()
                valore = row["Importo"]
                perc = (valore / totale) * 100 if totale > 0 else 0
                colore = color_map.get(categoria, "#999999")
            
                barra_html += (
                    f'<div title="{categoria}: €{valore:.2f}" '
                    f'style="width:{perc}%;background:{colore};"></div>'
                )
#####################################################################################################################################################################################################################################################################################


        with col1_2:
            st.subheader("Dettaglio Spese Fisse:")
            dettaglio_df = df_fisse.copy()
            dettaglio_df["PctTotale"] = dettaglio_df["Importo"].apply(lambda x: (x / stipendio_totale * 100) if stipendio_totale else 0)
            dettaglio_df["PctScelto"] = dettaglio_df["Importo"].apply(lambda x: (x / stipendio_utilizzare * 100) if stipendio_utilizzare else 0)
            dettaglio_rows = []
            for _, row in dettaglio_df.sort_values("Importo", ascending=False).iterrows():
                categoria = str(row["Categoria"])
                valore = float(row["Importo"])
                colore = color_map.get(categoria, "#999999")
                dettaglio_rows.append(f"""
                <div style="display:grid;grid-template-columns:6px 1.15fr .72fr .58fr .58fr;gap:8px;align-items:center;
                            padding:7px 9px;margin:5px 0;border-radius:8px;
                            background:rgba(255,255,255,.045);border:0.5px solid rgba(255,255,255,.08);">
                    <div style="height:100%;min-height:24px;border-radius:999px;background:{colore};"></div>
                    <div style="font-size:12px;font-weight:600;color:{colore};">{categoria}</div>
                    <div style="font-size:12px;color:rgba(255,255,255,.84);font-family:DM Mono, monospace;text-align:right;">€{valore:.2f}</div>
                    <div style="font-size:11px;color:rgba(255,255,255,.50);text-align:right;">{row["PctTotale"]:.1f}% entr.</div>
                    <div style="font-size:11px;color:rgba(255,255,255,.50);text-align:right;">{row["PctScelto"]:.1f}% budg.</div>
                </div>
                """)
            st.markdown("".join(dettaglio_rows), unsafe_allow_html=True)
    
        with col_vuoto_b:
            note_wrap_left, note_wrap, note_wrap_right = st.columns([0.02, 0.96, 0.02], gap="small")
            with note_wrap:
                st.markdown('<div class="section-pill">📝 Promemoria</div>', unsafe_allow_html=True)

                def _memo_card(label, value):
                    raw_text = str(value or "").strip()
                    if raw_text:
                        preview = raw_text if len(raw_text) <= 230 else raw_text[:227].rstrip() + "..."
                        preview_html = html.escape(preview)
                    else:
                        preview_html = '<span class="memo-card-empty">Nessun promemoria scritto.</span>'
                    return f"""
                    <div class="memo-card">
                        <div class="memo-card-title">{html.escape(label)}</div>
                        <div class="memo-card-preview">{preview_html}</div>
                    </div>
                    """

                n1, n2 = st.columns(2, gap="small")
                with n1:
                    st.markdown(_memo_card("Promemoria 1", _nota_value("nota1")), unsafe_allow_html=True)
                    with st.popover("Apri / modifica 1", use_container_width=True):
                        nota1 = st.text_area("Promemoria 1", value=_nota_value("nota1"), height=420, label_visibility="collapsed", key="nota1_text")
                with n2:
                    st.markdown(_memo_card("Promemoria 2", _nota_value("nota2")), unsafe_allow_html=True)
                    with st.popover("Apri / modifica 2", use_container_width=True):
                        nota2 = st.text_area("Promemoria 2", value=_nota_value("nota2"), height=420, label_visibility="collapsed", key="nota2_text")
                n3, n4 = st.columns(2, gap="small")
                with n3:
                    st.markdown(_memo_card("Promemoria 3", _nota_value("nota3")), unsafe_allow_html=True)
                    with st.popover("Apri / modifica 3", use_container_width=True):
                        nota3 = st.text_area("Promemoria 3", value=_nota_value("nota3"), height=420, label_visibility="collapsed", key="nota3_text")
                with n4:
                    st.markdown(_memo_card("Promemoria 4", _nota_value("nota4")), unsafe_allow_html=True)
                    with st.popover("Apri / modifica 4", use_container_width=True):
                        nota4 = st.text_area("Promemoria 4", value=_nota_value("nota4"), height=420, label_visibility="collapsed", key="nota4_text")

                salva = st.button(
                    "💾 Salva note",
                    use_container_width=True,
                    key="save_note_promemoria",
                    disabled=not st.session_state.get("note_loaded_from_sheet", True)
                )
                if salva:
                    note_values = [nota1, nota2, nota3, nota4]
                    all_notes_empty = all(not str(value).strip() for value in note_values)
                    previous_values = [
                        _nota_value("nota1"),
                        _nota_value("nota2"),
                        _nota_value("nota3"),
                        _nota_value("nota4"),
                    ]
                    previous_had_content = any(value.strip() for value in previous_values)
                    if all_notes_empty and previous_had_content:
                        st.error("Salvataggio bloccato: stai per sovrascrivere note esistenti con campi vuoti.")
                        st.stop()
                    df_note = _build_note_df(nota1, nota2, nota3, nota4, risparmio_desiderato_corrente)
                    if save_data_gsheets(worksheet_name, NOTE_HEADERS, df_note):
                        st.session_state.note_df_draft = df_note.copy()
                        st.session_state.note_loaded_from_sheet = True
                        st.success("Note salvate")
                    else:
                        st.error("Errore salvataggio")
                

    with col3:
        col3_left, col3_right = st.columns(LAYOUT_COLONNE["destra_risparmi_carte"], gap="medium")
        with col3_left:
            st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)
            st.markdown('<div class="section-pill">💰 Risparmi del Mese</div>', unsafe_allow_html=True)
            st.subheader("Risparmiati del mese:")
        
            kpi_val = f"€{risparmi_mensili_calc:.2f}"
            kpi_pct = f"{risparmi_mensili_calc/stipendio_utilizzare*100:.1f}"
            kpi_pctot = f"{risparmi_mensili_calc/stipendio_totale*100:.1f}"
        
            # valori già calcolati
            v1 = risparmio_stipendi_calc
            v2 = risparmi_mese_precedente
            v3 = risparmio_da_spendere_calc
            v4 = risparmio_spese_quotidiane_calc
            
            html_risparmi = ""
            html_risparmi += _money_row_html("Dal budget non usato", v1, "#9ca3af", triangolino_verde_BNL, "differenza tra stipendio percepito e quota stipendio scelta")
            html_risparmi += _money_row_html("Dal Mese Precedente", v2, "#60a5fa", triangolino_verde_BNL, "risparmio riportato nel mese corrente")
            html_risparmi += _money_row_html("Dai 'Da Spendere'", v3, "#fde047", triangolino_verde_BNL, "differenza non usata sul budget da spendere")
            html_risparmi += _money_row_html("Dalle 'Spese Quotidiane'", v4, "#FB923C", triangolino_verde_BNL, "differenza non usata sulle spese quotidiane")
            st.markdown(html_risparmi, unsafe_allow_html=True)
            st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)
            
            col_risparmi_1, col_risparmi_2 = st.columns(LAYOUT_COLONNE["risparmi_kpi_grafico"], gap="small")
            with col_risparmi_1:
                st.markdown(f"""
                <div class="kpi-card" style="border-color:rgba(52,211,153,0.25);">
                    <div class="kpi-label">Tot. Risparmiato</div>
                    <div class="kpi-value" style="color:#34d399;">{kpi_val}</div>
                    <div style="font-size:11px;color:rgba(255,255,255,0.34);margin-top:3px;">{kpi_pct}% del budget mensile disponibile</div>
                    <div style="font-size:11px;color:rgba(255,255,255,0.34);margin-top:3px;">{kpi_pctot}% delle entrate mensili totali</div>
                </div>
                """, unsafe_allow_html=True)

        
                st.markdown('<div style="height: 20px;"></div>', unsafe_allow_html=True)
                st.markdown('<div style="height: 10px;"></div>', unsafe_allow_html=True)
                savings_vals = [risparmio_stipendi_calc, risparmi_mese_precedente, risparmio_da_spendere_calc, risparmio_spese_quotidiane_calc]
                non_saved_calc = max(0, (stipendio_originale + sum(ALTRE_ENTRATE.values())) - sum(savings_vals))
                df_savings_raw = pd.DataFrame({
                    'Component': ['Da Stipendi', 'Da Mese Prec.', 'Da Spendere', 'Quotidiane'],
                    'Value': [risparmio_stipendi_calc, risparmi_mese_precedente, risparmio_da_spendere_calc, risparmio_spese_quotidiane_calc]
                })
                df_savings = df_savings_raw[df_savings_raw["Value"] > 0].copy()
                totale = df_savings["Value"].sum()
                if totale != 0:
                    df_savings["Percentuale"] = (df_savings["Value"] / totale * 100).round(1)
                else:
                    df_savings["Percentuale"] = 0
                
            with col_risparmi_2:
                if not df_savings.empty:
                    chart_savings_arc = alt.Chart(df_savings).mark_arc(innerRadius=32, outerRadius=56).encode(
                        theta=alt.Theta(field="Value", type="quantitative"),
                        color=alt.Color(
                            field="Component", type="nominal",
                            scale=alt.Scale(
                                domain=['Da Stipendi', 'Da Mese Prec.', 'Da Spendere', 'Quotidiane'],
                                range=['#9ca3af', '#60a5fa', '#fde047', '#FB923C']
                            ),
                            legend=alt.Legend(
                                title=None,
                                orient='right',
                                direction='vertical',
                                labelColor='rgba(255,255,255,0.65)',
                                labelFontSize=11,
                                symbolSize=40,
                                padding=2,
                                offset=5  # 👈 distanza dal grafico (chiave!)
                            )
                        ),
                        tooltip=[
                            alt.Tooltip('Component:N', title='Risparmi'),
                            alt.Tooltip('Value:Q', title='Totale (€)', format='.2f'),
                            alt.Tooltip("Percentuale:Q", title="%", format=".1f")
                        ]
                    ).properties(
                        title="💰 Distribuzione Risparmi",
                        width=150,
                        height=170
                    ).configure_title(
                        anchor='middle'
                    ).configure_view(
                        strokeWidth=0,
                        fill='transparent'
                    )
                
                    # mantiene colori indipendenti se hai più chart simili
                    chart_donut_Distribuzione_Risparmi = chart_savings_arc.resolve_scale(color='independent')
                    st.altair_chart(chart_donut_Distribuzione_Risparmi, use_container_width=True)
    


                            
        with col3_right:
            st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)
            st.markdown('<div class="section-pill">💳 Trasferimenti Carte</div>', unsafe_allow_html=True)
            st.subheader("Trasferimenti sulle Carte:")
        
            for carta in ["ING", "Revolut", "BNL"]:
                spese_carta = {voce: SPESE["Fisse"].get(voce, 0) + SPESE["Variabili"].get(voce, 0) 
                               for voce in SPESE[carta]}
                spese_carta = {voce: importo for voce, importo in spese_carta.items() if importo != 0}
                if carta == "Revolut":
                    totale_carta = revolut_expenses  # Usa il valore modificato per Revolut
                    colore = "#89CFF0"  # Azzurro
                    testo = "trasferire"
                    somma_spese_programmate_immediate = SPESE["Fisse"]["Psicologo"] + SPESE["Fisse"]["Sport"] + SPESE["Fisse"]["Cane"] + SPESE["Fisse"]["Trasporti"] + SPESE["Fisse"]["Bollette"] + SPESE["Fisse"]["Beneficienza"] + SPESE["Fisse"]["Pulizia Casa"] + SPESE["Fisse"]["Disney+"] + SPESE["Fisse"]["Netflix"] + SPESE["Fisse"]["Spotify"]
                    spese_che_anticipo_per_un_giorno_di_disney_spotify=18
                    somma_valori = risparmi_mese_precedente - somma_spese_programmate_immediate - spese_che_anticipo_per_un_giorno_di_disney_spotify + totale_carta
                    st.markdown(
                        _money_row_html(
                            f"Da {testo} su {carta}",
                            totale_carta,
                            colore,
                            _triangle_for_card(carta),
                            f"+ €{risparmi_mese_precedente:.2f} dai risparmi - (€{somma_spese_programmate_immediate:.2f} - €{spese_che_anticipo_per_un_giorno_di_disney_spotify:.2f}) -> vedrai €{somma_valori:.2f}"
                        ),
                        unsafe_allow_html=True
                    )
                else:
                    totale_carta = sum(spese_carta.values())
                    if carta == "ING":
                        colore = "#D2691E"
                        testo = "trasferire"
                    elif carta == "BNL":
                        colore = "green"
                        colore2 = "#77DD77"
                        testo = "mantenere"
                        testo2 = "risparmiato"
                    st.markdown(
                        _money_row_html(
                            f"Da {testo} su {carta}",
                            totale_carta,
                            colore,
                            _triangle_for_card(carta),
                            "totale delle spese previste su questa carta"
                        ),
                        unsafe_allow_html=True
                    )
            st.markdown(
                _money_row_html(
                    f"Totale {testo2} su {carta}",
                    risparmi_mensili,
                    colore2,
                    _triangle_for_card(carta),
                    "quota da lasciare come risparmio"
                ),
                unsafe_allow_html=True
            )
    
            # FIX 4: NEW "Carte" donut chart
            st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)
            with st.container():  
                # Calculate totals per card
                ing_total = sum(SPESE["Fisse"].get(v, 0) + SPESE["Variabili"].get(v, 0) for v in SPESE["ING"])
                revolut_total = revolut_expenses + risparmi_mese_precedente  # original before subtraction
                bnl_total = sum(SPESE["Fisse"].get(v, 0) + SPESE["Variabili"].get(v, 0) for v in SPESE["BNL"])
        
                df_carte = pd.DataFrame({
                    'Carta': ['ING', 'Revolut', 'BNL', 'Risparmiato BNL'],
                    'Totale': [ing_total, revolut_total, bnl_total, risparmi_mensili]
                        })
                df_carte['Percentuale'] = (df_carte['Totale'] / df_carte['Totale'].sum() * 100).round(1)
        
                carte_arc = alt.Chart(df_carte).mark_arc(innerRadius=32, outerRadius=56).encode(
                theta=alt.Theta(field="Totale", type="quantitative"),
                color=alt.Color(
                    field="Carta", type="nominal",
                    scale=alt.Scale(
                        domain=['ING', 'Revolut', 'BNL', 'Risparmiato BNL'],
                        range=['#D2691E', '#89CFF0', '#2E7D32', '#66BB6A']
                    ),
                    legend=alt.Legend(
                        title=None,
                        orient='right',
                        direction='vertical',
                        labelColor='rgba(255,255,255,0.65)',
                        labelFontSize=11,
                        symbolSize=40,
                        padding=2,
                        offset=5  # 👈 distanza dal grafico (chiave!)
                    )
        
                ),
                tooltip=[
                    alt.Tooltip("Carta:N", title="Carta"),
                    alt.Tooltip("Totale:Q", title="Totale (€)", format=".2f"),
                    alt.Tooltip("Percentuale:Q", title="%", format=".1f")
                ]
                ).properties(
                    title="💳 Distribuzione Carte",
                    width=150,
                    height=170,
                ).configure_title(
                    anchor='middle'
                ).configure_view(
                    strokeWidth=0,
                    fill='transparent',
                )    
        
                chart_carte = carte_arc.resolve_scale(color='independent')
                st.altair_chart(chart_carte, use_container_width=True)
            st.markdown('<div style="height:12px;"></div>', unsafe_allow_html=True)
        st.markdown('<div style="height:18px;"></div>', unsafe_allow_html=True)
        render_turni_guadagni_section()

if __name__ == "__main__":
    main()

st.markdown('<div style="height:18px;"></div>', unsafe_allow_html=True)


#####################################
# FUNZIONI PER GESTIONE FILE LOCALE
#####################################

def load_data_local(percorso_file):
    if os.path.exists(percorso_file):
        try:
            with open(percorso_file, 'r') as file:
                contenuto = json.load(file)
            df = pd.DataFrame(contenuto)
            if not df.empty and "Mese" in df.columns:
                df["Mese"] = pd.to_datetime(df["Mese"], errors="coerce")
                df = df.sort_values(by="Mese").reset_index(drop=True)
            return df
        except Exception as e:
            placeholder = st.empty()
            placeholder.error(f"Errore nel caricamento di {percorso_file}: {e}")
            time.sleep(3)
            placeholder.empty()
            return pd.DataFrame()
    else:
        return pd.DataFrame()

def save_data_local(percorso_file, data):
    try:
        data_dict = data.to_dict(orient="records")
        json_content = json.dumps(data_dict, indent=4, default=str)
        with open(percorso_file, "w") as file:
            file.write(json_content)
        placeholder = st.empty()
        placeholder.success(f"Dati salvati correttamente in {percorso_file}.")
        time.sleep(3)
        placeholder.empty()
    except Exception as e:
        placeholder = st.empty()
        placeholder.error(f"Errore nel salvataggio di {percorso_file}: {e}")
        time.sleep(3)
        placeholder.empty()

#####################################
# FUNZIONI PER CALCOLI E GRAFICI
#####################################

@st.cache_data
def calcola_statistiche(data, colonne):
    stats = {col: {'somma': data[col].sum(), 'media': round(data[col].mean(), 2)} for col in colonne}
    return stats

def calcola_medie(data, colonne):
    if data.empty:
        return data
    data = data.copy()
    data["Mese"] = pd.to_datetime(data["Mese"], errors="coerce")
    for col in colonne:
        if col in data.columns:
            data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0)
            data[f"Media {col}"] = data[col].expanding().mean().round(2)
            if col == "Stipendio":
                data[f"Media {col} NO 13°/PDR"] = data[col].where(~data["Mese"].dt.month.isin([7, 12])).expanding().mean().round(2)
    return data
    
def crea_grafico_stipendi(data):
    if data.empty:
        return alt.Chart(pd.DataFrame({'Mese': [], 'Valore': [], 'Categoria': []})).mark_line()
    
    # Ensure Mese is datetime (Google Sheets returns strings)
    data = data.copy()
    data["Mese"] = pd.to_datetime(data["Mese"], errors="coerce")

    # Only melt columns that actually exist
    base_vars = [v for v in ["Stipendio", "Risparmi", "Messi da parte Totali"] if v in data.columns]
    media_vars = [v for v in ["Media Stipendio", "Media Risparmi", "Media Stipendio NO 13°/PDR", "Media Messi da parte Totali"] if v in data.columns]
    
    frames = [data.melt(id_vars=["Mese"], value_vars=base_vars, var_name="Categoria", value_name="Valore")]
    if media_vars:
        frames.append(data.melt(id_vars=["Mese"], value_vars=media_vars, var_name="Categoria", value_name="Valore"))
    
    data_completa = pd.concat(frames)

    data_completa["Categoria"] = data_completa["Categoria"].replace({
        "Stipendio": "Stipendi",
        "Media Stipendio": "Media Stipendi",
        "Media Stipendio NO 13°/PDR": "Media Stipendi NO 13°/PDR"
    })

    bar_categories = ["Risparmi", "Messi da parte Totali"]
    # FIX 1: Risparmi bar overlapping inside Messi da parte Totali
    # Use opacity layering - Messi da parte Totali as base, Risparmi overlaid
    bar_color_range = ["rgba(255, 165, 0, 0.5)", "#4CAF50"]

    line_categories = ["Stipendi", "Media Stipendi", "Media Stipendi NO 13°/PDR", "Media Risparmi", "Media Messi da parte Totali"]
    line_color_range = ["#5792E8", "#f87171", "#fb923c", "#FFA040", "#90EE90"]
    # FIX 2: Month labels - use full month names diagonal like Bollette chart
    data_completa["Mese"] = pd.to_datetime(data_completa["Mese"], errors="coerce")
    data_completa["Mese_str"] = data_completa["Mese"].dt.strftime("%B %Y")
    ordine_mesi = data_completa.sort_values("Mese")["Mese_str"].unique().tolist()

    df_bar = data_completa[data_completa["Categoria"].isin(bar_categories)]
    df_line = data_completa[~data_completa["Categoria"].isin(bar_categories)]

    # FIX 1: Messi da parte Totali as base bar
    df_messi = df_bar[df_bar["Categoria"] == "Messi da parte Totali"]
    df_risparmi = df_bar[df_bar["Categoria"] == "Risparmi"]

    # FIX 2: Use Mese_str with diagonal labels like Bollette chart
    base_bar_messi = alt.Chart(df_messi).mark_bar(size=40, color="#4CAF50", opacity=0.8).encode(
        x=alt.X("Mese_str:N", sort=ordine_mesi, title="Mese", axis=alt.Axis(labelAngle=-45)),
        y=alt.Y("Valore:Q", title="Valore (€)"),
        tooltip=[
            alt.Tooltip("Mese_str:N", title="Mese"),
            alt.Tooltip("Categoria:N", title="Voce"),
            alt.Tooltip("Valore:Q", title="Importo", format=",.2f"),
        ]
    )

    # FIX 1: Risparmi overlaid ON TOP of Messi da parte (same x position, smaller/different color)
    base_bar_risparmi = alt.Chart(df_risparmi).mark_bar(size=40, color="rgba(255,165,0,0.6)", opacity=0.9).encode(
        x=alt.X("Mese_str:N", sort=ordine_mesi),
        y=alt.Y("Valore:Q"),
        tooltip=[
            alt.Tooltip("Mese_str:N", title="Mese"),
            alt.Tooltip("Categoria:N", title="Voce"),
            alt.Tooltip("Valore:Q", title="Importo", format=",.2f"),
        ]
    )

    # Labels for Messi da parte Totali
    text_labels = alt.Chart(df_messi).mark_text(dy=-20, size=12, color='white').encode(
        x=alt.X("Mese_str:N", sort=ordine_mesi),
        y=alt.Y("Valore:Q"),
        text=alt.Text("Valore:Q")
    )

    # Line chart with FIX 2 month formatting
    base_line = alt.Chart(df_line).encode(
        x=alt.X("Mese:T", title="Mese", axis=alt.Axis(tickCount="month", format="%B %Y", labelAngle=-45)),
        y=alt.Y("Valore:Q", title="Valore (€)")
    )
    line_chart = base_line.mark_line(strokeWidth=2, strokeDash=[5,5]).encode(
    alt.Color("Categoria:N", scale=alt.Scale(domain=line_categories, range=line_color_range), title="Stipendi")
    )
    points_chart = base_line.mark_point(shape="circle", size=60, filled=True, opacity=0.85).encode(
        alt.Color("Categoria:N", scale=alt.Scale(domain=line_categories, range=line_color_range), title="Stipendi")
    )
    chart_line = line_chart + points_chart

    # FIX 1 + FIX 2: Layer bars with overlap + line chart
    final_chart = alt.layer(base_bar_messi, base_bar_risparmi, text_labels, chart_line).resolve_scale(
        y="shared",
        color="independent"
    )
    return final_chart

def crea_grafico_bollette_linea_continua(data_completa, order):
    df_bollette = data_completa[data_completa["Categoria"] != "Saldo"]
    order_mapping = {"Internet": 0, "Elettricità": 1, "Gas": 2, "Acqua": 3, "Tari": 4}
    df_bollette["stack_order"] = df_bollette["Categoria"].map(order_mapping)
    
    base_stack = alt.Chart(df_bollette).transform_stack(
        stack='Valore',
        groupby=['Mese_str'],
        sort=[{'field': 'stack_order', 'order': 'ascending'}],
        as_=['lower', 'upper']
    )
    
    barre = base_stack.mark_bar(opacity=0.8, size=18).encode(
        x=alt.X("Mese_str:N", sort=order, title="Mese", axis=alt.Axis(labelAngle=-45, labelFontSize=10)),
        y=alt.Y("lower:Q", title="Valore (€)"),
        y2="upper:Q",
        color=alt.Color("Categoria:N", scale=alt.Scale(
            domain=["Elettricità", "Gas", "Acqua", "Internet", "Tari"],
            range=["#84B6F4", "#FF6961", "#96DED1", "#FFF5A1", "#C19A6B"]),
            legend=alt.Legend(title="Bollette")),
        tooltip=[
            alt.Tooltip("Mese_str:N", title="Mese"),
            alt.Tooltip("Categoria:N", title="Voce"),
            alt.Tooltip("Valore:Q", title="Importo", format=",.2f"),
        ]
    )
    
    df_saldo = data_completa[data_completa["Categoria"] == "Saldo"]
    linea_saldo_unica = alt.Chart(df_saldo).mark_line(strokeWidth=2, strokeDash=[5,5], color="#F0F0F0", opacity=0.25).encode(
        x=alt.X("Mese_str:N", sort=order),
        y=alt.Y("Valore:Q"),
        tooltip=[
            alt.Tooltip("Mese_str:N", title="Mese"),
            alt.Tooltip("Valore:Q", title="Saldo", format=",.2f"),
        ]
    )

    punti_saldo_color = alt.Chart(df_saldo).mark_point(shape="diamond", size=80, filled=True).encode(
        x=alt.X("Mese_str:N", sort=order),
        y=alt.Y("Valore:Q"),
        color=alt.condition("datum.Valore < 0", alt.value("#FF6961"), alt.value("#77DD77")),
        tooltip=[
            alt.Tooltip("Mese_str:N", title="Mese"),
            alt.Tooltip("Valore:Q", title="Saldo", format=",.2f"),
        ]
    )

    df_totali = data_completa[data_completa["Categoria"].isin(["Elettricità", "Gas", "Acqua", "Internet", "Tari"])].groupby(
        ["Mese", "Mese_str"], as_index=False
    )["Valore"].sum()
    
    testo_totale = alt.Chart(df_totali).mark_text(
        align="center", baseline="bottom", dy=-5, fontSize=10, color="white"
    ).encode(
        x=alt.X("Mese_str:N", sort=order),
        y=alt.Y("Valore:Q"),
        text=alt.Text("Valore:Q", format=".2f")
    )
    
    linea_saldo = linea_saldo_unica + punti_saldo_color
    grafico_finale = alt.layer(barre, linea_saldo, testo_totale)
    return grafico_finale
    
def crea_confronto_anno_su_anno_stipendi(data):
    if data.empty:
        return alt.Chart(pd.DataFrame({'Mese_str': [], 'Stipendio': [], 'Anno': []})).mark_line()
    df = data.copy()
    df["Mese"] = pd.to_datetime(df["Mese"], errors="coerce")
    df = df.dropna(subset=["Mese"])
    if df.empty:
        return alt.Chart(pd.DataFrame({'Mese_str': [], 'Stipendio': [], 'Anno': []})).mark_line()
    df["Anno"] = df["Mese"].dt.year.astype(str)
    df["Mese_str"] = df["Mese"].dt.strftime("%b")
    chart = alt.Chart(df).mark_line(point=True).encode(
        x=alt.X("Mese_str:N", title="Mese",
                sort=["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]),
        y=alt.Y("Stipendio:Q", title="Stipendio (€)", aggregate="mean"),
        color=alt.Color("Anno:N", title="Anno"),
        tooltip=["Anno", "Mese_str", alt.Tooltip("Stipendio:Q", aggregate="mean", format=".2f")]
    ).properties(title="")
    return chart

def crea_confronto_anno_su_anno_bollette(data):
    if data.empty:
        return alt.Chart(pd.DataFrame({'Mese_str': [], 'Totale_Bollette': [], 'Anno': []})).mark_line()
    df = data.copy()
    df["Mese"] = pd.to_datetime(df["Mese"], errors="coerce")
    df = df.dropna(subset=["Mese"])
    if df.empty:
        return alt.Chart(pd.DataFrame({'Mese_str': [], 'Totale_Bollette': [], 'Anno': []})).mark_line()
    if "Totale_Bollette" not in df.columns:
        df["Totale_Bollette"] = df["Elettricità"] + df["Gas"] + df["Acqua"] + df["Internet"] + df["Tari"]
    df["Anno"] = df["Mese"].dt.year.astype(str)
    df["Mese_str"] = df["Mese"].dt.strftime("%b")
    chart = alt.Chart(df).mark_line(point=True).encode(
        x=alt.X("Mese_str:N", title="Mese",
                sort=["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]),
        y=alt.Y("Totale_Bollette:Q", title="Spesa Totale (€)"),
        color=alt.Color("Anno:N", title="Anno"),
        tooltip=["Anno", "Mese_str", alt.Tooltip("Totale_Bollette:Q", format=".2f")]
    ).properties(title="")
    return chart


#######################################
# SEZIONE: Storico Stipendi e Risparmi
#######################################

st.markdown('<div class="section-pill">📈 Storico Stipendi</div>', unsafe_allow_html=True)
st.title("Storico Stipendi e Risparmi")

STIPENDI_HEADERS = ["Mese", "Stipendio", "Risparmi", "Messi da parte Totali"]
data_stipendi = load_data_gsheets("Stipendi", STIPENDI_HEADERS)
if data_stipendi.empty:
    data_stipendi = pd.DataFrame(columns=STIPENDI_HEADERS)
else:
    data_stipendi["Mese"] = pd.to_datetime(data_stipendi["Mese"], errors="coerce")
    data_stipendi = data_stipendi.dropna(subset=["Mese"])
    data_stipendi["Mese"] = data_stipendi["Mese"].dt.to_period("M").dt.to_timestamp()
    for col in ["Stipendio", "Risparmi", "Messi da parte Totali"]:
        data_stipendi[col] = pd.to_numeric(data_stipendi[col], errors="coerce").fillna(0.0)

col_sx_stip, col_cx_stip_vuoto, col_dx_stip_chart = st.columns(LAYOUT_COLONNE["storico_form_chart"])
with col_sx_stip:
    st.subheader("Gestisci mese")
    mesi_anni = pd.date_range(start="2024-03-01", end="2030-12-01", freq="MS").strftime("%B %Y")
    current_month_label = _now_italy().strftime("%B %Y")
    mese_default_index = list(mesi_anni).index(current_month_label) if current_month_label in list(mesi_anni) else 0
    selected_mese = st.selectbox("Seleziona il mese e l'anno", mesi_anni, index=mese_default_index, key="mese_stipendi")
    mese_dt = pd.Timestamp(datetime.strptime(selected_mese, "%B %Y")).to_period("M").to_timestamp()

    record_esistente = data_stipendi[data_stipendi["Mese"] == mese_dt] if not data_stipendi.empty else pd.DataFrame()
    stipendio_val = float(record_esistente["Stipendio"].iloc[0]) if not record_esistente.empty else 0.0
    risparmi_val = float(record_esistente["Risparmi"].iloc[0]) if not record_esistente.empty else 0.0
    messi_da_parte_mese_corrente_val = float(record_esistente["Messi da parte Totali"].iloc[0]) if not record_esistente.empty else 0.0
    st.caption("I campi sotto mostrano i valori salvati per il mese selezionato. Se il mese non esiste, verrà creato al salvataggio.")

    col_input1, col_input2 = st.columns(2)
    with col_input1:
        stipendio = st.number_input("Stipendio (€)", min_value=0.0, step=100.0, value=stipendio_val, key=f"stipendio_input_{selected_mese}")
        aggiungi_button = st.button("Aggiungi/Modifica Dati", key="aggiorna_stipendi")
    with col_input2:
        risparmi = st.number_input("Risparmi mese prec. (€)", min_value=0.0, step=100.0, value=risparmi_val, key=f"risparmi_input_{selected_mese}")
        messi_da_parte_mese_corrente = st.number_input("Messi da parte Totali (Risp. su BNL) (€)", min_value=0.0, step=100.0, value=messi_da_parte_mese_corrente_val, key=f"messi_da_parte_input_{selected_mese}")
        elimina_button = st.button(f"Elimina Record per {selected_mese}", key="elimina_stipendi")

    if aggiungi_button:
        if stipendio > 0 or risparmi > 0 or messi_da_parte_mese_corrente > 0:
            if not record_esistente.empty:
                data_stipendi.loc[data_stipendi["Mese"] == mese_dt, "Stipendio"] = stipendio
                data_stipendi.loc[data_stipendi["Mese"] == mese_dt, "Risparmi"] = risparmi
                data_stipendi.loc[data_stipendi["Mese"] == mese_dt, "Messi da parte Totali"] = messi_da_parte_mese_corrente
                placeholder = st.empty()
                placeholder.success(f"Record per {selected_mese} aggiornato!")
                time.sleep(3)
                placeholder.empty()
            else:
                nuovo_record = {"Mese": mese_dt, "Stipendio": stipendio, "Risparmi": risparmi, "Messi da parte Totali": messi_da_parte_mese_corrente}
                data_stipendi = pd.concat([data_stipendi, pd.DataFrame([nuovo_record])], ignore_index=True)
                placeholder = st.empty()
                placeholder.success(f"Dati per {selected_mese} aggiunti!")
                time.sleep(3)
                placeholder.empty()

            data_stipendi = data_stipendi.sort_values(by="Mese").reset_index(drop=True)
            save_data_gsheets("Stipendi", STIPENDI_HEADERS, data_stipendi)
        else:
            placeholder = st.empty()
            placeholder.error("Inserisci valori validi per stipendio, risparmi o messi da parte!")
            time.sleep(3)
            placeholder.empty()

    if elimina_button:
        if not record_esistente.empty:
            data_stipendi = data_stipendi[data_stipendi["Mese"] != mese_dt]
            save_data_gsheets("Stipendi", STIPENDI_HEADERS, data_stipendi)
            placeholder = st.empty()
            placeholder.success(f"Record per {selected_mese} eliminato!")
            time.sleep(3)
            placeholder.empty()
        else:
            placeholder = st.empty()
            placeholder.error(f"Nessun record trovato per {selected_mese}.")
            time.sleep(3)
            placeholder.empty()

with col_dx_stip_chart:
    st.markdown("### Confronto Anno su Anno degli Stipendi")
    if not data_stipendi.empty:
        confronto_chart = crea_confronto_anno_su_anno_stipendi(data_stipendi)
        st.altair_chart(confronto_chart, use_container_width=True)
    else:
        st.info("Nessun dato disponibile ancora.")

st.markdown("---")
st.subheader("Dati Storici Stipendi/Risparmi")

col_table, col_chart = st.columns(LAYOUT_COLONNE["storico_tabella_grafico"])
with col_table:
    df_stip = data_stipendi.copy()
    st.markdown(
        _history_table_html(
            df_stip,
            ["Stipendio", "Risparmi", "Messi da parte Totali"],
            {
                "Stipendio": "#5792E8",
                "Risparmi": "#EF9F27",
                "Messi da parte Totali": "#1D9E75",
            },
        ),
        unsafe_allow_html=True,
    )
    
    data_stipendi = calcola_medie(data_stipendi, ["Stipendio", "Risparmi", "Messi da parte Totali"])
    stats_stip = calcola_statistiche(data_stipendi, ["Stipendio", "Risparmi", "Messi da parte Totali"])
    st.markdown(
        '<div style="height:18px;margin:12px 0 16px;border-top:1px solid rgba(255,255,255,.08);"></div>',
        unsafe_allow_html=True
    )
    
    col_somme1, col_somme2, col_somme3 = st.columns(LAYOUT_COLONNE["storico_kpi"])
    _s1 = f"{stats_stip['Stipendio']['somma']:,.2f} €"
    _s2 = f"{stats_stip['Stipendio']['media']:,.2f} €"
    _r1 = f"{stats_stip['Risparmi']['somma']:,.2f} €"
    _r2 = f"{stats_stip['Risparmi']['media']:,.2f} €"
    _m1 = f"{stats_stip['Messi da parte Totali']['somma']:,.2f} €"
    _m2 = f"{stats_stip['Messi da parte Totali']['media']:,.2f} €"
    with col_somme1:
        st.markdown(f"""
        <div class="kpi-card" style="margin-bottom:8px;">
            <div class="kpi-label">Somma Stipendi</div>
            <div class="kpi-value" style="color:#5792E8;font-size:16px;">{_s1}</div>
        </div>
        <div class="kpi-card" style="margin-bottom:8px;">
            <div class="kpi-label">Media Stipendi</div>
            <div class="kpi-value" style="color:#f87171;font-size:16px;">{_s2}</div>
        </div>""", unsafe_allow_html=True)
        if "Media Stipendio NO 13°/PDR" in data_stipendi.columns and not data_stipendi.empty:
            _s3 = f"{data_stipendi['Media Stipendio NO 13°/PDR'].iloc[-1]:,.2f} €"
            st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">Media NO 13°/PDR</div>
            <div class="kpi-value" style="color:#fb923c;font-size:16px;">{_s3}</div>
        </div>""", unsafe_allow_html=True)
    with col_somme2:
        st.markdown(f"""
        <div class="kpi-card" style="margin-bottom:8px;">
            <div class="kpi-label">Somma Risparmi</div>
            <div class="kpi-value" style="color:#EF9F27;font-size:16px;">{_r1}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Media Risparmi</div>
            <div class="kpi-value" style="color:#FFA040;font-size:16px;">{_r2}</div>
        </div>""", unsafe_allow_html=True)
    with col_somme3:
        st.markdown(f"""
        <div class="kpi-card" style="margin-bottom:8px;">
            <div class="kpi-label">Somma Messi da Parte</div>
            <div class="kpi-value" style="color:#1D9E75;font-size:16px;">{_m1}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Media Messi da Parte</div>
            <div class="kpi-value" style="color:#90EE90;font-size:16px;">{_m2}</div>
        </div>""", unsafe_allow_html=True)

with col_chart:
    if data_stipendi is not None and not data_stipendi.empty:
        try:
            chart_data = data_stipendi.copy()
            chart_data["Mese"] = pd.to_datetime(chart_data["Mese"], errors="coerce")
            chart_data = chart_data.dropna(subset=["Mese"])
            current_month_start = pd.Timestamp(_now_italy().date()).to_period("M").to_timestamp()
            chart_start = current_month_start - pd.DateOffset(years=3)
            chart_data = chart_data[(chart_data["Mese"] >= chart_start) & (chart_data["Mese"] <= current_month_start)]
            chart_data["Extra messi da parte"] = (
                pd.to_numeric(chart_data["Messi da parte Totali"], errors="coerce").fillna(0)
                - pd.to_numeric(chart_data["Risparmi"], errors="coerce").fillna(0)
            ).clip(lower=0)
            chart_data["Risparmi tooltip"] = pd.to_numeric(chart_data["Risparmi"], errors="coerce").fillna(0)
            chart_data["Mese_str"] = chart_data["Mese"].dt.strftime("%b %Y")
            ordine_mesi = chart_data.sort_values("Mese")["Mese_str"].unique().tolist()

            x_axis = alt.X(
                "Mese_str:N",
                sort=ordine_mesi,
                title="Mese",
                axis=alt.Axis(labelAngle=-45, labelFontSize=10)
            )

            line_stipendi = alt.Chart(chart_data).mark_line(
                color="#5792E8", strokeWidth=2, point=True
            ).encode(
                x=x_axis,
                y=alt.Y("Stipendio:Q", title="Stipendi (€)", axis=alt.Axis(orient="left")),
                tooltip=[alt.Tooltip("Mese_str:N", title="Mese"), alt.Tooltip("Stipendio:Q", title="Stipendio", format=",.2f")]
            )

            line_media_stip = alt.Chart(chart_data).mark_line(
                color="#f87171", strokeWidth=2, strokeDash=[6,3], point=True, opacity=0.4
            ).encode(
                x=x_axis,
                y=alt.Y("Media Stipendio:Q"),
                tooltip=[alt.Tooltip("Mese_str:N", title="Mese"), alt.Tooltip("Media Stipendio:Q", title="Media stipendio", format=",.2f")]
            )

            line_media_no13 = alt.Chart(chart_data).mark_line(
                color="#fb923c", strokeWidth=2, strokeDash=[3,3], point=True
            ).encode(
                x=x_axis,
                y=alt.Y("Media Stipendio NO 13°/PDR:Q"),
                tooltip=[alt.Tooltip("Mese_str:N", title="Mese"), alt.Tooltip("Media Stipendio NO 13°/PDR:Q", title="Media senza 13/PDR", format=",.2f")]
            )

            risparmi_stack = chart_data.melt(
                id_vars=["Mese_str", "Risparmi tooltip", "Messi da parte Totali"],
                value_vars=["Risparmi", "Extra messi da parte"],
                var_name="Componente risparmio",
                value_name="Valore"
            )
            risparmi_stack["Voce"] = risparmi_stack["Componente risparmio"].replace({
                "Risparmi": "Risparmi",
                "Extra messi da parte": "Messi da parte"
            })

            bars_risparmi = alt.Chart(risparmi_stack).mark_bar(
                opacity=0.38, size=17
            ).encode(
                x=x_axis,
                y=alt.Y(
                    "Valore:Q",
                    title="Risparmi / messi da parte (€)",
                    axis=alt.Axis(orient="right"),
                    stack="zero"
                ),
                color=alt.Color(
                    "Voce:N",
                    scale=alt.Scale(
                        domain=["Risparmi", "Messi da parte"],
                        range=["#EF9F27", "#1D9E75"]
                    ),
                    legend=None
                ),
                order=alt.Order("Componente risparmio:N", sort="descending"),
                tooltip=[
                    alt.Tooltip("Mese_str:N", title="Mese"),
                    alt.Tooltip("Voce:N", title="Voce"),
                    alt.Tooltip("Valore:Q", title="Importo", format=",.2f"),
                    alt.Tooltip("Messi da parte Totali:Q", title="Totale messo da parte", format=",.2f"),
                ]
            )

            line_media_risp = alt.Chart(chart_data).mark_line(
                color="#FFA040", strokeWidth=2, strokeDash=[4,4], point=True, opacity=0.9
            ).encode(
                x=x_axis,
                y=alt.Y("Media Risparmi:Q"),
                tooltip=[alt.Tooltip("Mese_str:N", title="Mese"), alt.Tooltip("Media Risparmi:Q", title="Media risparmi", format=",.2f")]
            )

            line_media_messi = alt.Chart(chart_data).mark_line(
                color="#90EE90", strokeWidth=2, strokeDash=[5,5], point=True
            ).encode(
                x=x_axis,
                y=alt.Y("Media Messi da parte Totali:Q"),
                tooltip=[alt.Tooltip("Mese_str:N", title="Mese"), alt.Tooltip("Media Messi da parte Totali:Q", title="Media messi da parte", format=",.2f")]
            )

            stipendi_chart = alt.layer(line_stipendi, line_media_stip, line_media_no13)
            risparmi_chart = alt.layer(bars_risparmi, line_media_risp, line_media_messi)

            grafico_finale = alt.layer(risparmi_chart, stipendi_chart).properties(
                title="Storico Stipendi e Risparmi",
                height=430
            ).resolve_scale(y="independent")

            st.altair_chart(grafico_finale, use_container_width=True)

            # Legend labels  <-- YAHAN SE ADD KARO
            st.markdown("""
            <div style="display:flex; flex-wrap:wrap; gap:16px; margin-top:8px; padding:10px 16px; 
                        background:rgba(255,255,255,0.04); border-radius:10px;">
                <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);">
                    <span style="width:14px;height:14px;border-radius:3px;background:#1D9E75;opacity:0.7;display:inline-block;"></span>Messi da parte
                </span>
                <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);">
                    <span style="width:14px;height:14px;border-radius:3px;background:#EF9F27;display:inline-block;"></span>Risparmi
                </span>
                <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);">
                    <span style="width:28px;height:3px;background:#5792E8;display:inline-block;border-radius:2px;"></span>Stipendi
                </span>
                <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);">
                    <span style="width:28px;height:2px;border-top:2px dashed #f87171;display:inline-block;"></span>Media Stipendi
                </span>
                <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);">
                    <span style="width:28px;height:2px;border-top:2px dashed #fb923c;display:inline-block;"></span>Media NO 13°/PDR
                </span>
                <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);">
                    <span style="width:28px;height:2px;border-top:2px dashed #FFA040;display:inline-block;"></span>Media Risparmi
                </span>
                <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);">
                    <span style="width:28px;height:2px;border-top:2px dashed #90EE90;display:inline-block;"></span>Media Messi da parte
                </span>
            </div>
            """, unsafe_allow_html=True)
        except Exception as e:
            st.error(f"Errore nel grafico: {e}")
    else:
        st.info("Nessun dato disponibile. Aggiungi i dati nella sezione a sinistra.")

st.markdown('<hr style="width: 100%; height:1px;border-width:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.18),transparent);">', unsafe_allow_html=True)


############################
# SEZIONE: Storico Bollette
#############################

st.markdown('<div class="section-pill">🧾 Storico Bollette</div>', unsafe_allow_html=True)
st.title("Storico Bollette")

BOLLETTE_HEADERS = ["Mese", "Elettricità", "Gas", "Acqua", "Internet", "Tari"]
data_bollette = load_data_gsheets("Bollette", BOLLETTE_HEADERS)
if data_bollette.empty:
    data_bollette = pd.DataFrame(columns=BOLLETTE_HEADERS)
else:
    data_bollette["Mese"] = pd.to_datetime(data_bollette["Mese"], errors="coerce")
    data_bollette = data_bollette.dropna(subset=["Mese"])
    data_bollette["Mese"] = data_bollette["Mese"].dt.to_period("M").dt.to_timestamp()
    for col in ["Elettricità", "Gas", "Acqua", "Internet", "Tari"]:
        data_bollette[col] = pd.to_numeric(data_bollette[col], errors="coerce").fillna(0.0)

col_sx_bol, col_cx_bol_vuoto, col_dx_bol_chart = st.columns(LAYOUT_COLONNE["bollette_form_chart"])

with col_sx_bol:
    with st.container():
        st.subheader("Gestisci bollette")
        mesi_anni_bol = pd.date_range(start="2024-03-01", end="2030-12-01", freq="MS").strftime("%B %Y")
        current_month_label_bol = _now_italy().strftime("%B %Y")
        mese_bol_default_index = list(mesi_anni_bol).index(current_month_label_bol) if current_month_label_bol in list(mesi_anni_bol) else 0
        selected_mese_bol = st.selectbox("Seleziona il mese e l'anno", mesi_anni_bol, index=mese_bol_default_index, key="mese_bollette")
        mese_dt_bol = pd.Timestamp(datetime.strptime(selected_mese_bol, "%B %Y")).to_period("M").to_timestamp()
        
        record_bol = data_bollette[data_bollette["Mese"] == mese_dt_bol] if not data_bollette.empty else pd.DataFrame()
        elettricita_val = float(record_bol["Elettricità"].iloc[0]) if not record_bol.empty else 0.0
        gas_val = float(record_bol["Gas"].iloc[0]) if not record_bol.empty else 0.0
        acqua_val = float(record_bol["Acqua"].iloc[0]) if not record_bol.empty else 0.0
        internet_val = float(record_bol["Internet"].iloc[0]) if not record_bol.empty else 0.0
        tari_val = float(record_bol["Tari"].iloc[0]) if not record_bol.empty else 0.0
        st.caption("I campi sotto mostrano i valori salvati per il mese selezionato. Se il mese non esiste, verrà creato al salvataggio.")
        
        col_bol_input1, col_bol_input2 = st.columns(2)
        with col_bol_input1:
            elettricita = st.number_input("Elettricità (€)", min_value=0.0, step=10.0, value=elettricita_val, key=f"elettricita_input_{selected_mese_bol}")
            gas = st.number_input("Gas (€)", min_value=0.0, step=10.0, value=gas_val, key=f"gas_input_{selected_mese_bol}")
            aggiungi_bollette = st.button("Aggiungi/Modifica Bollette", key="aggiorna_bollette")
        with col_bol_input2:
            acqua = st.number_input("Acqua (€)", min_value=0.0, step=10.0, value=acqua_val, key=f"acqua_input_{selected_mese_bol}")
            internet = st.number_input("Internet (€)", min_value=0.0, step=10.0, value=internet_val, key=f"internet_input_{selected_mese_bol}")
            tari = st.number_input("Tari (€)", min_value=0.0, step=10.0, value=tari_val, key=f"tari_input_{selected_mese_bol}")
            elimina_bollette = st.button(f"Elimina Record per {selected_mese_bol}", key="elimina_bollette")

        if aggiungi_bollette:
            if elettricita > 0 or gas > 0 or acqua > 0 or internet > 0 or tari > 0:
                if not record_bol.empty:
                    data_bollette.loc[data_bollette["Mese"] == mese_dt_bol, "Elettricità"] = elettricita
                    data_bollette.loc[data_bollette["Mese"] == mese_dt_bol, "Gas"] = gas
                    data_bollette.loc[data_bollette["Mese"] == mese_dt_bol, "Acqua"] = acqua
                    data_bollette.loc[data_bollette["Mese"] == mese_dt_bol, "Internet"] = internet
                    data_bollette.loc[data_bollette["Mese"] == mese_dt_bol, "Tari"] = tari
                    placeholder = st.empty()
                    placeholder.success(f"Record per {selected_mese_bol} aggiornato!")
                    time.sleep(3)
                    placeholder.empty()
                else:
                    nuovo_record_bol = {"Mese": mese_dt_bol, "Elettricità": elettricita, "Gas": gas, "Acqua": acqua, "Internet": internet, "Tari": tari}
                    data_bollette = pd.concat([data_bollette, pd.DataFrame([nuovo_record_bol])], ignore_index=True)
                    placeholder = st.empty()
                    placeholder.success(f"Bollette per {selected_mese_bol} aggiunte!")
                    time.sleep(3)
                    placeholder.empty()

                data_bollette = data_bollette.sort_values(by="Mese").reset_index(drop=True)
                save_data_gsheets("Bollette", BOLLETTE_HEADERS, data_bollette)
            else:
                placeholder = st.empty()
                placeholder.error("Inserisci valori validi per le bollette!")
                time.sleep(3)
                placeholder.empty()

        if elimina_bollette:
            if not record_bol.empty:
                data_bollette = data_bollette[data_bollette["Mese"] != mese_dt_bol]
                save_data_gsheets("Bollette", BOLLETTE_HEADERS, data_bollette)
                placeholder = st.empty()
                placeholder.success(f"Record per {selected_mese_bol} eliminato!")
                time.sleep(3)
                placeholder.empty()
            else:
                placeholder = st.empty()
                placeholder.error(f"Nessun record trovato per {selected_mese_bol}.")
                time.sleep(3)
                placeholder.empty()

with col_dx_bol_chart:
    st.markdown("### Confronto Anno su Anno delle Bollette")
    if not data_bollette.empty:
        confronto_bollette_chart = crea_confronto_anno_su_anno_bollette(data_bollette)
        st.altair_chart(confronto_bollette_chart, use_container_width=True)
    else:
        st.info("Nessun dato disponibile ancora.")

st.markdown("---")
st.subheader("Dati Storici Bollette")
col_bol_table, col_bol_chart = st.columns(LAYOUT_COLONNE["bollette_tabella_grafico"])
with col_bol_table:
    df_bol = data_bollette.copy()
    st.markdown(
        _history_table_html(
            df_bol,
            ["Elettricità", "Gas", "Acqua", "Internet", "Tari"],
            {
                "Elettricità": "#84B6F4",
                "Gas": "#FF6961",
                "Acqua": "#96DED1",
                "Internet": "#FFF5A1",
                "Tari": "#C19A6B",
            },
        ),
        unsafe_allow_html=True,
    )
    
    stats_bollette = calcola_statistiche(data_bollette, ["Elettricità", "Gas", "Acqua", "Internet", "Tari"])
    st.markdown(
        '<div style="height:18px;margin:12px 0 16px;border-top:1px solid rgba(255,255,255,.08);"></div>',
        unsafe_allow_html=True
    )
    
    col_bol_somme1, col_bol_somme2, col_bol_somme3 = st.columns(3)
    with col_bol_somme1:
        st.markdown(f"""
        <div class="kpi-card" style="margin-bottom:8px;">
            <div class="kpi-label">Somma Elettricità</div>
            <div class="kpi-value" style="color:#84B6F4;font-size:16px;">{stats_bollette['Elettricità']['somma']:,.2f} €</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Somma Gas</div>
            <div class="kpi-value" style="color:#FF6961;font-size:16px;">{stats_bollette['Gas']['somma']:,.2f} €</div>
        </div>""", unsafe_allow_html=True)
    with col_bol_somme2:
        st.markdown(f"""
        <div class="kpi-card" style="margin-bottom:8px;">
            <div class="kpi-label">Somma Acqua</div>
            <div class="kpi-value" style="color:#96DED1;font-size:16px;">{stats_bollette['Acqua']['somma']:,.2f} €</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Somma Tari</div>
            <div class="kpi-value" style="color:#C19A6B;font-size:16px;">{stats_bollette['Tari']['somma']:,.2f} €</div>
        </div>""", unsafe_allow_html=True)
    with col_bol_somme3:
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">Somma Internet</div>
            <div class="kpi-value" style="color:#FFF5A1;font-size:16px;">{stats_bollette['Internet']['somma']:,.2f} €</div>
        </div>""", unsafe_allow_html=True)
    
    def calcola_saldo(data, decisione_budget_bollette_mensili):
        saldo_iniziale = 0
        saldi = []
        for _, row in data.iterrows():
            totale = row.get("Elettricità", 0) + row.get("Gas", 0) + row.get("Acqua", 0) + row.get("Internet", 0) + row.get("Tari", 0)
            saldo = saldo_iniziale + decisione_budget_bollette_mensili - totale
            saldi.append(saldo)
            saldo_iniziale = saldo
        data["Saldo"] = saldi
        return data
    
    data_bollette = calcola_saldo(data_bollette, decisione_budget_bollette_mensili)
    
    data_melted = data_bollette.melt(
        id_vars=["Mese"],
        value_vars=["Elettricità", "Gas", "Acqua", "Internet", "Tari"],
        var_name="Categoria",
        value_name="Valore"
    )
    data_saldo = data_bollette[["Mese", "Saldo"]].copy()
    data_saldo["Categoria"] = "Saldo"
    data_saldo["Valore"] = data_saldo["Saldo"]
    data_saldo.drop(columns=["Saldo"], inplace=True)
    data_completa_bollette = pd.concat([data_melted, data_saldo], ignore_index=True)
    data_completa_bollette["Mese"] = pd.to_datetime(data_completa_bollette["Mese"], errors="coerce")
    current_month_start_bol = pd.Timestamp(_now_italy().date()).to_period("M").to_timestamp()
    chart_start_bol = current_month_start_bol - pd.DateOffset(years=3)
    data_completa_bollette = data_completa_bollette[
        (data_completa_bollette["Mese"] >= chart_start_bol)
        & (data_completa_bollette["Mese"] <= current_month_start_bol)
    ].copy()
    data_completa_bollette["Mese_str"] = data_completa_bollette["Mese"].dt.strftime("%b %Y")
    ordine = data_completa_bollette.sort_values("Mese")["Mese_str"].unique().tolist()
    
with col_bol_chart:
    st.altair_chart(crea_grafico_bollette_linea_continua(data_completa_bollette, ordine).properties(height=500), use_container_width=True)

    total_bollette = (stats_bollette["Elettricità"]["somma"] + stats_bollette["Gas"]["somma"] +
                    stats_bollette["Acqua"]["somma"] + stats_bollette["Internet"]["somma"] + stats_bollette["Tari"]["somma"])
    n_mesi = data_bollette["Mese"].nunique() if data_bollette["Mese"].nunique() > 0 else 1
    media_annua = total_bollette / n_mesi
    st.markdown(f"**Media mensile bollette:** <span style='color:#FFA500;'>{media_annua:,.2f} €</span>", unsafe_allow_html=True)

st.markdown('<hr style="width: 100%; height:1px;border-width:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.18),transparent);">', unsafe_allow_html=True)
