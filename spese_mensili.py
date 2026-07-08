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


@st.cache_resource
def get_gsheet_spreadsheet():
    client = get_gsheet_client()
    if not client:
        return None
    try:
        return client.open_by_url(SHEET_URL)
    except Exception:
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
        spreadsheet = get_gsheet_spreadsheet()
        if spreadsheet is None:
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

components.html(
    """
    <script>
    const params = new URLSearchParams(window.parent.location.search);
    const hasExplicitView = params.has("view");
    const isPhoneWidth = window.parent.innerWidth <= 820;
    if (!hasExplicitView && isPhoneWidth) {
        params.set("view", "mobile");
        window.parent.location.replace(window.parent.location.pathname + "?" + params.toString() + window.parent.location.hash);
    }
    </script>
    """,
    height=0,
)

_view_param = st.query_params.get("view")
if isinstance(_view_param, list):
    _view_param = _view_param[0] if _view_param else None
_default_view = "Desktop" if _view_param == "desktop" else "Telefono"

st.markdown("""
<style>
[data-testid="stSidebar"] {
    display: none !important;
}
.main-view-switch {
    position: fixed;
    z-index: 999999;
    top: 4px;
    left: 8px;
    display: inline-flex;
    gap: 4px;
    padding: 3px;
    border-radius: 999px;
    background: rgba(9,14,24,.86);
    border: 0.5px solid rgba(148,163,184,.18);
    backdrop-filter: blur(10px);
    box-shadow: 0 8px 22px rgba(0,0,0,.22);
}
.main-view-switch a {
    display: inline-flex;
    align-items: center;
    min-height: 24px;
    padding: 4px 9px;
    border-radius: 999px;
    text-decoration: none !important;
    color: rgba(219,234,254,.72) !important;
    font-size: 10px;
    font-weight: 900;
    letter-spacing: .15px;
}
.main-view-switch a.active {
    color: #fff !important;
    background: linear-gradient(135deg, rgba(56,189,248,.40), rgba(96,165,250,.22));
    box-shadow: 0 0 0 1px rgba(56,189,248,.30);
}
</style>
""", unsafe_allow_html=True)

VISTA_APP = _default_view
_desktop_active = "active" if VISTA_APP == "Desktop" else ""
_mobile_active = "active" if VISTA_APP == "Telefono" else ""
st.markdown(
    f'<div class="main-view-switch">'
    f'<a class="{_desktop_active}" href="?view=desktop" target="_self">Desktop</a>'
    f'<a class="{_mobile_active}" href="?view=mobile" target="_self">Telefono</a>'
    f'</div>',
    unsafe_allow_html=True
)

MOBILE_VIEW = VISTA_APP == "Telefono"
MOBILE_SECTIONS = ["Panoramica", "Spese", "Variabili", "Entrate", "Risparmi", "Carte", "Note", "Turni", "Storico", "Bollette"]

if MOBILE_VIEW:
    mobile_section_param = st.query_params.get("mobile_section")
    if isinstance(mobile_section_param, list):
        mobile_section_param = mobile_section_param[0] if mobile_section_param else None
    if mobile_section_param == "Promemoria":
        mobile_section_param = "Note"
    if mobile_section_param in MOBILE_SECTIONS and "mobile_section_select" not in st.session_state:
        st.session_state["mobile_section_select"] = mobile_section_param
    pending_mobile_section = st.session_state.pop("_pending_mobile_section", None)
    if pending_mobile_section == "Promemoria":
        pending_mobile_section = "Note"
    if pending_mobile_section in MOBILE_SECTIONS:
        st.session_state["mobile_section_select"] = pending_mobile_section
    if "mobile_section_select" not in st.session_state:
        st.session_state["mobile_section_select"] = "Panoramica"
    if st.session_state.get("mobile_section_select") == "Promemoria":
        st.session_state["mobile_section_select"] = "Note"
    if st.session_state.get("mobile_section_select") not in MOBILE_SECTIONS:
        st.session_state["mobile_section_select"] = "Panoramica"
    st.markdown("""
    <style>
    .block-container {
        max-width: 760px !important;
        padding: 0.75rem 0.85rem 4rem !important;
    }
    .mobile-compact-input-note {
        display: block;
        width: 100%;
        text-align: center;
        font-size: 10px;
        color: rgba(255,255,255,.42);
        margin-top: 6px;
        margin-bottom: 18px;
        line-height: 1.15;
    }
    html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"], .block-container {
        max-width: 100vw !important;
        overflow-x: hidden !important;
        box-sizing: border-box !important;
    }
    * {
        box-sizing: border-box;
    }
    [data-testid="stVerticalBlock"],
    [data-testid="element-container"],
    [data-testid="stTextInput"],
    [data-testid="stNumberInput"],
    .kpi-card,
    .budget-memory-card {
        min-width: 0 !important;
        max-width: 100% !important;
        width: 100% !important;
    }
    [data-testid="stTextInput"] input,
    [data-testid="stNumberInput"] input {
        min-width: 0 !important;
        width: 100% !important;
        font-size: 12px !important;
        padding-left: 7px !important;
        padding-right: 7px !important;
    }
    [data-testid="stTextInput"] label,
    [data-testid="stNumberInput"] label,
    .salary-input-label {
        font-size: 8.6px !important;
        letter-spacing: .25px !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio percepito"]):has(input[aria-label="Quota stip. scelta"]):has(input[aria-label="Risparmi mese prec."]) {
        display: grid !important;
        grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
        gap: 6px !important;
        align-items: end !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio percepito"]):has(input[aria-label="Quota stip. scelta"]):has(input[aria-label="Risparmi mese prec."]) > div[data-testid="column"] {
        width: auto !important;
        min-width: 0 !important;
        max-width: 100% !important;
        flex: initial !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio percepito"]):has(input[aria-label="Quota stip. scelta"]):has(input[aria-label="Risparmi mese prec."]) [data-testid="stTextInput"] input,
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio percepito"]):has(input[aria-label="Quota stip. scelta"]):has(input[aria-label="Risparmi mese prec."]) [data-testid="stNumberInput"] input {
        height: 34px !important;
        min-height: 34px !important;
        font-size: 12px !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio percepito"]):has(input[aria-label="Quota stip. scelta"]):has(input[aria-label="Risparmi mese prec."]) [data-testid="stTextInput"] label,
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio percepito"]):has(input[aria-label="Quota stip. scelta"]):has(input[aria-label="Risparmi mese prec."]) [data-testid="stNumberInput"] label {
        min-height: 20px !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio percepito"]):has(input[aria-label="Quota stip. scelta"]):has(input[aria-label="Risparmi mese prec."]) [data-testid="stTextInput"],
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio percepito"]):has(input[aria-label="Quota stip. scelta"]):has(input[aria-label="Risparmi mese prec."]) [data-testid="stNumberInput"] {
        padding: 7px 8px 8px !important;
        border-radius: 12px !important;
        background:
            linear-gradient(135deg, rgba(96,165,250,.12), rgba(255,255,255,.035)),
            rgba(15,23,42,.36) !important;
        border: 0.5px solid rgba(96,165,250,.16) !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio percepito"]):has(input[aria-label="Quota stip. scelta"]):has(input[aria-label="Risparmi mese prec."]) [data-testid="stNumberInput"] button {
        min-width: 24px !important;
        width: 24px !important;
        min-height: 34px !important;
        padding: 0 !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio percepito"]):has(input[aria-label="Quota stip. scelta"]):has(input[aria-label="Risparmi mese prec."]) > div[data-testid="column"]:nth-child(1) [data-testid="stNumberInput"] {
        background:
            linear-gradient(135deg, rgba(52,211,153,.20), rgba(255,255,255,.035)),
            rgba(15,23,42,.36) !important;
        border-color: rgba(52,211,153,.30) !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio percepito"]):has(input[aria-label="Quota stip. scelta"]):has(input[aria-label="Risparmi mese prec."]) > div[data-testid="column"]:nth-child(1) [data-testid="stNumberInput"] label {
        color: rgba(134,239,172,.96) !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio percepito"]):has(input[aria-label="Quota stip. scelta"]):has(input[aria-label="Risparmi mese prec."]) > div[data-testid="column"]:nth-child(2) [data-testid="stNumberInput"] {
        background:
            linear-gradient(135deg, rgba(96,165,250,.22), rgba(255,255,255,.035)),
            rgba(15,23,42,.36) !important;
        border-color: rgba(96,165,250,.34) !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio percepito"]):has(input[aria-label="Quota stip. scelta"]):has(input[aria-label="Risparmi mese prec."]) > div[data-testid="column"]:nth-child(2) [data-testid="stNumberInput"] label {
        color: rgba(147,197,253,.98) !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio percepito"]):has(input[aria-label="Quota stip. scelta"]):has(input[aria-label="Risparmi mese prec."]) > div[data-testid="column"]:nth-child(3) [data-testid="stNumberInput"] {
        background:
            linear-gradient(135deg, rgba(250,204,21,.18), rgba(255,255,255,.035)),
            rgba(15,23,42,.36) !important;
        border-color: rgba(250,204,21,.30) !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio percepito"]):has(input[aria-label="Quota stip. scelta"]):has(input[aria-label="Risparmi mese prec."]) > div[data-testid="column"]:nth-child(3) [data-testid="stNumberInput"] label {
        color: rgba(253,224,71,.98) !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio (€)"]):has(input[aria-label="Risparmi mese prec. (€)"]):has(input[aria-label="Messi da parte (€)"]) {
        display: grid !important;
        grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
        gap: 6px !important;
        align-items: end !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio (€)"]):has(input[aria-label="Risparmi mese prec. (€)"]):has(input[aria-label="Messi da parte (€)"]) > div[data-testid="column"],
    div[data-testid="stHorizontalBlock"]:has(.mobile-stipendi-save-marker):has(.mobile-stipendi-delete-marker) > div[data-testid="column"] {
        width: auto !important;
        min-width: 0 !important;
        max-width: 100% !important;
        flex: initial !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio (€)"]):has(input[aria-label="Risparmi mese prec. (€)"]):has(input[aria-label="Messi da parte (€)"]) [data-testid="stNumberInput"] label {
        min-height: 18px !important;
        font-size: 7.8px !important;
        line-height: 1.05 !important;
        white-space: normal !important;
        letter-spacing: .15px !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio (€)"]):has(input[aria-label="Risparmi mese prec. (€)"]):has(input[aria-label="Messi da parte (€)"]) [data-testid="stNumberInput"] input {
        height: 32px !important;
        min-height: 32px !important;
        font-size: 10.5px !important;
        padding: 4px 6px !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Stipendio (€)"]):has(input[aria-label="Risparmi mese prec. (€)"]):has(input[aria-label="Messi da parte (€)"]) [data-testid="stNumberInput"] button {
        min-width: 19px !important;
        width: 19px !important;
        min-height: 32px !important;
        padding: 0 !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-stipendi-save-marker):has(.mobile-stipendi-delete-marker) {
        display: grid !important;
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: 8px !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-stipendi-save-marker):has(.mobile-stipendi-delete-marker) [data-testid="stButton"] button {
        min-height: 34px !important;
        padding: 0.35rem 0.45rem !important;
        font-size: 11px !important;
        white-space: nowrap !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Elettricità (€)"]):has(input[aria-label="Gas (€)"]):has(input[aria-label="Acqua (€)"]),
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Internet (€)"]):has(input[aria-label="Tari (€)"]) {
        display: grid !important;
        gap: 6px !important;
        align-items: end !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Elettricità (€)"]):has(input[aria-label="Gas (€)"]):has(input[aria-label="Acqua (€)"]) {
        grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Internet (€)"]):has(input[aria-label="Tari (€)"]),
    div[data-testid="stHorizontalBlock"]:has(.mobile-bollette-save-marker):has(.mobile-bollette-delete-marker) {
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Elettricità (€)"]):has(input[aria-label="Gas (€)"]):has(input[aria-label="Acqua (€)"]) > div[data-testid="column"],
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Internet (€)"]):has(input[aria-label="Tari (€)"]) > div[data-testid="column"],
    div[data-testid="stHorizontalBlock"]:has(.mobile-bollette-save-marker):has(.mobile-bollette-delete-marker) > div[data-testid="column"] {
        width: auto !important;
        min-width: 0 !important;
        max-width: 100% !important;
        flex: initial !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Elettricità (€)"]):has(input[aria-label="Gas (€)"]):has(input[aria-label="Acqua (€)"]) [data-testid="stNumberInput"] label,
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Internet (€)"]):has(input[aria-label="Tari (€)"]) [data-testid="stNumberInput"] label {
        min-height: 18px !important;
        font-size: 7.8px !important;
        line-height: 1.05 !important;
        white-space: normal !important;
        letter-spacing: .15px !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Elettricità (€)"]):has(input[aria-label="Gas (€)"]):has(input[aria-label="Acqua (€)"]) [data-testid="stNumberInput"] input,
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Internet (€)"]):has(input[aria-label="Tari (€)"]) [data-testid="stNumberInput"] input {
        height: 32px !important;
        min-height: 32px !important;
        font-size: 10.5px !important;
        padding: 4px 6px !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Elettricità (€)"]):has(input[aria-label="Gas (€)"]):has(input[aria-label="Acqua (€)"]) [data-testid="stNumberInput"] button,
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Internet (€)"]):has(input[aria-label="Tari (€)"]) [data-testid="stNumberInput"] button {
        min-width: 19px !important;
        width: 19px !important;
        min-height: 32px !important;
        padding: 0 !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-bollette-save-marker):has(.mobile-bollette-delete-marker) {
        display: grid !important;
        gap: 8px !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-bollette-save-marker):has(.mobile-bollette-delete-marker) [data-testid="stButton"] button {
        min-height: 34px !important;
        padding: 0.35rem 0.45rem !important;
        font-size: 11px !important;
        white-space: nowrap !important;
    }
    .mobile-salary-note-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 6px;
        width: 100%;
        margin-top: 4px;
        margin-bottom: 16px;
    }
    .mobile-salary-field-title {
        font-size: 12px;
        font-weight: 900;
        letter-spacing: .15px;
        line-height: 1.05;
        margin: 0 0 7px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .mobile-salary-field-title.green { color: #86efac; }
    .mobile-salary-field-title.blue { color: #93c5fd; }
    .mobile-salary-field-title.yellow { color: #fde047; }
    .mobile-kpi-summary-grid {
        display: grid;
        grid-template-columns: 1fr;
        gap: 7px;
        width: 100%;
        max-width: 100%;
        height: 100%;
    }
    .mobile-kpi-summary-grid .kpi-card {
        min-height: 76px !important;
        padding: 10px 10px !important;
        margin-bottom: 0 !important;
    }
    .mobile-kpi-summary-grid .kpi-value {
        font-size: 18px !important;
        line-height: 1.12 !important;
    }
    .mobile-kpi-summary-grid .kpi-label {
        font-size: 9px !important;
        line-height: 1.15 !important;
    }
    .mobile-bollette-kpi-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
        width: 100%;
        max-width: 100%;
        align-items: stretch;
    }
    .mobile-bollette-kpi-grid .kpi-card {
        min-height: 74px !important;
        margin: 0 !important;
        padding: 10px 10px !important;
    }
    .mobile-bollette-kpi-grid .kpi-label {
        font-size: 8.5px !important;
        line-height: 1.12 !important;
    }
    .mobile-bollette-kpi-grid .kpi-value {
        font-size: 15px !important;
        line-height: 1.12 !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-budget-left-marker):has(.mobile-budget-right-marker) {
        display: grid !important;
        grid-template-columns: minmax(0, .98fr) minmax(0, 1.02fr) !important;
        gap: 8px !important;
        align-items: stretch !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-budget-left-marker):has(.mobile-budget-right-marker) > div[data-testid="column"] {
        width: auto !important;
        min-width: 0 !important;
        max-width: 100% !important;
        flex: initial !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-budget-left-marker):has(.mobile-budget-right-marker) .budget-memory-card {
        min-height: 122px !important;
        height: auto !important;
        padding: 9px 10px 7px !important;
        margin-bottom: 0 !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-budget-left-marker):has(.mobile-budget-right-marker) .budget-memory-title {
        font-size: 10px !important;
        margin-bottom: 3px !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-budget-left-marker):has(.mobile-budget-right-marker) .budget-memory-row {
        padding: 5px 0 !important;
        gap: 6px !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-budget-left-marker):has(.mobile-budget-right-marker) .budget-memory-label {
        font-size: 9px !important;
        line-height: 1.17 !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-budget-left-marker):has(.mobile-budget-right-marker) .budget-memory-value {
        font-size: 11px !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-budget-left-marker):has(.mobile-budget-right-marker) [data-testid="stExpander"] {
        margin-top: 6px !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-budget-left-marker):has(.mobile-budget-right-marker) [data-testid="stExpander"] summary {
        min-height: 34px !important;
        font-size: 11px !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Nome nuova spesa"]):has(input[aria-label="Importo nuova spesa"]):has(input[aria-label="Nuovo gruppo visivo da aggiungere"]),
    div[data-testid="stHorizontalBlock"]:has([aria-label="Colore categoria nuova spesa"]):has([aria-label="Carta nuova spesa"]):has([aria-label="Gruppo visivo nuova spesa"]) {
        display: grid !important;
        grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
        gap: 6px !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
        align-items: end !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Nome nuova spesa"]):has(input[aria-label="Importo nuova spesa"]):has(input[aria-label="Nuovo gruppo visivo da aggiungere"]) > div[data-testid="column"],
    div[data-testid="stHorizontalBlock"]:has([aria-label="Colore categoria nuova spesa"]):has([aria-label="Carta nuova spesa"]):has([aria-label="Gruppo visivo nuova spesa"]) > div[data-testid="column"] {
        width: auto !important;
        min-width: 0 !important;
        max-width: 100% !important;
        flex: initial !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Nome nuova spesa"]):has(input[aria-label="Importo nuova spesa"]):has(input[aria-label="Nuovo gruppo visivo da aggiungere"]) label,
    div[data-testid="stHorizontalBlock"]:has([aria-label="Colore categoria nuova spesa"]):has([aria-label="Carta nuova spesa"]):has([aria-label="Gruppo visivo nuova spesa"]) label,
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Importo"]):has([aria-label="Colore categoria"]):has([aria-label="Gruppo visivo"]) label {
        min-height: 18px !important;
        font-size: 8px !important;
        line-height: 1.05 !important;
        letter-spacing: .2px !important;
        white-space: normal !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Nome nuova spesa"]):has(input[aria-label="Importo nuova spesa"]):has(input[aria-label="Nuovo gruppo visivo da aggiungere"]) input,
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Importo"]):has([aria-label="Colore categoria"]):has([aria-label="Gruppo visivo"]) input {
        height: 32px !important;
        min-height: 32px !important;
        font-size: 10.5px !important;
        padding: 4px 6px !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Nome nuova spesa"]):has(input[aria-label="Importo nuova spesa"]):has(input[aria-label="Nuovo gruppo visivo da aggiungere"]) [data-testid="stNumberInput"] button,
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Importo"]):has([aria-label="Colore categoria"]):has([aria-label="Gruppo visivo"]) [data-testid="stNumberInput"] button {
        min-width: 19px !important;
        width: 19px !important;
        min-height: 32px !important;
        padding: 0 !important;
    }
    div[data-testid="stHorizontalBlock"]:has([aria-label="Colore categoria nuova spesa"]):has([aria-label="Carta nuova spesa"]):has([aria-label="Gruppo visivo nuova spesa"]) [data-testid="stSelectbox"],
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Importo"]):has([aria-label="Colore categoria"]):has([aria-label="Gruppo visivo"]) [data-testid="stSelectbox"],
    div[data-testid="stHorizontalBlock"]:has([aria-label="Colore categoria nuova spesa"]):has([aria-label="Carta nuova spesa"]):has([aria-label="Gruppo visivo nuova spesa"]) [data-baseweb="select"],
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Importo"]):has([aria-label="Colore categoria"]):has([aria-label="Gruppo visivo"]) [data-baseweb="select"] {
        min-width: 0 !important;
        width: 100% !important;
        max-width: 100% !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Importo"]):has([aria-label="Colore categoria"]):has([aria-label="Gruppo visivo"]) {
        display: grid !important;
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: 8px !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
        align-items: start !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Importo"]):has([aria-label="Colore categoria"]):has([aria-label="Gruppo visivo"]) > div[data-testid="column"] {
        width: auto !important;
        min-width: 0 !important;
        max-width: 100% !important;
        flex: initial !important;
    }
    div[data-testid="stHorizontalBlock"]:has(input[aria-label="Importo"]):has([aria-label="Colore categoria"]):has([aria-label="Gruppo visivo"]) p {
        font-size: 12px !important;
        line-height: 1.1 !important;
        margin-bottom: 4px !important;
    }
    .fixed-expense-add-main-marker,
    .fixed-expense-add-meta-marker,
    .fixed-expense-actions-marker,
    .fixed-expense-editor-marker,
    .other-income-actions-marker,
    .other-income-editor-marker,
    .other-income-new-marker,
    .turni-mode-marker,
    .turni-day-menu-marker,
    .mobile-calendar-nav-marker,
    .mobile-calendar-row-marker,
    .turni-rules-marker {
        display: none !important;
    }
    div[data-testid="stMarkdown"]:has(.fixed-expense-actions-marker),
    div[data-testid="stMarkdown"]:has(.fixed-expense-editor-marker),
    div[data-testid="stMarkdown"]:has(.other-income-actions-marker),
    div[data-testid="stMarkdown"]:has(.other-income-editor-marker),
    div[data-testid="stMarkdown"]:has(.other-income-new-marker),
    div[data-testid="stMarkdown"]:has(.turni-mode-marker),
    div[data-testid="stMarkdown"]:has(.turni-day-menu-marker),
    div[data-testid="stMarkdown"]:has(.mobile-calendar-nav-marker),
    div[data-testid="stMarkdown"]:has(.mobile-calendar-row-marker),
    div[data-testid="stMarkdown"]:has(.turni-rules-marker) {
        display: none !important;
        height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-add-main-marker),
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-add-meta-marker) {
        display: grid !important;
        grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
        gap: 6px !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
        align-items: end !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-editor-marker) {
        display: grid !important;
        grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
        gap: 8px !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
        align-items: start !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.other-income-editor-marker) {
        display: grid !important;
        grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
        gap: 8px !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
        align-items: start !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.other-income-new-marker) {
        display: grid !important;
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: 8px !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
        align-items: end !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-actions-marker),
    div[data-testid="stHorizontalBlock"]:has(.other-income-actions-marker) {
        display: grid !important;
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: 8px !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
        align-items: stretch !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.turni-mode-marker) {
        display: grid !important;
        grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
        gap: 6px !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
        align-items: center !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-calendar-nav-marker) {
        display: grid !important;
        grid-template-columns: 42px minmax(0, 1fr) 42px !important;
        gap: 8px !important;
        align-items: center !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-calendar-row-marker) {
        display: grid !important;
        grid-template-columns: repeat(7, minmax(0, 1fr)) !important;
        gap: 7px !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
        align-items: stretch !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.turni-day-menu-marker) {
        display: grid !important;
        grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
        gap: 8px !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
        align-items: end !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.turni-rules-marker) {
        display: grid !important;
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: 8px !important;
        width: 100% !important;
        max-width: 100% !important;
        overflow: hidden !important;
        align-items: start !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-add-main-marker) > div[data-testid="column"],
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-add-meta-marker) > div[data-testid="column"],
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-actions-marker) > div[data-testid="column"],
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-editor-marker) > div[data-testid="column"],
    div[data-testid="stHorizontalBlock"]:has(.other-income-actions-marker) > div[data-testid="column"],
    div[data-testid="stHorizontalBlock"]:has(.other-income-editor-marker) > div[data-testid="column"],
    div[data-testid="stHorizontalBlock"]:has(.other-income-new-marker) > div[data-testid="column"],
    div[data-testid="stHorizontalBlock"]:has(.mobile-calendar-nav-marker) > div[data-testid="column"],
    div[data-testid="stHorizontalBlock"]:has(.mobile-calendar-row-marker) > div[data-testid="column"],
    div[data-testid="stHorizontalBlock"]:has(.turni-day-menu-marker) > div[data-testid="column"],
    div[data-testid="stHorizontalBlock"]:has(.turni-mode-marker) > div[data-testid="column"],
    div[data-testid="stHorizontalBlock"]:has(.turni-rules-marker) > div[data-testid="column"] {
        width: auto !important;
        min-width: 0 !important;
        max-width: 100% !important;
        flex: initial !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-actions-marker) [data-testid="stButton"] button,
    div[data-testid="stHorizontalBlock"]:has(.other-income-actions-marker) [data-testid="stButton"] button {
        min-height: 38px !important;
        padding: 6px 6px !important;
        font-size: 10.5px !important;
        line-height: 1.1 !important;
        white-space: normal !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-add-main-marker) label,
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-add-meta-marker) label,
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-editor-marker) label,
    div[data-testid="stHorizontalBlock"]:has(.other-income-editor-marker) label,
    div[data-testid="stHorizontalBlock"]:has(.other-income-new-marker) label,
    div[data-testid="stHorizontalBlock"]:has(.turni-mode-marker) label,
    div[data-testid="stHorizontalBlock"]:has(.turni-rules-marker) label {
        min-height: 17px !important;
        font-size: 7.8px !important;
        line-height: 1.05 !important;
        letter-spacing: .18px !important;
        white-space: normal !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.turni-mode-marker) label {
        min-height: 30px !important;
        font-size: 9px !important;
        white-space: nowrap !important;
        display: flex !important;
        align-items: center !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.turni-day-menu-marker) label {
        min-height: 17px !important;
        font-size: 8px !important;
        line-height: 1.05 !important;
        white-space: normal !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-add-main-marker) input,
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-editor-marker) input,
    div[data-testid="stHorizontalBlock"]:has(.other-income-editor-marker) input,
    div[data-testid="stHorizontalBlock"]:has(.other-income-new-marker) input,
    div[data-testid="stHorizontalBlock"]:has(.turni-day-menu-marker) input,
    div[data-testid="stHorizontalBlock"]:has(.turni-rules-marker) input {
        height: 32px !important;
        min-height: 32px !important;
        font-size: 10.5px !important;
        padding: 4px 6px !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-add-main-marker) [data-testid="stNumberInput"] button,
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-editor-marker) [data-testid="stNumberInput"] button,
    div[data-testid="stHorizontalBlock"]:has(.other-income-editor-marker) [data-testid="stNumberInput"] button,
    div[data-testid="stHorizontalBlock"]:has(.other-income-new-marker) [data-testid="stNumberInput"] button,
    div[data-testid="stHorizontalBlock"]:has(.turni-day-menu-marker) [data-testid="stNumberInput"] button,
    div[data-testid="stHorizontalBlock"]:has(.turni-rules-marker) [data-testid="stNumberInput"] button {
        min-width: 19px !important;
        width: 19px !important;
        min-height: 32px !important;
        padding: 0 !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-add-meta-marker) [data-testid="stSelectbox"],
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-editor-marker) [data-testid="stSelectbox"],
    div[data-testid="stHorizontalBlock"]:has(.other-income-editor-marker) [data-testid="stSelectbox"],
    div[data-testid="stHorizontalBlock"]:has(.turni-day-menu-marker) [data-testid="stSelectbox"],
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-add-meta-marker) [data-baseweb="select"],
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-editor-marker) [data-baseweb="select"],
    div[data-testid="stHorizontalBlock"]:has(.other-income-editor-marker) [data-baseweb="select"],
    div[data-testid="stHorizontalBlock"]:has(.turni-day-menu-marker) [data-baseweb="select"] {
        min-width: 0 !important;
        width: 100% !important;
        max-width: 100% !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.fixed-expense-editor-marker) p,
    div[data-testid="stHorizontalBlock"]:has(.other-income-editor-marker) p {
        font-size: 12px !important;
        line-height: 1.1 !important;
        margin-bottom: 4px !important;
    }
    .mobile-notes-html-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
        width: 100%;
        max-width: 100%;
        margin-bottom: 10px;
    }
    .mobile-notes-html-grid .memo-card {
        min-width: 0;
        min-height: 132px;
        padding: 10px 9px;
        margin: 0;
        border-radius: 12px;
    }
    .mobile-notes-html-grid .memo-card-title {
        font-size: 9.5px;
        letter-spacing: .55px;
        margin-bottom: 7px;
    }
    .mobile-notes-html-grid .memo-card-preview {
        min-height: 78px;
        max-height: 92px;
        overflow: hidden;
        font-size: 10px;
        line-height: 1.3;
    }
    .mobile-objective-block {
        margin-top: 14px;
    }
    .mobile-objective-title {
        color: rgba(255,255,255,.90);
        font-size: 18px;
        font-weight: 900;
        margin: 0 0 10px;
        line-height: 1.15;
    }
    .mobile-objective-metric {
        margin: 7px 0;
        line-height: 1.25;
    }
    .mobile-objective-label {
        font-size: 10px;
        color: rgba(255,255,255,.44);
        text-transform: uppercase;
        letter-spacing: .55px;
    }
    .mobile-objective-value {
        font-size: 15px;
        font-weight: 900;
        color: rgba(255,255,255,.92);
    }
    .mobile-progress {
        height: 7px;
        border-radius: 999px;
        overflow: hidden;
        background: rgba(255,255,255,.10);
        margin: 10px 0 5px;
    }
    .mobile-progress-fill {
        height: 100%;
        border-radius: 999px;
        background: #1d9bf0;
    }
    @media (max-width: 767px) {
        .block-container {
            width: 100% !important;
            max-width: 100vw !important;
            padding-left: 0.7rem !important;
            padding-right: 0.7rem !important;
        }
        div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]:nth-child(2)):not(:has(> div[data-testid="column"]:nth-child(3))) {
            display: grid !important;
            grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
            gap: 8px !important;
            width: 100% !important;
            max-width: 100% !important;
            overflow: hidden !important;
            align-items: start !important;
        }
        div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]:nth-child(3)):not(:has(> div[data-testid="column"]:nth-child(4))) {
            display: grid !important;
            grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
            gap: 6px !important;
            width: 100% !important;
            max-width: 100% !important;
            overflow: hidden !important;
            align-items: start !important;
        }
        div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]:nth-child(2)):not(:has(> div[data-testid="column"]:nth-child(3))) > div[data-testid="column"],
        div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]:nth-child(3)):not(:has(> div[data-testid="column"]:nth-child(4))) > div[data-testid="column"] {
            width: auto !important;
            min-width: 0 !important;
            max-width: 100% !important;
            flex: initial !important;
        }
        div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]:nth-child(3)):not(:has(> div[data-testid="column"]:nth-child(4))) [data-testid="stTextInput"] label,
        div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]:nth-child(3)):not(:has(> div[data-testid="column"]:nth-child(4))) [data-testid="stNumberInput"] label,
        div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]:nth-child(3)):not(:has(> div[data-testid="column"]:nth-child(4))) [data-testid="stSelectbox"] label {
            min-height: 17px !important;
            font-size: 7.8px !important;
            line-height: 1.05 !important;
            white-space: normal !important;
            letter-spacing: .16px !important;
        }
        div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]:nth-child(3)):not(:has(> div[data-testid="column"]:nth-child(4))) [data-testid="stTextInput"] input,
        div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]:nth-child(3)):not(:has(> div[data-testid="column"]:nth-child(4))) [data-testid="stNumberInput"] input {
            min-height: 32px !important;
            height: 32px !important;
            font-size: 10.5px !important;
            padding: 4px 6px !important;
        }
        div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]:nth-child(3)):not(:has(> div[data-testid="column"]:nth-child(4))) [data-testid="stNumberInput"] button {
            min-width: 19px !important;
            width: 19px !important;
            min-height: 32px !important;
            padding: 0 !important;
        }
        div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]:nth-child(3)):not(:has(> div[data-testid="column"]:nth-child(4))) [data-testid="stSelectbox"],
        div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]:nth-child(3)):not(:has(> div[data-testid="column"]:nth-child(4))) [data-baseweb="select"] {
            min-width: 0 !important;
            width: 100% !important;
            max-width: 100% !important;
        }
        .mobile-home-grid {
            max-width: 100% !important;
            overflow: hidden !important;
        }
        .mobile-home-card {
            min-width: 0 !important;
            padding: 11px 12px !important;
        }
    }
    h1 {
        font-size: 1.45rem !important;
        text-align: left !important;
        line-height: 1.15 !important;
    }
    h2, h3 {
        font-size: 1.18rem !important;
        line-height: 1.2 !important;
    }
    .kpi-card {
        padding: 0.8rem 0.9rem !important;
        margin-bottom: 8px !important;
        height: 100% !important;
    }
    .kpi-value {
        font-size: 19px !important;
    }
    .section-pill {
        margin-top: 12px !important;
        margin-bottom: 8px !important;
    }
    [data-testid="stTabs"] [role="tablist"] {
        overflow-x: auto !important;
        flex-wrap: nowrap !important;
    }
    .mobile-home-title {
        font-size: 1.45rem;
        font-weight: 900;
        text-align: center;
        color: #dbeafe;
        margin: 6px 0 14px;
        line-height: 1.15;
        background: linear-gradient(90deg, #60a5fa, #a78bfa, #5eead4);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .mobile-home-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 10px;
        margin: 4px 0 18px;
    }
    .mobile-home-card,
    .mobile-card-caption {
        display: block;
        min-height: 78px;
        padding: 13px 14px;
        border-radius: 14px;
        text-decoration: none !important;
        background:
            linear-gradient(135deg, color-mix(in srgb, var(--section-color) 18%, transparent), rgba(255,255,255,.035));
        border: 0.5px solid color-mix(in srgb, var(--section-color) 42%, rgba(255,255,255,.12));
        border-left: 4px solid var(--section-color);
        box-shadow: 0 10px 24px rgba(0,0,0,.18);
    }
    .mobile-card-caption {
        min-height: 68px;
        margin: 0 0 6px;
    }
    .mobile-home-card.panoramica,
    .mobile-card-caption.panoramica { --section-color:#38bdf8; }
    .mobile-home-card.spese,
    .mobile-card-caption.spese,
    .mobile-nav a.spese { --section-color:#f87171; }
    .mobile-home-card.variabili,
    .mobile-card-caption.variabili { --section-color:#4ade80; }
    .mobile-home-card.entrate,
    .mobile-card-caption.entrate,
    .mobile-nav a.entrate { --section-color:#34d399; }
    .mobile-home-card.risparmi,
    .mobile-card-caption.risparmi,
    .mobile-nav a.risparmi { --section-color:#facc15; }
    .mobile-home-card.carte,
    .mobile-card-caption.carte { --section-color:#89cff0; }
    .mobile-home-card.promemoria,
    .mobile-card-caption.promemoria { --section-color:#fde68a; }
    .mobile-home-card.turni,
    .mobile-card-caption.turni,
    .mobile-nav a.turni { --section-color:#60a5fa; }
    .mobile-home-card.storico,
    .mobile-card-caption.storico,
    .mobile-nav a.storico { --section-color:#a78bfa; }
    .mobile-home-card.bollette,
    .mobile-card-caption.bollette,
    .mobile-nav a.bollette { --section-color:#fb923c; }
    .mobile-nav a.panoramica { --section-color:#38bdf8; }
    .mobile-nav a.variabili { --section-color:#4ade80; }
    .mobile-nav a.carte { --section-color:#89cff0; }
    .mobile-home-card.active,
    .mobile-card-caption.active {
        background:
            linear-gradient(135deg, color-mix(in srgb, var(--section-color) 32%, transparent), rgba(255,255,255,.06));
        border-color: color-mix(in srgb, var(--section-color) 72%, rgba(255,255,255,.16));
        box-shadow: 0 0 0 1px color-mix(in srgb, var(--section-color) 38%, transparent), 0 14px 30px rgba(0,0,0,.22);
    }
    .mobile-home-card strong,
    .mobile-card-caption strong {
        display: block;
        color: rgba(255,255,255,.94);
        font-size: 14px;
        line-height: 1.2;
        margin-bottom: 6px;
    }
    .mobile-home-card span,
    .mobile-card-caption span {
        color: rgba(255,255,255,.46);
        font-size: 11px;
        line-height: 1.25;
    }
    div[data-testid="stButton"] > button[kind="secondary"] {
        min-height: 34px !important;
        border-radius: 11px !important;
        background: rgba(30,64,105,.50) !important;
        border: 0.5px solid rgba(96,165,250,.30) !important;
        color: rgba(219,234,254,.94) !important;
        font-size: 11px !important;
        font-weight: 800 !important;
    }
    div[data-testid="stRadio"] [role="radiogroup"] {
        display: grid !important;
        grid-template-columns: repeat(8, minmax(0, 1fr)) !important;
        gap: 8px 9px !important;
        align-items: stretch !important;
        justify-content: start !important;
    }
    div[data-testid="stRadio"] [role="radiogroup"] > label {
        min-width: 0 !important;
        width: 100% !important;
        max-width: 100% !important;
        margin: 0 !important;
        padding: 0 !important;
    }
    div[data-testid="stRadio"] [role="radiogroup"] > label > div:first-child,
    div[data-testid="stRadio"] [role="radiogroup"] > label input[type="radio"],
    div[data-testid="stRadio"] [role="radiogroup"] > label [data-baseweb="radio"],
    div[data-testid="stRadio"] [role="radiogroup"] > label [data-testid="stMarkdownContainer"] + div,
    div[data-testid="stRadio"] [role="radiogroup"] > label > div:last-child > div:not([data-testid="stMarkdownContainer"]),
    div[data-testid="stRadio"] [role="radiogroup"] > label svg,
    div[data-testid="stRadio"] [role="radiogroup"] > label [role="radio"] {
        display: none !important;
        width: 0 !important;
        min-width: 0 !important;
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        opacity: 0 !important;
        pointer-events: none !important;
    }
    div[data-testid="stRadio"] [role="radiogroup"] > label > div:last-child {
        width: 100% !important;
        max-width: 100% !important;
        min-height: 38px !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        text-align: center !important;
        padding: 6px 3px !important;
        border-radius: 11px !important;
        border: 0.5px solid color-mix(in srgb, var(--mobile-radio-color, #60a5fa) 52%, rgba(255,255,255,.13)) !important;
        border-bottom: 3px solid var(--mobile-radio-color, #60a5fa) !important;
        background: linear-gradient(135deg, color-mix(in srgb, var(--mobile-radio-color, #60a5fa) 26%, rgba(15,23,42,.92)), rgba(255,255,255,.035)) !important;
        color: rgba(255,255,255,.90) !important;
        font-size: 8.5px !important;
        font-weight: 900 !important;
        line-height: 1.05 !important;
        box-shadow: 0 8px 18px rgba(0,0,0,.16) !important;
    }
    div[data-testid="stRadio"] [role="radiogroup"] > label:has(input:checked) > div:last-child {
        background: linear-gradient(135deg, color-mix(in srgb, var(--mobile-radio-color, #60a5fa) 48%, rgba(15,23,42,.88)), rgba(255,255,255,.08)) !important;
        box-shadow: 0 0 0 1px color-mix(in srgb, var(--mobile-radio-color, #60a5fa) 48%, transparent), 0 10px 22px rgba(0,0,0,.22) !important;
        color: #ffffff !important;
    }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(1) { --mobile-radio-color:#38bdf8; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(2) { --mobile-radio-color:#f87171; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(3) { --mobile-radio-color:#4ade80; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(4) { --mobile-radio-color:#34d399; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(5) { --mobile-radio-color:#facc15; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(6) { --mobile-radio-color:#89cff0; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(7) { --mobile-radio-color:#fde68a; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(8) { --mobile-radio-color:#60a5fa; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(9) { --mobile-radio-color:#a78bfa; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(10) { --mobile-radio-color:#fb923c; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(1) { grid-column:1 / span 2; grid-row:1; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(2) { grid-column:4; grid-row:1; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(3) { grid-column:5; grid-row:1; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(4) { grid-column:7; grid-row:1; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(5) { grid-column:8; grid-row:1; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(6) { grid-column:1; grid-row:2; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(7) {
        grid-column:2;
        grid-row:2;
        width: 100% !important;
        justify-self: start !important;
    }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(7) > div:last-child {
        max-width: 100% !important;
    }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(8) { grid-column:4 / span 2; grid-row:2; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(9) { grid-column:7; grid-row:2; }
    div[data-testid="stRadio"] [role="radiogroup"] > label:nth-child(10) { grid-column:8; grid-row:2; }
    .mobile-panorama-budget-row [data-testid="column"] {
        min-width: 0 !important;
        width: 100% !important;
    }
    .mobile-panorama-budget-row .kpi-card {
        min-height: 112px !important;
        padding: 12px 13px !important;
    }
    .mobile-panorama-budget-row .budget-memory-card {
        min-height: 184px !important;
        height: 100% !important;
        padding: 10px 11px 9px !important;
    }
    .mobile-panorama-budget-row .budget-memory-title {
        font-size: 10px !important;
        margin-bottom: 6px !important;
    }
    .mobile-panorama-budget-row .budget-memory-row {
        gap: 8px !important;
        padding: 8px 0 !important;
        align-items: center !important;
    }
    .mobile-panorama-budget-row .budget-memory-label {
        font-size: 9.5px !important;
        line-height: 1.2 !important;
    }
    .mobile-panorama-budget-row .budget-memory-value {
        font-size: 12px !important;
    }
    .mobile-panorama-budget-row [data-testid="stExpander"] {
        margin-top: 6px !important;
    }
    .mobile-panorama-budget-row [data-testid="stExpander"] summary {
        min-height: 36px !important;
        font-size: 11px !important;
    }
    .mobile-nav {
        display: flex;
        gap: 7px;
        overflow-x: auto;
        padding: 2px 0 10px;
        margin: 0 0 12px;
    }
    .mobile-nav a {
        flex: 0 0 auto;
        text-decoration: none;
        color: rgba(255,255,255,.88);
        background: color-mix(in srgb, var(--section-color) 16%, rgba(15,23,42,.75));
        border: 0.5px solid color-mix(in srgb, var(--section-color) 48%, rgba(255,255,255,.12));
        border-bottom: 3px solid var(--section-color);
        border-radius: 999px;
        padding: 7px 11px 6px;
        font-size: 12px;
        font-weight: 850;
        white-space: nowrap;
        box-shadow: 0 8px 18px rgba(0,0,0,.16);
    }
    .mobile-anchor {
        scroll-margin-top: 22px;
    }
    .mobile-anchor:not(#mobile-top):not(#mobile-dashboard) {
        display:block;
        border-top: 1px solid rgba(255,255,255,.08);
        margin-top: 18px;
        padding-top: 10px;
    }
    #mobile-spese { border-top-color: rgba(248,113,113,.48); }
    #mobile-variabili { border-top-color: rgba(74,222,128,.48); }
    #mobile-entrate { border-top-color: rgba(52,211,153,.48); }
    #mobile-risparmi { border-top-color: rgba(250,204,21,.48); }
    #mobile-carte { border-top-color: rgba(137,207,240,.48); }
    #mobile-turni { border-top-color: rgba(96,165,250,.48); }
    #mobile-promemoria { border-top-color: rgba(253,230,138,.44); }
    #mobile-stipendi { border-top-color: rgba(167,139,250,.48); }
    #mobile-bollette { border-top-color: rgba(251,146,60,.48); }
    section[data-testid="stSidebar"] [data-testid="stSelectbox"] label {
        color: rgba(255,255,255,.78) !important;
        font-weight: 900 !important;
        letter-spacing: .5px !important;
    }
    section[data-testid="stSidebar"] [data-baseweb="select"] > div {
        background:
            linear-gradient(135deg, rgba(56,189,248,.16), rgba(167,139,250,.10)),
            rgba(15,23,42,.82) !important;
        border: 1px solid rgba(96,165,250,.34) !important;
        border-radius: 12px !important;
        box-shadow: 0 10px 22px rgba(0,0,0,.20) !important;
    }
    [data-testid="stVegaLiteChart"] {
        overflow-x: auto !important;
    }
    [data-testid="stVegaLiteChart"] > div {
        min-width: min(100%, 560px) !important;
    }
    .mobile-calendar-grid {
        display: grid;
        grid-template-columns: repeat(7, minmax(0, 1fr));
        gap: 7px;
        margin-top: 10px;
    }
    .mobile-calendar-navline {
        display: grid;
        grid-template-columns: 42px minmax(0, 1fr) 42px;
        gap: 8px;
        align-items: center;
        margin: 12px 0 10px;
    }
    .mobile-calendar-arrow {
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: 38px;
        border-radius: 10px;
        background: rgba(30,58,92,.82);
        border: 0.5px solid rgba(96,165,250,.44);
        color: #9bd0ff !important;
        text-decoration: none !important;
        font-size: 18px;
        font-weight: 900;
    }
    .mobile-calendar-title {
        min-width: 0;
        text-align: center;
        color: rgba(255,255,255,.92);
        font-size: 22px;
        line-height: 1.1;
        font-weight: 900;
        white-space: nowrap;
    }
    .mobile-calendar-head {
        text-align: center;
        color: rgba(255,255,255,.46);
        font-size: 11px;
        font-weight: 800;
        padding-bottom: 2px;
    }
    .mobile-calendar-day {
        min-height: 42px;
        border-radius: 10px;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 2px;
        background: rgba(30,58,92,.70);
        border: 0.5px solid rgba(96,165,250,.36);
        color: #d1d5db;
        font-size: 13px;
        font-weight: 800;
        line-height: 1;
        text-decoration: none !important;
    }
    .mobile-calendar-day.selected {
        border-color: rgba(125,211,252,.70);
        box-shadow: 0 0 0 1px rgba(125,211,252,.44), 0 0 14px rgba(96,165,250,.16);
    }
    a.mobile-calendar-day:hover {
        border-color: rgba(125,211,252,.62);
        background: rgba(30,64,115,.82);
        text-decoration: none !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-calendar-row-marker) [data-testid="stButton"] button {
        min-height: 42px !important;
        height: 42px !important;
        border-radius: 10px !important;
        padding: 0 2px !important;
        background: rgba(30,58,92,.70) !important;
        border: 0.5px solid rgba(96,165,250,.36) !important;
        color: #d1d5db !important;
        font-size: 12px !important;
        font-weight: 900 !important;
        line-height: 1 !important;
        white-space: nowrap !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-calendar-row-marker) [data-testid="stButton"] button:hover {
        border-color: rgba(125,211,252,.62) !important;
        background: rgba(30,64,115,.82) !important;
    }
    .mobile-calendar-day.empty {
        background: transparent;
        border-color: transparent;
    }
    .mobile-calendar-day .holiday {
        color: #ff626f;
    }
    .mobile-calendar-day .today-dot {
        color: #fb923c;
        margin-right: 1px;
        font-size: 10px;
    }
    .mobile-calendar-day .shift {
        font-size: 14px;
        font-weight: 1000;
        text-shadow: 0 0 2px color-mix(in srgb, currentColor 35%, transparent);
    }
    .mobile-day-extra,
    .mobile-day-sede {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 10px;
        height: 10px;
        border-radius: 4px;
        margin-left: 1px;
        font-size: 7px;
        line-height: 1;
        font-weight: 1000;
    }
    .mobile-day-extra {
        color: #f5d0fe;
        background: rgba(168,85,247,.24);
        border: 0.5px solid rgba(216,180,254,.45);
    }
    .mobile-day-sede {
        color: #fef3c7;
        background: rgba(245,158,11,.20);
        border: 0.5px solid rgba(251,191,36,.40);
    }
    .mobile-calendar-legend {
        display:flex;
        gap:8px;
        flex-wrap:wrap;
        margin-top:10px;
        font-size:12px;
        color:rgba(255,255,255,.62);
    }
    .mobile-donut-card {
        margin: 4px 0 10px;
        padding: 10px;
        border-radius: 13px;
        background: rgba(255,255,255,.045);
        border: 0.5px solid rgba(255,255,255,.10);
    }
    .mobile-variabili-grid {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 40%;
        gap: 10px;
        align-items: start;
        margin-top: 8px;
    }
    .mobile-variabili-list {
        min-width: 0;
    }
    .mobile-variabili-chart {
        min-width: 0;
    }
    .mobile-variabili-grid .mobile-donut-card {
        margin: 0;
        padding: 9px;
    }
    .mobile-side-grid {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 40%;
        gap: 10px;
        align-items: start;
        margin: 8px 0 10px;
    }
    .mobile-altre-entrate-grid {
        align-items: center;
    }
    .mobile-altre-entrate-grid > div:nth-child(2) {
        align-self: center;
    }
    .mobile-altre-top-grid,
    .mobile-altre-bottom-grid {
        display: grid;
        grid-template-columns: minmax(0, 1fr) 40%;
        gap: 10px;
        align-items: start;
        margin: 6px 0 8px;
        width: 100%;
        max-width: 100%;
    }
    .mobile-altre-top-grid > div,
    .mobile-altre-bottom-grid > div {
        min-width: 0;
    }
    .mobile-altre-top-grid h3,
    .mobile-altre-top-grid .mobile-objective-title {
        margin-top: 0 !important;
        padding-top: 0 !important;
    }
    .mobile-altre-bottom-grid {
        align-items: start;
    }
    .mobile-altre-bottom-grid .kpi-card {
        margin-top: 0 !important;
    }
    .mobile-altre-bottom-grid .mobile-donut-card {
        margin: 0;
    }
    .mobile-side-grid .mobile-donut-card {
        margin: 0;
        padding: 9px;
    }
    .mobile-three-donut-row {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 6px;
        margin: 10px 0 12px;
        width: 100%;
        max-width: 100%;
    }
    .mobile-three-donut-row .mobile-donut-card {
        min-width: 0;
        margin: 0;
        padding: 7px 6px;
    }
    .mobile-three-donut-row .mobile-donut-title {
        font-size: 8px;
        line-height: 1.1;
        min-height: 18px;
        margin-bottom: 5px;
    }
    .mobile-three-donut-row .mobile-donut-ring {
        width: 52px;
        height: 52px;
    }
    .mobile-three-donut-row .mobile-donut-hole {
        width: 30px;
        height: 30px;
    }
    .mobile-three-donut-row .mobile-donut-legend {
        gap: 3px;
    }
    .mobile-three-donut-row .mobile-donut-legend-row {
        grid-template-columns: 6px minmax(0, 1fr);
        gap: 3px;
    }
    .mobile-three-donut-row .mobile-donut-dot {
        width: 5px;
        height: 5px;
    }
    .mobile-three-donut-row .mobile-donut-label {
        font-size: 7px;
        line-height: 1.08;
    }
    .mobile-fixed-expenses-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px 12px;
        align-items: start;
    }
    .mobile-fixed-expenses-col {
        min-width: 0;
    }
    .mobile-donut-title {
        font-size: 11px;
        line-height: 1.15;
        font-weight: 900;
        letter-spacing: .2px;
        color: rgba(255,255,255,.86);
        margin-bottom: 8px;
        white-space: normal;
    }
    .mobile-donut-body {
        display: grid;
        grid-template-columns: 1fr;
        gap: 7px;
        align-items: center;
    }
    .mobile-donut-ring {
        width: 68px;
        height: 68px;
        border-radius: 999px;
        display: grid;
        place-items: center;
        box-shadow: inset 0 0 0 1px rgba(255,255,255,.06);
        margin: 0 auto;
    }
    .mobile-donut-hole {
        width: 38px;
        height: 38px;
        border-radius: 999px;
        background: #111827;
        box-shadow: 0 0 0 1px rgba(255,255,255,.04);
    }
    .mobile-donut-legend {
        min-width: 0;
        display: grid;
        gap: 5px;
    }
    .mobile-donut-legend-row {
        display: grid;
        grid-template-columns: 8px minmax(0, 1fr);
        gap: 5px;
        align-items: center;
        min-width: 0;
    }
    .mobile-donut-dot {
        width: 7px;
        height: 7px;
        border-radius: 999px;
    }
    .mobile-donut-label {
        min-width: 0;
        color: rgba(255,255,255,.66);
        font-size: 10px;
        line-height: 1.15;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    </style>
    """, unsafe_allow_html=True)
    _mobile_cards = [
        ("Panoramica", "panoramica", "Stipendi", "Budget e impostazioni"),
        ("Spese", "spese", "Spese", "Fisse e dettaglio"),
        ("Variabili", "variabili", "Variabili", "Quote e donut"),
        ("Entrate", "entrate", "Altre entrate", "Altre entrate e obiettivi"),
        ("Risparmi", "risparmi", "Risparmi", "Riepilogo mese"),
        ("Carte", "carte", "Carte", "Trasferimenti"),
        ("Note", "promemoria", "Note", "Note mensili"),
        ("Turni", "turni", "Turni", "Live e calendario"),
        ("Storico", "storico", "Storico stipendi", "Stipendi e risparmi"),
        ("Bollette", "bollette", "Storico bollette", "Storico e saldo"),
    ]
    st.markdown(f"""
    <div id="mobile-top" class="mobile-anchor"></div>
    <div class="mobile-home-title">Calcolatore di Spese Personali</div>
    """, unsafe_allow_html=True)
    mobile_section_labels = {
        "Panoramica": "Stipendi",
        "Spese": "Spese fisse",
        "Variabili": "Spese variabili",
        "Entrate": "Altre entrate",
        "Storico": "Storico stipendi",
        "Bollette": "Storico bollette",
    }
    mobile_section = st.radio(
        "Sezione telefono",
        MOBILE_SECTIONS,
        key="mobile_section_select",
        horizontal=True,
        label_visibility="collapsed",
        format_func=lambda section: mobile_section_labels.get(section, section)
    )

def _mobile_show(*sections):
    return (not MOBILE_VIEW) or (mobile_section in sections)


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
    "dashboard_principale": [1, 2.70, 1.78],  # Spese fisse | Variabili/Entrate | Risparmi/Carte/Turni
    "turni_calendario_riepilogo": [1.55, 0.55],
    "turni_frecce_titolo": [0.16, 0.68, 0.16],
    "centrale_variabili_altre": [1.05, 0.95],
    "spese_fisse_lista": [1, 1.1],
    "variabili_quote_budget": [1, 1],
    "variabili_kpi_grafico": [1.15, 2.05],
    "altre_entrate_obiettivo": [1.06, 1.04],
    "altre_entrate_kpi_grafico": [1.10, 1.90],
    "destra_risparmi_carte": [1.60, 1.00],
    "risparmi_kpi_grafico": [1.18, 1.12],
    "dettaglio_spese_fisse": [0.07, 0.42, 0.62, 0.90],
    "storico_form_chart": [1, 1, 2],
    "storico_tabella_grafico": [1.1, 3],
    "storico_kpi": [1.3, 1, 1],
    "bollette_form_chart": [1, 1, 2],
    "bollette_tabella_grafico": [1, 3.3],
    "form_nome_importo": [1.4, 0.8],
    "bottone_salva_note": [3, 1],
}

triangolino_verde_BNL = '<span style="display:inline-block; width:0; height:0; border-top:5px solid transparent; border-bottom:5px solid transparent; border-right:5px solid green; margin-left:10px;"></span>'
triangolino_arancione_ING = '<span style="display:inline-block; width:0; height:0; border-top:5px solid transparent; border-bottom:5px solid transparent; border-right:5px solid #D2691E; margin-left:10px;"></span>'
triangolino_blu_Revolut = '<span style="display:inline-block; width:0; height:0; border-top:5px solid transparent; border-bottom:5px solid transparent; border-right:5px solid #89CFF0; margin-left:10px;"></span>'
# /////  

def _mobile_donut_html(title, labels, values, colors):
    clean_items = [
        (str(label), float(value), str(color))
        for label, value, color in zip(labels, values, colors)
        if float(value or 0) > 0
    ]
    total = sum(value for _, value, _ in clean_items)
    if total <= 0:
        return ""

    start = 0.0
    stops = []
    legend_rows = []
    for label, value, color in clean_items:
        end = start + (value / total * 360)
        stops.append(f"{color} {start:.2f}deg {end:.2f}deg")
        start = end
        legend_rows.append(
            f'<div class="mobile-donut-legend-row">'
            f'<span class="mobile-donut-dot" style="background:{color};"></span>'
            f'<span class="mobile-donut-label">{html.escape(label)}</span>'
            f'</div>'
        )

    gradient = ", ".join(stops)
    return (
        '<div class="mobile-donut-card">'
        f'<div class="mobile-donut-title">{html.escape(title)}</div>'
        '<div class="mobile-donut-body">'
        f'<div class="mobile-donut-ring" style="background:conic-gradient({gradient});">'
        '<div class="mobile-donut-hole"></div>'
        '</div>'
        f'<div class="mobile-donut-legend">{"".join(legend_rows)}</div>'
        '</div>'
        '</div>'
    )

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
        "Amara": 135,
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
    "Revolut": ["Trasporti", "Sport", "Bollette", "Pulizia Casa", "Psicologo", "Amara", "Beneficienza", "Netflix", "Spotify", "Disney+", "Emergenze/Compleanni", "Viaggi", "Da spendere", "Spese quotidiane"],
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
    ("Vita e cura", ["World Food Programme", "Beneficienza", "Trasporti", "Sport", "Amara"]),
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
    if voce in ["Sport", "Psicologo", "Amara"]:
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


def _mobile_history_table_html(df, columns, colors):
    if df.empty:
        return '<div class="kpi-card" style="color:rgba(255,255,255,.62);">Nessun dato storico disponibile.</div>'

    cards = []
    for _, row in df.sort_values("Mese", ascending=False).iterrows():
        mese_raw = pd.to_datetime(row.get("Mese"), errors="coerce")
        mese = mese_raw.strftime("%B %Y") if not pd.isna(mese_raw) else str(row.get("Mese", ""))
        values_html = ""
        for col in columns:
            value = pd.to_numeric(row.get(col, 0), errors="coerce")
            value = 0.0 if pd.isna(value) else float(value)
            color = colors.get(col, "#9ca3af")
            values_html += (
                '<div style="display:flex;align-items:center;justify-content:space-between;gap:6px;'
                'padding:4px 0;border-top:1px solid rgba(255,255,255,.055);">'
                f'<span style="display:flex;align-items:center;gap:5px;color:rgba(255,255,255,.64);font-size:11px;">'
                f'<span style="width:6px;height:6px;border-radius:999px;background:{color};display:inline-block;"></span>'
                f'{html.escape(col)}</span>'
                f'<span style="font-family:DM Mono, monospace;color:{color};font-weight:800;font-size:12px;">€{value:,.2f}</span>'
                '</div>'
            )
        cards.append(
            '<div style="padding:9px 10px;border-radius:10px;'
            'background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.075);min-width:0;">'
            f'<div style="font-weight:900;color:rgba(255,255,255,.88);margin-bottom:5px;font-size:13px;">{html.escape(mese)}</div>'
            f'{values_html}</div>'
        )

    return (
        '<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));'
        'gap:8px;max-height:410px;overflow-y:auto;padding-right:3px;'
        'scrollbar-color:rgba(148,163,184,.55) transparent;">'
        + "".join(cards) +
        '</div>'
    )


def _render_stipendi_kpi_cards(data_stipendi):
    data_stipendi = calcola_medie(data_stipendi, ["Stipendio", "Risparmi", "Messi da parte Totali"])
    stats_stip = calcola_statistiche(data_stipendi, ["Stipendio", "Risparmi", "Messi da parte Totali"])
    st.markdown(
        '<div style="height:18px;margin:12px 0 16px;border-top:1px solid rgba(255,255,255,.08);"></div>',
        unsafe_allow_html=True
    )

    _s1 = f"{stats_stip['Stipendio']['somma']:,.2f} €"
    _s2 = f"{stats_stip['Stipendio']['media']:,.2f} €"
    _s3 = (
        f"{data_stipendi['Media Stipendio NO 13°/PDR'].iloc[-1]:,.2f} €"
        if "Media Stipendio NO 13°/PDR" in data_stipendi.columns and not data_stipendi.empty
        else "0.00 €"
    )
    _r1 = f"{stats_stip['Risparmi']['somma']:,.2f} €"
    _r2 = f"{stats_stip['Risparmi']['media']:,.2f} €"
    _m1 = f"{stats_stip['Messi da parte Totali']['somma']:,.2f} €"
    _m2 = f"{stats_stip['Messi da parte Totali']['media']:,.2f} €"

    if MOBILE_VIEW:
        cards = [
            ("Somma Stipendi", _s1, "#5792E8"),
            ("Media Stipendi", _s2, "#f87171"),
            ("Media Stipendi Ordinari (no spikes)", _s3, "#fb923c"),
            ("Somma Risparmi Mese Precedente", _r1, "#EF9F27"),
            ("Media Risparmi Mese Precedente", _r2, "#FFA040"),
            ("Somma Messi da Parte", _m1, "#1D9E75"),
            ("Media Messi da Parte", _m2, "#90EE90"),
        ]
        html_cards = "".join(
            '<div class="kpi-card" style="min-width:0;padding:12px 12px;">'
            f'<div class="kpi-label" style="font-size:10px;line-height:1.15;">{html.escape(label)}</div>'
            f'<div class="kpi-value" style="color:{color};font-size:18px;line-height:1.15;">{html.escape(value)}</div>'
            '</div>'
            for label, value, color in cards
        )
        st.markdown(
            '<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));'
            'gap:8px;align-items:stretch;">'
            f'{html_cards}'
            '</div>',
            unsafe_allow_html=True
        )
        return

    col_somme1, col_somme2, col_somme3 = st.columns(LAYOUT_COLONNE["storico_kpi"])
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
            st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">Media Stipendi Ordinari (no spikes)</div>
            <div class="kpi-value" style="color:#fb923c;font-size:16px;">{_s3}</div>
        </div>""", unsafe_allow_html=True)
    with col_somme2:
        st.markdown(f"""
        <div class="kpi-card" style="margin-bottom:8px;">
            <div class="kpi-label">Somma Risparmi Mese Precedente</div>
            <div class="kpi-value" style="color:#EF9F27;font-size:16px;">{_r1}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Media Risparmi Mese Precedente</div>
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
    df["Voce"] = df["Voce"].astype(str).replace({"Altro/C": "Amara", "Cane": "Amara"})
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
        "Amara": "#40E0D0",
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
    filter: saturate(1.35) brightness(1.15);
    text-shadow:
        0 0 2px rgba(255,255,255,0.18),
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
TURNI_HEADERS = ["Data", "Turno", "Festivo", "Straordinario minuti", "Sede"]
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
    "Ferie": ("09:00", "17:00"),
    "Riposo": ("00:00", "00:00"),
}

DEFAULT_TURNI_RULES = {
    "paga_oraria": 12.60,
    "quota_fissa_mensile": 0.0,
    "m_p_feriale_pct": 20.0,
    "m_p_festivo_giorno_pct": 50.0,
    "notte_feriale_pct": 50.0,
    "festivo_sera_notte_pct": 60.0,
    "straordinario_feriale_pct": 25.0,
    "straordinario_festivo_pct": 50.0,
    "stra_mattina_feriale_pct": 25.0,
    "stra_mattina_festivo_pct": 55.0,
    "stra_pomeriggio_feriale_pct": 40.0,
    "stra_pomeriggio_festivo_pct": 60.0,
    "stra_notte_feriale_pct": 50.0,
    "stra_notte_festivo_pct": 70.0,
    "stra_ferie_feriale_pct": 25.0,
    "stra_ferie_festivo_pct": 50.0,
    "buono_pasto": 7.0,
    "smart_target": 15.0,
    "accrediti_mensili": 43.87,
    "trattenute_mensili": 218.73,
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


def _parse_float_turni(value):
    try:
        if pd.isna(value):
            return 0.0
    except Exception:
        pass
    if isinstance(value, str):
        value = value.strip().replace("€", "").replace(".", "").replace(",", ".")
    try:
        return float(value)
    except Exception:
        return 0.0


def _normalize_turni_df(df):
    if df.empty:
        return pd.DataFrame(columns=TURNI_HEADERS)
    old_stra_cols = [col for col in ["Straordinario feriale", "Straordinario festivo"] if col in df.columns]
    for col in TURNI_HEADERS:
        if col not in df.columns:
            df[col] = ""
    if "Straordinario minuti" in df.columns:
        df["Straordinario minuti"] = df["Straordinario minuti"].apply(_parse_float_turni)
    if old_stra_cols:
        old_minutes = sum(df[col].apply(_parse_float_turni) for col in old_stra_cols) * 60
        df["Straordinario minuti"] = df["Straordinario minuti"].where(df["Straordinario minuti"] > 0, old_minutes)
    df = df[TURNI_HEADERS].copy()
    df["Data"] = pd.to_datetime(df["Data"], errors="coerce")
    df = df.dropna(subset=["Data"])
    df["Data"] = df["Data"].dt.strftime("%Y-%m-%d")
    df["Turno"] = df["Turno"].astype(str)
    df["Festivo"] = df["Festivo"].apply(_parse_bool_turni)
    df["Straordinario minuti"] = df["Straordinario minuti"].apply(lambda value: int(round(max(0, _parse_float_turni(value)))))
    df["Sede"] = df["Sede"].apply(_parse_bool_turni)
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
            "range": "A1:E1",
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
                "range": f"A{i+2}:E{i+2}",
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
    else:
        for key, value in DEFAULT_TURNI_RULES.items():
            st.session_state.turni_rules.setdefault(key, value)
    return st.session_state.turni_rules


def _apply_turni_rules_from_widgets(rules):
    widget_to_rule = {
        "turni_paga": "paga_oraria",
        "turni_mp_feriale": "m_p_feriale_pct",
        "turni_mp_festivo": "m_p_festivo_giorno_pct",
        "turni_notte_feriale": "notte_feriale_pct",
        "turni_festivo_notte": "festivo_sera_notte_pct",
        "turni_stra_feriale": "straordinario_feriale_pct",
        "turni_stra_festivo": "straordinario_festivo_pct",
        "turni_stra_m_feriale": "stra_mattina_feriale_pct",
        "turni_stra_m_festivo": "stra_mattina_festivo_pct",
        "turni_stra_p_feriale": "stra_pomeriggio_feriale_pct",
        "turni_stra_p_festivo": "stra_pomeriggio_festivo_pct",
        "turni_stra_n_feriale": "stra_notte_feriale_pct",
        "turni_stra_n_festivo": "stra_notte_festivo_pct",
        "turni_stra_f_feriale": "stra_ferie_feriale_pct",
        "turni_stra_f_festivo": "stra_ferie_festivo_pct",
        "turni_buono_pasto": "buono_pasto",
        "turni_smart_target": "smart_target",
        "turni_accrediti_mensili": "accrediti_mensili",
        "turni_trattenute_mensili": "trattenute_mensili",
        "turni_ind_mp_f": "ind_m_p_feriale",
        "turni_ind_n_f": "ind_notte_feriale",
        "turni_ind_mp_fe": "ind_m_p_festivo",
        "turni_ind_n_fe": "ind_notte_festiva",
    }
    for widget_key, rule_key in widget_to_rule.items():
        if widget_key in st.session_state:
            rules[rule_key] = float(st.session_state[widget_key])
    st.session_state.turni_rules = rules
    return rules


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
    if not festive_at_start and start.weekday() == 5:
        return 0.0
    if turno == "Notte":
        return rules["ind_notte_festiva"] if festive_at_start else rules["ind_notte_feriale"]
    return rules["ind_m_p_festivo"] if festive_at_start else rules["ind_m_p_feriale"]


def _turni_row_straordinario_minuti(row):
    return int(round(max(0, _parse_float_turni(row.get("Straordinario minuti", 0)))))


def _turni_row_sede(row):
    return _parse_bool_turni(row.get("Sede", False))


def _upsert_turni_day(df_turni, day_str, turno=None, festivo=None, straordinario_minuti=None, sede=None):
    row = df_turni[df_turni["Data"] == day_str]
    current = {
        "Data": day_str,
        "Turno": "" if row.empty else str(row.iloc[0].get("Turno", "")),
        "Festivo": False if row.empty else _parse_bool_turni(row.iloc[0].get("Festivo", False)),
        "Straordinario minuti": 0 if row.empty else _turni_row_straordinario_minuti(row.iloc[0]),
        "Sede": False if row.empty else _turni_row_sede(row.iloc[0]),
    }
    if turno is not None:
        current["Turno"] = turno if turno in TURNI_ORARI else ""
    if festivo is not None:
        current["Festivo"] = bool(festivo)
    if straordinario_minuti is not None:
        current["Straordinario minuti"] = int(round(max(0, _parse_float_turni(straordinario_minuti))))
    if sede is not None:
        current["Sede"] = bool(sede)
    df_new = df_turni[df_turni["Data"] != day_str].copy()
    return _normalize_turni_df(pd.concat([df_new, pd.DataFrame([current])], ignore_index=True))


def _save_turni_and_rerun(df_new, error_message="Aggiornato in bozza, ma non salvato su Google Sheets."):
    df_new = _normalize_turni_df(df_new)
    st.session_state.turni_df_draft = df_new.copy()
    if save_turni_data(df_new):
        st.session_state.turni_dirty = False
        st.rerun()
    st.session_state.turni_dirty = True
    st.error(error_message)
    return df_new


def _format_minutes_label(minutes):
    minutes = int(round(max(0, _parse_float_turni(minutes))))
    if minutes <= 0:
        return "0m"
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}h {mins:02d}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def _pct_for_straordinario(turno, dt_obj, forced_festivo, rules):
    festive = _is_festive_at(dt_obj, forced_festivo)
    fallback_key = "straordinario_festivo_pct" if festive else "straordinario_feriale_pct"
    fallback = float(rules.get(fallback_key, 50.0 if festive else 25.0))
    if turno == "Pomeriggio":
        key = "stra_pomeriggio_festivo_pct" if festive else "stra_pomeriggio_feriale_pct"
    elif turno == "Notte":
        key = "stra_notte_festivo_pct" if festive else "stra_notte_feriale_pct"
    elif turno == "Ferie":
        key = "stra_ferie_festivo_pct" if festive else "stra_ferie_feriale_pct"
    else:
        key = "stra_mattina_festivo_pct" if festive else "stra_mattina_feriale_pct"
    return float(rules.get(key, fallback))


def _calc_straordinario_minuti(data_str, turno, forced_festivo, rules, until=None, only_day=None, straordinario_minuti=0):
    minuti = int(round(max(0, _parse_float_turni(straordinario_minuti))))
    if minuti <= 0 or turno in ["", "Riposo"]:
        return {"total": 0.0, "base": 0.0, "extra": 0.0, "hours": 0.0, "hours_by_pct": {}}
    now = _now_italy() if until is None else until
    _, shift_end = _shift_bounds(data_str, turno)
    overtime_start = shift_end
    overtime_end = overtime_start + timedelta(minutes=min(minuti, 120))
    effective_end = overtime_end if now.year >= 9999 else min(overtime_end, now)

    start = overtime_start
    if only_day is not None:
        day_start = _dt_for_turno(only_day, "00:00")
        day_end = day_start + timedelta(days=1)
        start = max(start, day_start)
        effective_end = min(effective_end, day_end)
    if effective_end <= start:
        return {"total": 0.0, "base": 0.0, "extra": 0.0, "hours": 0.0, "hours_by_pct": {}}

    paga = float(rules["paga_oraria"])
    base = 0.0
    extra = 0.0
    hours = 0.0
    hours_by_pct = {}
    t = start
    while t < effective_end:
        nxt = min(t + timedelta(minutes=1), effective_end)
        h = (nxt - t).total_seconds() / 3600
        pct = _pct_for_straordinario(turno, t, forced_festivo, rules)
        base += paga * h
        extra += paga * pct / 100 * h
        hours += h
        hours_by_pct[pct] = hours_by_pct.get(pct, 0.0) + h
        t = nxt
    return {"total": base + extra, "base": base, "extra": extra, "hours": hours, "hours_by_pct": hours_by_pct}


def _is_sede_buono_pasto(data_str, turno, forced_festivo, sede):
    if not sede or turno in ["", "Ferie", "Riposo"]:
        return False
    start, _ = _shift_bounds(data_str, turno)
    return turno != "Mattina" or _is_festive_at(start, forced_festivo)


def _calc_turno_hours_by_pct(data_str, turno, forced_festivo, rules):
    if turno in ["", "Ferie", "Riposo"]:
        return {}
    start, end = _shift_bounds(data_str, turno)
    hours_by_pct = {}
    t = start
    while t < end:
        nxt = min(t + timedelta(minutes=1), end)
        h = (nxt - t).total_seconds() / 3600
        pct = _pct_for_turno(turno, t, forced_festivo, rules)
        hours_by_pct[pct] = hours_by_pct.get(pct, 0.0) + h
        t = nxt
    return hours_by_pct


def compute_turni_month_report(df_turni, rules, month_key):
    month_df = df_turni[df_turni["Data"].str.startswith(month_key)].copy()
    month_df = month_df[month_df["Turno"].isin(TURNI_ORARI.keys()) & (month_df["Turno"] != "")]
    report = {
        "work_days": 0,
        "ferie_days": 0,
        "turn_counts": {"Mattina": 0, "Pomeriggio": 0, "Notte": 0, "Ferie": 0},
        "turn_type_counts": {},
        "sede_days": 0,
        "sede_required": 0,
        "sede_remaining": 0,
        "buoni_pasto_days": 0,
        "buoni_pasto_total": 0.0,
        "straordinario_minutes": 0,
        "straordinario_total": 0.0,
        "hours_by_pct": {},
    }
    for _, row in month_df.iterrows():
        data = row["Data"]
        turno = row["Turno"]
        festivo = bool(row["Festivo"])
        sede = _turni_row_sede(row)
        stra_minuti = _turni_row_straordinario_minuti(row)
        if turno == "Ferie":
            report["ferie_days"] += 1
            report["turn_counts"]["Ferie"] += 1
        elif turno != "Riposo":
            report["work_days"] += 1
            report["turn_counts"][turno] = report["turn_counts"].get(turno, 0) + 1
            start, _ = _shift_bounds(data, turno)
            suffix = "festivo" if _is_festive_at(start, festivo) else "feriale"
            key = f"{turno} {suffix}"
            report["turn_type_counts"][key] = report["turn_type_counts"].get(key, 0) + 1
            for pct, hours in _calc_turno_hours_by_pct(data, turno, festivo, rules).items():
                report["hours_by_pct"][pct] = report["hours_by_pct"].get(pct, 0.0) + hours
        if sede:
            report["sede_days"] += 1
        if _is_sede_buono_pasto(data, turno, festivo, sede):
            report["buoni_pasto_days"] += 1
        if stra_minuti:
            report["straordinario_minutes"] += stra_minuti
            stra_calc = _calc_straordinario_minuti(
                data,
                turno,
                festivo,
                rules,
                until=datetime.max.replace(tzinfo=None),
                straordinario_minuti=stra_minuti,
            )
            report["straordinario_total"] += stra_calc["total"]
            for pct, hours in stra_calc.get("hours_by_pct", {}).items():
                report["hours_by_pct"][pct] = report["hours_by_pct"].get(pct, 0.0) + hours
    smart_target = int(round(max(0, _parse_float_turni(rules.get("smart_target", 15)))))
    report["sede_required"] = max(0, report["work_days"] - smart_target)
    report["sede_remaining"] = max(0, report["sede_required"] - report["sede_days"])
    report["buoni_pasto_total"] = report["buoni_pasto_days"] * float(rules.get("buono_pasto", 7.0))
    return report


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


def compute_turno(data_str, turno, forced_festivo, rules, until=None, only_day=None, straordinario_minuti=0):
    now = _now_italy() if until is None else until
    paga = float(rules["paga_oraria"])
    stra_calc = _calc_straordinario_minuti(
        data_str,
        turno,
        forced_festivo,
        rules,
        until=until,
        only_day=only_day,
        straordinario_minuti=straordinario_minuti,
    )

    if turno == "Riposo":
        return {**stra_calc, "rate_min": 0.0}

    if turno == "Ferie":
        start, end = _shift_bounds(data_str, turno)
        if only_day is not None and data_str != only_day:
            return {"total": stra_calc["total"], "base": stra_calc["base"], "extra": stra_calc["extra"], "hours": stra_calc["hours"], "rate_min": 0.0}
        effective_end = min(end, now)
        if effective_end <= start:
            hours = 0.0
        else:
            hours = min(8.0, (effective_end - start).total_seconds() / 3600)
        base = paga * hours
        rate_min = paga / 60 if start <= _now_italy() < end else 0.0
        return {
            "total": base + stra_calc["total"],
            "base": base + stra_calc["base"],
            "extra": stra_calc["extra"],
            "hours": hours + stra_calc["hours"],
            "rate_min": rate_min,
        }

    start, end = _shift_bounds(data_str, turno)
    effective_end = min(end, now)

    if only_day is not None:
        day_start = _dt_for_turno(only_day, "00:00")
        day_end = day_start + timedelta(days=1)
        start = max(start, day_start)
        effective_end = min(effective_end, day_end)

    if effective_end <= start:
        return {"total": stra_calc["total"], "base": stra_calc["base"], "extra": stra_calc["extra"], "hours": stra_calc["hours"], "rate_min": 0.0}

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
    base += stra_calc["base"]
    extra += stra_calc["extra"]
    hours += stra_calc["hours"]

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
    current_shift_type = "—"
    current_turno = ""
    current_shift_date = ""
    current_shift_start_date = ""
    current_shift_end = None
    current_rate_change_at = None
    is_on_leave = False
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
        stra_minuti = _turni_row_straordinario_minuti(row)
        has_turno = turno in TURNI_ORARI and turno != ""

        if has_turno and turno == "Ferie" and data[:7] == current_m:
            ferie_days_total += 1

        if has_turno and turno not in ["Ferie", "Riposo"] and data[:7] == current_m:
            work_days_total += 1
            start_day, _ = _shift_bounds(data, turno)
            if start_day <= now:
                work_days_done += 1

        if has_turno and data[:7] == current_m:
            calc_live = compute_turno(data, turno, festivo, rules, until=now, straordinario_minuti=stra_minuti)
            live_month += calc_live["total"]
            hours_live += calc_live["hours"]
            calc_full = compute_turno(data, turno, festivo, rules, until=datetime.max.replace(tzinfo=None), straordinario_minuti=stra_minuti)
            current_base_full += calc_full["base"]
            start, end = _shift_bounds(data, turno)
            if turno == "Ferie" and start.strftime("%Y-%m-%d") == today:
                is_on_leave = True
                rate_min = calc_live["rate_min"]
                current_shift = f"Ferie {start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
                current_shift_type = "Ferie · base 8h"
                turno_kpi_label = "Ferie — live / totale giornata"
                current_turno = "Ferie"
                current_shift_date = _turni_short_date_label(start)
                current_shift_start_date = data
                if now < end:
                    current_shift_end = end
                    current_rate_change_at = start if now < start else None
                live_today = calc_live["total"]
                expected_today = compute_turno(data, turno, festivo, rules, until=datetime.max.replace(tzinfo=None), straordinario_minuti=stra_minuti)["total"]
            elif turno not in ["Ferie", "Riposo"] and start <= now < end:
                rate_min = calc_live["rate_min"]
                current_shift = f"{turno} {start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
                current_shift_type = f"{turno} {'festivo' if _is_festive_at(now, festivo) else 'feriale'}"
                current_turno = turno
                current_shift_date = _turni_short_date_label(start)
                current_shift_start_date = data
                current_shift_end = end
                current_rate_change_at = _next_rate_checkpoint(now, end)
                live_today = calc_live["total"]
                expected_today = compute_turno(data, turno, festivo, rules, until=datetime.max.replace(tzinfo=None), straordinario_minuti=stra_minuti)["total"]

        if has_turno and data[:7] == prev_m:
            calc_prev = compute_turno(data, turno, festivo, rules, until=datetime.max.replace(tzinfo=None), straordinario_minuti=stra_minuti)
            prev_extras += calc_prev["extra"]

        if not has_turno:
            continue
        start, end = _shift_bounds(data, turno)
        if turno not in ["Ferie", "Riposo"] and start > now and (next_shift_start is None or start < next_shift_start):
            next_shift_start = start
            next_shift_label = f"{turno} {start.strftime('%d/%m %H:%M')}"
            next_shift_total = compute_turno(data, turno, festivo, rules, until=datetime.max.replace(tzinfo=None), straordinario_minuti=stra_minuti)["total"]
        if turno not in ["Ferie", "Riposo"] and end <= now and (last_shift_end is None or end > last_shift_end):
            last_shift_end = end
            last_shift_label = f"{turno} {start.strftime('%d/%m %H:%M')}"
            last_shift_total = compute_turno(data, turno, festivo, rules, until=datetime.max.replace(tzinfo=None), straordinario_minuti=stra_minuti)["total"]
        if turno != "Ferie" and current_shift_end is None and start.strftime("%Y-%m-%d") <= today <= end.strftime("%Y-%m-%d"):
            live_today += compute_turno(data, turno, festivo, rules, until=now, only_day=today, straordinario_minuti=stra_minuti)["total"]
            expected_today += compute_turno(data, turno, festivo, rules, until=datetime.max.replace(tzinfo=None), only_day=today, straordinario_minuti=stra_minuti)["total"]

    if current_shift_end is None and not is_on_leave:
        live_today = last_shift_total
        expected_today = next_shift_total
        turno_kpi_label = "Ultimo / prossimo turno"

    month_report = compute_turni_month_report(df_turni, rules, current_m)
    buoni_pasto_total = float(month_report.get("buoni_pasto_total", 0.0))
    monthly_adjustments = (
        float(rules["quota_fissa_mensile"])
        + float(rules.get("accrediti_mensili", 0.0))
        - float(rules.get("trattenute_mensili", 0.0))
        + buoni_pasto_total
    )
    live_month += monthly_adjustments
    payslip_estimate = monthly_adjustments + current_base_full + prev_extras

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
        "current_shift_type": current_shift_type,
        "current_turno": current_turno,
        "current_shift_date": current_shift_date,
        "current_shift_start_date": current_shift_start_date,
        "turno_kpi_label": turno_kpi_label,
        "last_shift_label": last_shift_label,
        "is_on_shift": bool(current_shift_end and not is_on_leave),
        "is_on_leave": bool(is_on_leave),
        "current_shift_end": current_shift_end.isoformat() if current_shift_end else "",
        "current_rate_change_at": current_rate_change_at.isoformat() if current_rate_change_at else "",
        "next_shift_start": next_shift_start.isoformat() if next_shift_start else "",
        "next_shift_label": next_shift_label,
        "work_days_done": work_days_done,
        "work_days_total": work_days_total,
        "ferie_days_total": ferie_days_total,
        "monthly_adjustments": monthly_adjustments,
        "buoni_pasto_total": buoni_pasto_total,
        "sede_days_total": int(month_report.get("sede_days", 0)),
        "sede_days_required": int(month_report.get("sede_required", 0)),
        "sede_days_remaining": int(month_report.get("sede_remaining", 0)),
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


def _storico_stipendio_for_month(month_key):
    headers = ["Mese", "Stipendio", "Risparmi", "Messi da parte Totali"]
    try:
        data = load_data_gsheets("Stipendi", headers)
    except Exception:
        return None
    if data is None or data.empty or "Mese" not in data.columns or "Stipendio" not in data.columns:
        return None
    data = data.copy()
    data["Mese"] = pd.to_datetime(data["Mese"], errors="coerce")
    data = data.dropna(subset=["Mese"])
    data["month_key"] = data["Mese"].dt.to_period("M").astype(str)
    match = data[data["month_key"] == month_key]
    if match.empty:
        return None
    return _parse_float_turni(match.iloc[-1].get("Stipendio", 0.0))


def _turni_month_money_summary(df_turni, rules, month_key):
    report = compute_turni_month_report(df_turni, rules, month_key)
    month_df = df_turni[df_turni["Data"].str.startswith(month_key)].copy()
    month_df = month_df[month_df["Turno"].isin(TURNI_ORARI.keys()) & (month_df["Turno"] != "")]
    turni_total = 0.0
    turni_base = 0.0
    turni_extra = 0.0
    for _, row in month_df.iterrows():
        calc = compute_turno(
            row["Data"],
            row["Turno"],
            bool(row["Festivo"]),
            rules,
            until=datetime.max.replace(tzinfo=None),
            straordinario_minuti=_turni_row_straordinario_minuti(row),
        )
        turni_total += float(calc.get("total", 0.0))
        turni_base += float(calc.get("base", 0.0))
        turni_extra += float(calc.get("extra", 0.0))
    monthly_adjustments = (
        float(rules.get("quota_fissa_mensile", 0.0))
        + float(rules.get("accrediti_mensili", 0.0))
        - float(rules.get("trattenute_mensili", 0.0))
        + float(report.get("buoni_pasto_total", 0.0))
    )
    return {
        **report,
        "turni_total": turni_total,
        "turni_base": turni_base,
        "turni_extra": turni_extra,
        "monthly_adjustments": monthly_adjustments,
        "month_total": turni_total + monthly_adjustments,
    }


def render_selected_month_turni_kpis(df_turni, rules, month_key, side_html=""):
    month_date = datetime.strptime(f"{month_key}-01", "%Y-%m-%d").date()
    month_label = html.escape(_turni_month_label(month_date))
    summary = _turni_month_money_summary(df_turni, rules, month_key)
    storico_stipendio = _storico_stipendio_for_month(month_key)
    actual_value = summary["month_total"] if storico_stipendio is None else storico_stipendio
    actual_subline = (
        "Guadagno effettivo da storico stipendi"
        if storico_stipendio is not None
        else "Storico assente: uso il calcolo dei turni"
    )
    work_days = int(summary.get("work_days", 0))
    ferie_days = int(summary.get("ferie_days", 0))
    total_days = work_days + ferie_days
    sede_days = int(summary.get("sede_days", 0))
    sede_required = int(summary.get("sede_required", 0))
    buoni = float(summary.get("buoni_pasto_total", 0.0))
    side_block = f'<div class="turni-live-side">{side_html}</div>' if side_html else ""
    shell_class = "turni-static-shell has-side" if side_html else "turni-static-shell"
    component_height = 286 if (MOBILE_VIEW and side_html) else (330 if MOBILE_VIEW else 126)
    components.html(f"""
    <div class="{shell_class}">
      <div class="turni-live-grid">
        <div class="kpi-card" style="border-color:rgba(52,211,153,0.25);">
          <div class="kpi-label">{month_label} — storico stipendi</div>
          <div class="kpi-value" style="color:#34d399;">{_money_turni(actual_value)}</div>
          <div class="turni-subline">{html.escape(actual_subline)}</div>
        </div>
        <div class="kpi-card" style="border-color:rgba(96,165,250,0.25);">
          <div class="kpi-label">Giorni lavorati / ferie</div>
          <div class="kpi-value" style="color:#60a5fa;">{work_days} / {total_days}</div>
          <div class="turni-subline">{work_days} lavorati + {ferie_days} ferie = {total_days}</div>
        </div>
        <div class="kpi-card" style="border-color:rgba(254,243,199,0.25);">
          <div class="kpi-label">Turni calcolati</div>
          <div class="kpi-value" style="color:#fef3c7;">{_money_turni(summary["month_total"])}</div>
          <div class="turni-subline">Sedi {sede_days}/{sede_required} · buoni {_money_turni(buoni)}</div>
        </div>
      </div>
      {side_block}
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
      .turni-static-shell.has-side {{
        display: grid;
        grid-template-columns: minmax(0, 1.02fr) minmax(0, .98fr);
        gap: 10px;
        align-items: start;
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
      .turni-subline {{
        font-size: 12px;
        color: rgba(255,255,255,0.42);
        margin-top: 5px;
      }}
      .turni-live-side {{
        min-width: 0;
      }}
      .turni-summary-compact-title {{
        color: rgba(255,255,255,.88);
        font-size: 13px;
        font-weight: 800;
        margin: 0 0 4px;
      }}
      .turni-grid-scroll {{
        max-height: 236px;
        overflow-y: auto;
        padding-right: 4px;
      }}
      .turni-card-small {{
        background: rgba(255,255,255,0.045);
        border: 0.5px solid rgba(255,255,255,0.10);
        border-left: 4px solid rgba(255,255,255,0.25);
        border-radius: 10px;
        padding: 6px 7px;
        margin-bottom: 5px;
      }}
      #turni-focus-card {{
        border-color: rgba(147,197,253,.55);
        box-shadow: 0 0 0 1px rgba(147,197,253,.26), 0 0 13px rgba(96,165,250,.18);
        background: rgba(96,165,250,.08);
      }}
      .turni-card-small .date {{
        font-size: 10px;
        color: rgba(255,255,255,0.58);
      }}
      .turni-card-small .title {{
        font-size: 12px;
        font-weight: 700;
        margin-top: 1px;
      }}
      .turni-card-small .meta {{
        font-size: 9px;
        color: rgba(255,255,255,0.42);
        margin-top: 2px;
      }}
      .turni-mattina {{ border-left-color:#60a5fa; }}
      .turni-pomeriggio {{ border-left-color:#fb923c; }}
      .turni-notte {{ border-left-color:#64748b; }}
      .turni-ferie {{ border-left-color:#34d399; }}
      @media (max-width: 760px) {{
        .turni-static-shell.has-side {{
          grid-template-columns: minmax(0, .94fr) minmax(0, 1.06fr);
          gap: 7px;
        }}
        .turni-live-grid {{
          grid-template-columns: 1fr;
          gap: 6px;
        }}
        .kpi-card {{
          padding: 8px 9px;
          min-height: auto;
        }}
        .kpi-label {{
          font-size: 8px;
          letter-spacing: .55px;
          margin-bottom: 4px;
        }}
        .kpi-value {{
          font-size: 14px;
        }}
        .turni-subline {{
          font-size: 9px;
          margin-top: 3px;
        }}
        .turni-grid-scroll {{
          max-height: 199px;
          padding-top: 2px;
        }}
        .turni-summary-compact-title {{
          font-size: 11px;
          margin: 0 0 7px;
        }}
      }}
    </style>
    """, height=component_height, scrolling=False)


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
            "Straordinario minuti": 0,
            "Sede": False,
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
    existing_month = df_turni[df_turni["Data"].str.startswith(month_key)].copy()
    if not existing_month.empty:
        existing_extra = existing_month.set_index("Data")[["Straordinario minuti", "Sede"]].to_dict("index")
        for idx, row in imported.iterrows():
            extra = existing_extra.get(row["Data"])
            if extra:
                imported.at[idx, "Straordinario minuti"] = extra.get("Straordinario minuti", 0)
                imported.at[idx, "Sede"] = extra.get("Sede", False)
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


def render_live_turni_kpis(stats, side_html=""):
    live_month = float(stats["live_month"])
    live_today = float(stats["live_today"])
    rate_min = float(stats["rate_min"])
    rate_hour = rate_min * 60
    rate_sec = rate_min / 60
    payslip_estimate = _money_turni(stats["payslip_estimate"])
    expected_today = _money_turni(stats["expected_today"])
    current_shift = str(stats["current_shift"]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    current_shift_type = str(stats.get("current_shift_type", "—")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    current_turno = str(stats.get("current_turno", "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    current_shift_date = str(stats.get("current_shift_date", "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    turno_kpi_label = str(stats.get("turno_kpi_label", "Turno — live / totale turno")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    is_on_shift = bool(stats.get("is_on_shift", False))
    is_on_leave = bool(stats.get("is_on_leave", False))
    is_live_accrual = is_on_shift or (is_on_leave and bool(stats.get("current_shift_end", "")))
    if is_on_shift:
        status_color = "#22c55e"
        status_shadow = "0 0 12px rgba(34,197,94,0.75)"
        status_text = f"In turno · {current_turno} · {current_shift_date}"
    elif is_on_leave:
        status_color = "#84cc16"
        status_shadow = "0 0 8px rgba(132,204,22,0.34)"
        status_text = f"Fuori turno · in ferie · {current_shift_date}"
    else:
        status_color = "#64748b"
        status_shadow = "none"
        status_text = "Fuori turno"
    current_shift_end = stats.get("current_shift_end", "")
    current_rate_change_at = stats.get("current_rate_change_at", "")
    next_shift_start = stats.get("next_shift_start", "")
    next_shift_label = str(stats.get("next_shift_label", "—")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    work_days_done = int(stats.get("work_days_done", 0))
    work_days_total = int(stats.get("work_days_total", 0))
    ferie_days_total = int(stats.get("ferie_days_total", 0))
    month_days_total = work_days_total + ferie_days_total
    ferie_suffix = f" + {ferie_days_total} ferie = {month_days_total}" if ferie_days_total else ""
    side_block = f'<div class="turni-live-side">{side_html}</div>' if side_html else ""
    shell_class = "turni-live-shell has-side" if side_html else "turni-live-shell"
    component_height = 286 if (MOBILE_VIEW and side_html) else (330 if MOBILE_VIEW else 126)
    components.html(f"""
    <div class="{shell_class}">
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
          <div id="turni-shift-type" class="turni-subline">{current_shift_type}</div>
        </div>
        <div class="kpi-card" style="border-color:rgba(254,243,199,0.25);">
          <div class="kpi-label">Stato turno</div>
          <div class="turni-status-row">
            <span id="turni-status-dot" class="turni-status-dot" style="background:{status_color}; box-shadow:{status_shadow};"></span>
            <span id="turni-status-text">{status_text}</span>
          </div>
          <div class="turni-rate-row">
            <span id="turni-rate-min" class="kpi-value" style="color:#fef3c7;">{rate_min:.2f} €/min</span>
            <span id="turni-rate-hour" class="kpi-value" style="color:#fef3c7;">{rate_hour:.2f} €/h</span>
          </div>
          <div id="turni-shift-label" style="font-size:11px;color:rgba(255,255,255,0.35);margin-top:4px;">{current_shift}</div>
        </div>
      </div>
      {side_block}
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
      .turni-live-shell.has-side {{
        display: grid;
        grid-template-columns: minmax(0, 1.02fr) minmax(0, .98fr);
        gap: 10px;
        align-items: start;
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
      .turni-rate-row {{
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 12px;
      }}
      .turni-subline {{
        font-size: 12px;
        color: rgba(255,255,255,0.42);
        margin-top: 5px;
      }}
      .turni-live-side {{
        min-width: 0;
      }}
      .turni-summary-compact-title {{
        color: rgba(255,255,255,.88);
        font-size: 13px;
        font-weight: 800;
        margin: 0 0 4px;
      }}
      .turni-grid-scroll {{
        max-height: 236px;
        overflow-y: auto;
        padding-right: 4px;
      }}
      .turni-card-small {{
        background: rgba(255,255,255,0.045);
        border: 0.5px solid rgba(255,255,255,0.10);
        border-left: 4px solid rgba(255,255,255,0.25);
        border-radius: 10px;
        padding: 6px 7px;
        margin-bottom: 5px;
      }}
      #turni-focus-card {{
        border-color: rgba(147,197,253,.55);
        box-shadow: 0 0 0 1px rgba(147,197,253,.26), 0 0 13px rgba(96,165,250,.18);
        background: rgba(96,165,250,.08);
      }}
      .turni-card-small .date {{
        font-size: 10px;
        color: rgba(255,255,255,0.58);
      }}
      .turni-card-small .title {{
        font-size: 12px;
        font-weight: 700;
        margin-top: 1px;
      }}
      .turni-card-small .meta {{
        font-size: 9px;
        color: rgba(255,255,255,0.42);
        margin-top: 2px;
      }}
      .turni-mattina {{ border-left-color:#60a5fa; }}
      .turni-pomeriggio {{ border-left-color:#fb923c; }}
      .turni-notte {{ border-left-color:#64748b; }}
      .turni-ferie {{ border-left-color:#34d399; }}
      @media (max-width: 760px) {{
        .turni-live-shell.has-side {{
          grid-template-columns: minmax(0, .94fr) minmax(0, 1.06fr);
          gap: 7px;
        }}
        .turni-live-side {{
          margin-top: 0;
        }}
        .turni-live-grid {{
          grid-template-columns: 1fr;
          gap: 6px;
        }}
        .kpi-card {{
          padding: 8px 9px;
          min-height: auto;
        }}
        .kpi-label {{
          font-size: 8px;
          letter-spacing: .55px;
          margin-bottom: 4px;
        }}
        .kpi-value {{
          font-size: 14px;
        }}
        .turni-subline {{
          font-size: 9px;
          margin-top: 3px;
        }}
        .turni-status-row {{
          font-size: 10px;
          gap: 5px;
          margin-bottom: 4px;
        }}
        .turni-rate-row {{
          gap: 5px;
        }}
        .turni-grid-scroll {{
          max-height: 199px;
          padding-top: 2px;
        }}
        .turni-summary-compact-title {{
          font-size: 11px;
          margin: 0 0 7px;
        }}
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
      const isOnLeave = {json.dumps(is_on_leave)};
      const isLiveAccrual = {json.dumps(is_live_accrual)};
      const monthEl = document.getElementById("turni-live-month");
      const todayEl = document.getElementById("turni-live-today");
      const dotEl = document.getElementById("turni-status-dot");
      const statusEl = document.getElementById("turni-status-text");
      const rateEl = document.getElementById("turni-rate-min");
      const rateHourEl = document.getElementById("turni-rate-hour");
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
        if (isOnLeave && shiftEnd) {{
          const totalHours = Math.floor(totalMinutes / 60);
          return `Ore ferie mancanti: ${{totalHours}}h ${{String(minutes).padStart(2, "0")}}m`;
        }}
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
        const shouldStart = !isInitiallyOnShift && !isOnLeave && nextShiftStart && Date.now() >= Date.parse(nextShiftStart);
        const extra = elapsedSeconds() * rateSec;
        monthEl.textContent = money(startMonth + extra);
        todayEl.textContent = money(startToday + extra);
        hoursLeftEl.textContent = remainingLabel();
        if (!isInitiallyOnShift && !isOnLeave && nextShiftLabel && nextShiftLabel !== "—") {{
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
        if (ended && (isInitiallyOnShift || isOnLeave)) {{
          if (isOnLeave) {{
            rateEl.textContent = "0.00 €/min";
            rateHourEl.textContent = "0.00 €/h";
            hoursLeftEl.textContent = "Aggiorno ferie...";
          }} else {{
            dotEl.style.background = "#64748b";
            dotEl.style.boxShadow = "none";
            statusEl.textContent = "Fuori turno";
            rateEl.textContent = "0.00 €/min";
            rateHourEl.textContent = "0.00 €/h";
            shiftEl.textContent = "—";
            hoursLeftEl.textContent = "Aggiorno stato turno...";
          }}
          refreshParentSoon();
        }}
      }}

      tick();
      const focusCard = document.getElementById("turni-focus-card");
      const liveScroller = document.querySelector(".turni-live-side .turni-grid-scroll");
      if (focusCard && liveScroller) {{
        liveScroller.scrollTop = Math.max(0, focusCard.offsetTop - liveScroller.offsetTop - 6);
      }}
      setInterval(tick, 1000);
    </script>
    """, height=component_height)


def _turni_month_summary_html(df_turni, month_key, rules, current_work_day=""):
    month_df = df_turni[df_turni["Data"].str.startswith(month_key)].copy()
    month_df = month_df[month_df["Turno"].isin(TURNI_ORARI.keys()) & (month_df["Turno"] != "")]
    if month_df.empty:
        return """
        <div class="turni-summary-compact">
          <div class="turni-summary-compact-title">Riepilogo turni</div>
          <div class="turni-card-small"><div class="meta">Nessun turno nel mese selezionato.</div></div>
        </div>
        """
    month_df = month_df.sort_values("Data")
    today_key = _now_italy().strftime("%Y-%m-%d")
    if current_work_day and current_work_day in set(month_df["Data"].astype(str)):
        focus_date = current_work_day
    else:
        focus_candidates = month_df[month_df["Data"] >= today_key]
        focus_date = focus_candidates.iloc[0]["Data"] if not focus_candidates.empty else month_df.iloc[-1]["Data"]
    cards = ['<div class="turni-summary-compact"><div class="turni-summary-compact-title">Riepilogo turni</div><div class="turni-grid-scroll">']
    for _, r in month_df.iterrows():
        turno = r["Turno"]
        info = _turno_color_info(turno)
        stra_minuti = _turni_row_straordinario_minuti(r)
        sede = _turni_row_sede(r)
        calc = compute_turno(
            r["Data"],
            turno,
            bool(r["Festivo"]),
            rules,
            until=datetime.max.replace(tzinfo=None),
            straordinario_minuti=stra_minuti,
        )
        seg = _segmenti_turno(r["Data"], turno, bool(r["Festivo"]))
        extra_notes = []
        if stra_minuti:
            extra_notes.append(f"Straord. {_format_minutes_label(stra_minuti)}")
        if sede:
            buono = " · buono pasto" if _is_sede_buono_pasto(r["Data"], turno, bool(r["Festivo"]), sede) else ""
            extra_notes.append(f"Sede{buono}")
        extra_txt = f'<div class="meta">{html.escape(" · ".join(extra_notes))}</div>' if extra_notes else ""
        data_dt = pd.to_datetime(r["Data"]).to_pydatetime()
        festivo_txt = " · festivo" if _is_italian_public_holiday(data_dt) else (" · festivo manuale" if bool(r["Festivo"]) else "")
        focus_attr = ' id="turni-focus-card"' if r["Data"] == focus_date else ""
        cards.append(
            f'<div{focus_attr} class="turni-card-small {info["class"]}">'
            f'<div class="date">{html.escape(str(r["Data"]))}{festivo_txt}</div>'
            f'<div class="title" style="color:{info["color"]};">{html.escape(info["emoji"])} {html.escape(str(turno))}</div>'
            f'<div class="meta">{html.escape(seg)} · Totale {html.escape(_money_turni(calc["total"]))}</div>'
            f'<div class="meta">Base {html.escape(_money_turni(calc["base"]))} · Extra {html.escape(_money_turni(calc["extra"]))}</div>'
            f'{extra_txt}'
            f'</div>'
        )
    cards.append("</div></div>")
    return "".join(cards)


def _existing_turni_row_values(df_turni, day_str):
    row = df_turni[df_turni["Data"] == day_str]
    if row.empty:
        return "", False, 0, False
    first = row.iloc[0]
    return (
        str(first.get("Turno", "")),
        bool(first.get("Festivo", False)),
        _turni_row_straordinario_minuti(first),
        _turni_row_sede(first),
    )


def _render_turni_day_action_menu(df_turni, month_days):
    if not month_days:
        return df_turni

    action_day = st.session_state.get("turni_action_day")
    if action_day not in month_days:
        return df_turni

    turno_esistente, festivo_esistente, stra_esistente, sede_esistente = _existing_turni_row_values(df_turni, action_day)
    durata_options = [0, 30, 45, 60, 75, 90, 105, 120]
    durata_default = min(durata_options, key=lambda value: abs(value - int(stra_esistente or 0)))
    action_day_label = pd.to_datetime(action_day).strftime("%d/%m/%Y")
    turno_label = f" · {turno_esistente}" if turno_esistente else ""

    st.markdown(f"#### Modifica giorno · {action_day_label}{turno_label}")
    with st.form(f"turni_day_action_form_{action_day}", clear_on_submit=False):
        menu_cols = st.columns(3, gap="small")
        with menu_cols[0]:
            st.markdown('<span class="turni-day-menu-marker"></span>', unsafe_allow_html=True)
            festivo_value = st.checkbox(
                "Festivo",
                value=bool(festivo_esistente),
                key=f"turni_day_festivo_{action_day}",
            )
        with menu_cols[1]:
            sede_value = st.checkbox(
                "Sede",
                value=bool(sede_esistente),
                key=f"turni_day_sede_{action_day}",
            )
        with menu_cols[2]:
            durata_value = st.selectbox(
                "Straordinario",
                durata_options,
                index=durata_options.index(durata_default),
                format_func=lambda value: "No" if value == 0 else _format_minutes_label(value),
                key=f"turni_day_stra_{action_day}",
            )

        action_cols = st.columns(2, gap="small")
        with action_cols[0]:
            save_day = st.form_submit_button("Salva giorno", use_container_width=True)
        with action_cols[1]:
            close_day = st.form_submit_button("Chiudi", use_container_width=True)

    if save_day:
        df_new = _upsert_turni_day(
            df_turni,
            action_day,
            festivo=festivo_value,
            straordinario_minuti=durata_value,
            sede=sede_value,
        )
        st.session_state.pop("turni_action_day", None)
        if "turni_day" in st.query_params:
            del st.query_params["turni_day"]
        return _save_turni_and_rerun(df_new, "Giorno aggiornato in bozza, ma non salvato su Google Sheets.")
    if close_day:
        st.session_state.pop("turni_action_day", None)
        if "turni_day" in st.query_params:
            del st.query_params["turni_day"]
        st.rerun()
    return df_turni


def _render_turni_report(report):
    def card(label, value, sub="", accent="#f8fafc"):
        return (
            f'<div class="turni-report-card" style="--accent:{html.escape(str(accent))};">'
            f'<div class="turni-report-label">{html.escape(str(label))}</div>'
            f'<div class="turni-report-value">{html.escape(str(value))}</div>'
            f'<div class="turni-report-sub">{html.escape(str(sub))}</div>'
            '</div>'
        )

    cards = [
        card("Giorni lavorati", report.get("work_days", 0), "escluse ferie", "#60a5fa"),
        card("Ferie", report.get("ferie_days", 0), "giorni base", "#34d399"),
        card(
            "Sedi",
            f'{report.get("sede_days", 0)} / {report.get("sede_required", 0)}',
            f'da fare {report.get("sede_remaining", 0)}',
            "#fb923c",
        ),
        card(
            "Buoni pasto",
            f'{report.get("buoni_pasto_days", 0)}',
            _money_turni(report.get("buoni_pasto_total", 0.0)),
            "#fde68a",
        ),
        card(
            "Straordinari",
            _format_minutes_label(report.get("straordinario_minutes", 0)),
            _money_turni(report.get("straordinario_total", 0.0)),
            "#c084fc",
        ),
        card("Smart target", "15", "giorni/mese", "#94a3b8"),
    ]
    turn_counts = report.get("turn_counts", {})
    turn_rows = "".join(
        f'<div><span>{html.escape(str(name))}</span><strong>{int(value)}</strong></div>'
        for name, value in turn_counts.items()
        if value
    ) or "<div><span>Nessun turno</span><strong>0</strong></div>"
    type_counts = report.get("turn_type_counts", {})
    type_rows = "".join(
        f'<div><span>{html.escape(str(name))}</span><strong>{int(value)}</strong></div>'
        for name, value in sorted(type_counts.items())
    ) or "<div><span>Nessun dettaglio</span><strong>0</strong></div>"
    hours_rows = "".join(
        f'<div><span>Magg. {float(pct):g}%</span><strong>{hours:.2f}h</strong></div>'
        for pct, hours in sorted(report.get("hours_by_pct", {}).items())
        if abs(hours) > 0.001
    ) or "<div><span>Nessuna maggiorazione</span><strong>0h</strong></div>"
    st.markdown(f"""
    <style>
      .turni-report-grid {{
        display:grid;
        grid-template-columns:repeat(3,minmax(0,1fr));
        gap:10px;
        margin: 4px 0 14px;
      }}
      .turni-report-card {{
        background:rgba(255,255,255,.055);
        border:0.5px solid color-mix(in srgb, var(--accent) 34%, rgba(255,255,255,.12));
        border-radius:12px;
        padding:10px 12px;
      }}
      .turni-report-label {{
        color:var(--accent);
        font-size:11px;
        text-transform:uppercase;
        letter-spacing:.06em;
        font-weight:900;
      }}
      .turni-report-value {{
        color:var(--accent);
        font-size:20px;
        font-weight:900;
        margin-top:5px;
      }}
      .turni-report-sub {{
        color:rgba(255,255,255,.45);
        font-size:11px;
        margin-top:3px;
      }}
      .turni-report-lists {{
        display:grid;
        grid-template-columns:repeat(3,minmax(0,1fr));
        gap:12px;
      }}
      .turni-report-list {{
        background:rgba(255,255,255,.035);
        border:0.5px solid rgba(255,255,255,.10);
        border-radius:12px;
        padding:12px;
      }}
      .turni-report-list h4 {{
        margin:0 0 8px;
        color:#93c5fd;
        font-size:14px;
      }}
      .turni-report-list div {{
        display:flex;
        justify-content:space-between;
        gap:10px;
        padding:5px 0;
        border-top:0.5px solid rgba(255,255,255,.07);
        color:rgba(255,255,255,.62);
        font-size:12px;
      }}
      .turni-report-list strong {{
        color:#fef3c7;
      }}
      @media (max-width: 767px) {{
        .turni-report-grid {{ grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; }}
        .turni-report-card {{ padding:8px 7px; }}
        .turni-report-label {{ font-size:8.5px; letter-spacing:.03em; }}
        .turni-report-value {{ font-size:15px; }}
        .turni-report-sub {{ font-size:8.5px; }}
        .turni-report-lists {{ grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; }}
        .turni-report-list {{ padding:8px 7px; }}
        .turni-report-list h4 {{ font-size:11px; }}
        .turni-report-list div {{ font-size:9px; gap:4px; }}
      }}
    </style>
    <div class="turni-report-grid">{"".join(cards)}</div>
    <div class="turni-report-lists">
      <div class="turni-report-list"><h4>Turni</h4>{turn_rows}</div>
      <div class="turni-report-list"><h4>Tipi turno</h4>{type_rows}</div>
      <div class="turni-report-list"><h4>Ore maggiorazione</h4>{hours_rows}</div>
    </div>
    """, unsafe_allow_html=True)


def render_turni_guadagni_section():
    st.markdown('<div id="mobile-turni" class="mobile-anchor"></div><div class="section-pill">⏱️ Guadagni Turni</div>', unsafe_allow_html=True)
    rules = get_turni_rules()
    rules = _apply_turni_rules_from_widgets(rules)
    if "turni_calendar_month" not in st.session_state:
        today_month = _now_italy().date()
        st.session_state.turni_calendar_month = datetime(today_month.year, today_month.month, 1).date()
    turni_month_param = st.query_params.get("turni_month")
    if isinstance(turni_month_param, list):
        turni_month_param = turni_month_param[0] if turni_month_param else None
    if isinstance(turni_month_param, str):
        try:
            parsed_month = datetime.strptime(turni_month_param, "%Y-%m").date()
            st.session_state.turni_calendar_month = datetime(parsed_month.year, parsed_month.month, 1).date()
        except ValueError:
            pass
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

    today = _now_italy().date()
    current_month_key = today.strftime("%Y-%m")
    is_selected_current_month = month_key == current_month_key
    stats = compute_turni_dashboard(df_turni, rules)
    current_work_day = (
        stats.get("current_shift_start_date", "")
        if (stats.get("is_on_shift", False) or stats.get("is_on_leave", False))
        else today.strftime("%Y-%m-%d")
    )

    summary_focus_day = current_work_day if is_selected_current_month else ""
    mobile_summary_html = _turni_month_summary_html(df_turni, month_key, rules, summary_focus_day) if MOBILE_VIEW else ""
    if is_selected_current_month:
        render_live_turni_kpis(stats, mobile_summary_html)
    else:
        render_selected_month_turni_kpis(df_turni, rules, month_key, mobile_summary_html)

    tab_cal, tab_rules, tab_report = st.tabs(["📅 Turni", "⚙️ Regole", "📊 Riepilogo"])

    with tab_cal:
        year, month = selected_month.year, selected_month.month

        if MOBILE_VIEW:
            cal_col = st.container()
            summary_col = None
        else:
            cal_col, summary_col = st.columns(LAYOUT_COLONNE["turni_calendario_riepilogo"], gap="medium")

        with cal_col:
            st.markdown('<div class="turni-calendar-wrap">', unsafe_allow_html=True)
            if MOBILE_VIEW:
                prev_month = _add_months_turni(selected_month, -1).strftime("%Y-%m")
                next_month = _add_months_turni(selected_month, 1).strftime("%Y-%m")
                st.markdown(
                    f"""
                    <div class="mobile-calendar-navline">
                      <a class="mobile-calendar-arrow" href="?view=mobile&mobile_section=Turni&turni_month={prev_month}#mobile-turni" target="_self">←</a>
                      <div class="mobile-calendar-title">📅 Calendario · {_turni_month_label(selected_month)}</div>
                      <a class="mobile-calendar-arrow" href="?view=mobile&mobile_section=Turni&turni_month={next_month}#mobile-turni" target="_self">→</a>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            else:
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
            cal = calendar.Calendar(firstweekday=0)
            month_days = [
                datetime(year, month, day).strftime("%Y-%m-%d")
                for day in range(1, calendar.monthrange(year, month)[1] + 1)
            ]
            if MOBILE_VIEW:
                turni_day_param = st.query_params.get("turni_day")
                if isinstance(turni_day_param, list):
                    turni_day_param = turni_day_param[0] if turni_day_param else None
                if turni_day_param in month_days:
                    st.session_state["turni_action_day"] = turni_day_param

                calendar_cells = ['<div class="mobile-calendar-grid">']
                for wd in weekdays:
                    calendar_cells.append(f'<div class="mobile-calendar-head">{html.escape(wd)}</div>')
                selected_action_day = st.session_state.get("turni_action_day")
                for week in cal.monthdatescalendar(year, month):
                    for day in week:
                        if day.month != month:
                            calendar_cells.append('<div class="mobile-calendar-day empty"></div>')
                            continue
                        day_str = day.strftime("%Y-%m-%d")
                        row = df_turni[df_turni["Data"] == day_str]
                        turno_corrente = "" if row.empty else str(row.iloc[0].get("Turno", ""))
                        info = _turno_color_info(turno_corrente)
                        shift_html = ""
                        if turno_corrente in TURNI_ORARI and turno_corrente:
                            shift_html = (
                                f'<span class="shift" style="color:{html.escape(info["color"])};">'
                                f'{html.escape(info["short"])}</span>'
                            )
                        stra_minuti = 0 if row.empty else _turni_row_straordinario_minuti(row.iloc[0])
                        sede = False if row.empty else _turni_row_sede(row.iloc[0])
                        markers_html = ""
                        if stra_minuti:
                            markers_html += '<span class="mobile-day-extra">+</span>'
                        if sede:
                            markers_html += '<span class="mobile-day-sede">S</span>'
                        day_is_festive = (
                            day.weekday() == 6
                            or _is_italian_public_holiday(datetime(day.year, day.month, day.day))
                            or (not row.empty and bool(row.iloc[0]["Festivo"]))
                        )
                        day_num_class = "holiday" if day_is_festive else ""
                        today_dot = '<span class="today-dot">•</span>' if day_str == current_work_day else ""
                        selected_class = " selected" if selected_action_day == day_str else ""
                        href = f"?view=mobile&mobile_section=Turni&turni_month={month_key}&turni_day={day_str}#mobile-turni"
                        calendar_cells.append(
                            f'<a href="{href}" target="_self" class="mobile-calendar-day{selected_class}">'
                            f'{today_dot}<span class="{day_num_class}">{day.day}</span>{shift_html}{markers_html}'
                            '</a>'
                        )
                calendar_cells.append("</div>")
                st.markdown("".join(calendar_cells), unsafe_allow_html=True)
            else:
                cols = st.columns(7)
                for c, wd in zip(cols, weekdays):
                    c.markdown(f"<div style='text-align:center;color:rgba(255,255,255,0.45);font-size:12px;'>{wd}</div>", unsafe_allow_html=True)

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
                            stra_minuti = 0
                            sede = False
                        else:
                            turno_corrente = row.iloc[0]["Turno"]
                            info = _turno_color_info(turno_corrente)
                            current_label = f" :{info['md_color']}[**{info['short']}**]" if turno_corrente in TURNI_ORARI and turno_corrente else ""
                            stra_minuti = _turni_row_straordinario_minuti(row.iloc[0])
                            sede = _turni_row_sede(row.iloc[0])
                        if stra_minuti:
                            current_label += " :violet[**+**]"
                        if sede:
                            current_label += " :orange[**S**]"
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
                            st.session_state["turni_action_day"] = day_str
                            st.rerun()

            st.markdown("""
            <div class="mobile-calendar-legend">
              <span style="border-bottom:4px solid #60a5fa;">Mattina</span>
              <span style="border-bottom:4px solid #fb923c;">Pomeriggio</span>
              <span style="border-bottom:4px solid #64748b;">Notte</span>
              <span style="border-bottom:4px solid #34d399;">Ferie</span>
              <span style="color:#ef4444;">Numero rosso = festivo</span>
            </div>
            """, unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

            df_turni = _render_turni_day_action_menu(df_turni, month_days)

        if summary_col is not None:
          with summary_col:
            st.markdown("#### 🗓️ Riepilogo turni del mese")
            month_df = df_turni[df_turni["Data"].str.startswith(month_key)].copy()
            month_df = month_df[month_df["Turno"].isin(TURNI_ORARI.keys()) & (month_df["Turno"] != "")]
            if month_df.empty:
                st.info("Nessun turno inserito per il mese selezionato.")
            else:
                month_df = month_df.sort_values("Data")
                today_key = _now_italy().strftime("%Y-%m-%d")
                if current_work_day and current_work_day in set(month_df["Data"].astype(str)):
                    focus_date = current_work_day
                else:
                    focus_candidates = month_df[month_df["Data"] >= today_key]
                    focus_date = focus_candidates.iloc[0]["Data"] if not focus_candidates.empty else month_df.iloc[-1]["Data"]
                cards = ['<div class="turni-grid-scroll">']
                for _, r in month_df.iterrows():
                    turno = r["Turno"]
                    info = _turno_color_info(turno)
                    stra_minuti = _turni_row_straordinario_minuti(r)
                    sede = _turni_row_sede(r)
                    calc = compute_turno(
                        r["Data"],
                        turno,
                        bool(r["Festivo"]),
                        rules,
                        until=datetime.max.replace(tzinfo=None),
                        straordinario_minuti=stra_minuti,
                    )
                    seg = _segmenti_turno(r["Data"], turno, bool(r["Festivo"]))
                    extra_notes = []
                    if stra_minuti:
                        extra_notes.append(f"Straord. {_format_minutes_label(stra_minuti)}")
                    if sede:
                        buono = " · buono pasto" if _is_sede_buono_pasto(r["Data"], turno, bool(r["Festivo"]), sede) else ""
                        extra_notes.append(f"Sede{buono}")
                    extra_txt = f'<div class="meta">{html.escape(" · ".join(extra_notes))}</div>' if extra_notes else ""
                    data_dt = pd.to_datetime(r["Data"]).to_pydatetime()
                    festivo_txt = " · festivo" if _is_italian_public_holiday(data_dt) else (" · festivo manuale" if bool(r["Festivo"]) else "")
                    focus_attr = ' id="turni-focus-card"' if r["Data"] == focus_date else ""
                    cards.append(
                        f'<div{focus_attr} class="turni-card-small {info["class"]}">'
                        f'<div class="date">{r["Data"]}{festivo_txt}</div>'
                        f'<div class="title" style="color:{info["color"]};">{info["emoji"]} {turno}</div>'
                        f'<div class="meta">{seg} · Totale {_money_turni(calc["total"])}</div>'
                        f'<div class="meta">Base {_money_turni(calc["base"])} · Extra {_money_turni(calc["extra"])}</div>'
                        f'{extra_txt}'
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
            st.warning("Modifiche turni in bozza: Google Sheets non ha confermato il salvataggio.")

    with tab_rules:
        c1, c2 = st.columns(2)
        with c1:
            if MOBILE_VIEW:
                st.markdown('<span class="turni-rules-marker"></span>', unsafe_allow_html=True)
            st.markdown("""
            <div style="margin:0 0 12px; padding-top:0;">
              <h5 style="margin:0;color:#93c5fd;">Maggiorazioni</h5>
            </div>
            """, unsafe_allow_html=True)
            st.markdown("""
            <div style="
                margin:0 0 6px;
                color:#fef3c7;
                font-size:13px;
                font-weight:900;
                letter-spacing:.2px;
                text-shadow:0 0 12px rgba(250,204,21,.25);
            ">Paga oraria base</div>
            """, unsafe_allow_html=True)
            rules["paga_oraria"] = st.number_input("Paga oraria base", value=float(rules["paga_oraria"]), step=0.10, key="turni_paga", label_visibility="collapsed")
            rules["quota_fissa_mensile"] = 0.0
            rules["m_p_feriale_pct"] = st.number_input("Mattina/Pomeriggio feriale %", value=float(rules["m_p_feriale_pct"]), step=1.0, key="turni_mp_feriale")
            rules["m_p_festivo_giorno_pct"] = st.number_input("Mattina/Pomeriggio festivo 06-18 %", value=float(rules["m_p_festivo_giorno_pct"]), step=1.0, key="turni_mp_festivo")
            rules["notte_feriale_pct"] = st.number_input("Notte feriale %", value=float(rules["notte_feriale_pct"]), step=1.0, key="turni_notte_feriale")
            rules["festivo_sera_notte_pct"] = st.number_input("Festivo sera/notte %", value=float(rules["festivo_sera_notte_pct"]), step=1.0, key="turni_festivo_notte")
            st.markdown("""
            <div style="border-top:1px solid rgba(255,255,255,.14); margin:18px 0 12px; padding-top:10px;">
              <h5 style="margin:0;color:#c084fc;">Straordinari</h5>
            </div>
            """, unsafe_allow_html=True)
            stra_cols = st.columns(2)
            with stra_cols[0]:
                rules["stra_mattina_feriale_pct"] = st.number_input("M feriale %", value=float(rules.get("stra_mattina_feriale_pct", 25.0)), step=1.0, key="turni_stra_m_feriale")
                rules["stra_pomeriggio_feriale_pct"] = st.number_input("P feriale %", value=float(rules.get("stra_pomeriggio_feriale_pct", 40.0)), step=1.0, key="turni_stra_p_feriale")
                rules["stra_notte_feriale_pct"] = st.number_input("N feriale %", value=float(rules.get("stra_notte_feriale_pct", 50.0)), step=1.0, key="turni_stra_n_feriale")
                rules["stra_ferie_feriale_pct"] = st.number_input("Ferie feriale %", value=float(rules.get("stra_ferie_feriale_pct", 25.0)), step=1.0, key="turni_stra_f_feriale")
            with stra_cols[1]:
                rules["stra_mattina_festivo_pct"] = st.number_input("M festivo %", value=float(rules.get("stra_mattina_festivo_pct", 55.0)), step=1.0, key="turni_stra_m_festivo")
                rules["stra_pomeriggio_festivo_pct"] = st.number_input("P festivo %", value=float(rules.get("stra_pomeriggio_festivo_pct", 60.0)), step=1.0, key="turni_stra_p_festivo")
                rules["stra_notte_festivo_pct"] = st.number_input("N festivo %", value=float(rules.get("stra_notte_festivo_pct", 70.0)), step=1.0, key="turni_stra_n_festivo")
                rules["stra_ferie_festivo_pct"] = st.number_input("Ferie festivo %", value=float(rules.get("stra_ferie_festivo_pct", 50.0)), step=1.0, key="turni_stra_f_festivo")
        with c2:
            if MOBILE_VIEW:
                st.markdown('<span class="turni-rules-marker"></span>', unsafe_allow_html=True)
            st.markdown("""
            <div style="margin:0 0 12px; padding-top:0;">
              <h5 style="margin:0;color:#fef3c7;">Indennità</h5>
            </div>
            """, unsafe_allow_html=True)
            rules["ind_m_p_feriale"] = st.number_input("Indennità M/P feriale", value=float(rules["ind_m_p_feriale"]), step=1.0, key="turni_ind_mp_f")
            rules["ind_notte_feriale"] = st.number_input("Indennità notte feriale", value=float(rules["ind_notte_feriale"]), step=1.0, key="turni_ind_n_f")
            rules["ind_m_p_festivo"] = st.number_input("Indennità M/P festiva", value=float(rules["ind_m_p_festivo"]), step=1.0, key="turni_ind_mp_fe")
            rules["ind_notte_festiva"] = st.number_input("Indennità notte festiva", value=float(rules["ind_notte_festiva"]), step=1.0, key="turni_ind_n_fe")
            st.markdown("""
            <div style="border-top:1px solid rgba(255,255,255,.14); margin:18px 0 12px; padding-top:10px;">
              <h5 style="margin:0;color:#34d399;">Sede e mensile</h5>
            </div>
            """, unsafe_allow_html=True)
            rules["buono_pasto"] = st.number_input("Buono pasto", value=float(rules.get("buono_pasto", 7.0)), step=0.50, key="turni_buono_pasto")
            rules["smart_target"] = st.number_input("Smart target mensile", value=float(rules.get("smart_target", 15.0)), step=1.0, key="turni_smart_target")
            rules["accrediti_mensili"] = st.number_input("Competenze fisse mensili", value=float(rules.get("accrediti_mensili", 0.0)), step=1.0, key="turni_accrediti_mensili")
            rules["trattenute_mensili"] = st.number_input("Trattenute fisse mensili", value=float(rules.get("trattenute_mensili", 0.0)), step=1.0, key="turni_trattenute_mensili")
            st.markdown("""
            <div class="kpi-card">
                <div class="kpi-label">Regole applicate</div>
                <div style="font-size:12px;color:rgba(255,255,255,0.65);line-height:1.5;">
                M 06-14: 20% / 50% + 6€/15€<br>
                P 14-18: 20% / 50% + 6€/15€<br>
                P 18-22: 20% / 60%, senza seconda indennità<br>
                N 22-06: 50% / 60% + 15€/25€<br>
                Straordinari: percentuali per turno e fer/fest<br>
                Sabato feriale: nessuna indennità<br>
                Ferie: 8 ore base<br>
                Sede: buono pasto se non mattina feriale
                </div>
            </div>
            """, unsafe_allow_html=True)

        st.session_state.turni_rules = rules
        st.caption("Le regole sono salvate nella sessione Streamlit. I turni arrivano da Google Calendar; il festivo manuale viene salvato subito su Google Sheets quando lo modifichi.")

    with tab_report:
        month_report = compute_turni_month_report(df_turni, rules, month_key)
        _render_turni_report(month_report)
# ─────────────────────────────────────────────────────────────────────────────

def main():
    load_spese_fisse_settings()
    load_altre_entrate_settings()

    if MOBILE_VIEW:
        col_left = st.container()
        col_center = st.container()
    else:
        col_left, col_center, col_right = st.columns(LAYOUT_COLONNE["titolo_dashboard"], gap="large")
    if _mobile_show("Panoramica"):
        with col_left:
            if MOBILE_VIEW:
                st.markdown('<div id="mobile-dashboard" class="mobile-anchor"></div>', unsafe_allow_html=True)
            else:
                st.markdown('<div id="mobile-dashboard" class="mobile-anchor"></div><div class="section-pill">💎 Dashboard Finanziaria</div>', unsafe_allow_html=True)
        with col_center:
            if not MOBILE_VIEW:
                st.markdown("<h1 style='text-align: center;'>Calcolatore di Spese Personali</h1>", unsafe_allow_html=True)

        st.markdown('<div style="height:4px;"></div>', unsafe_allow_html=True)
        st.markdown('<div class="section-pill">💶 Stipendi</div>', unsafe_allow_html=True)
    if MOBILE_VIEW:
        mobile_stip_col = st.container()
        mobile_quota_col = st.container()
        mobile_risp_col = st.container()
        mobile_note_col = st.container()
    else:
        col_stip_inserimento1, col_stip_inserimento2, col_stip_inserimento3, col_stip_inserimento4 = st.columns(LAYOUT_COLONNE["header_stipendi_note"], gap="large")
    if MOBILE_VIEW:
        col1 = col2 = col3 = None
    else:
        col1, col2, col3 = st.columns(LAYOUT_COLONNE["dashboard_principale"], gap="large")

    def _parse_mobile_amount(raw_value, fallback=0.0, max_value=None):
        text = str(raw_value).strip().replace("€", "").replace(" ", "")
        if not text:
            value = float(fallback)
        else:
            if "," in text and "." in text:
                if text.rfind(",") > text.rfind("."):
                    text = text.replace(".", "").replace(",", ".")
                else:
                    text = text.replace(",", "")
            elif "," in text:
                text = text.replace(".", "").replace(",", ".")
            try:
                value = float(text)
            except ValueError:
                value = float(fallback)
        if max_value is not None:
            value = min(value, float(max_value))
        return max(0.0, value)

    if MOBILE_VIEW:
        mobile_salary_defaults = {
            "mobile_salary_stipendio_percepito_value": float(input_stipendio_percepito),
            "mobile_salary_budget_da_stipendio_value": float(input_budget_da_stipendio),
            "mobile_salary_risparmi_mese_precedente_value": float(input_risparmi_mese_precedente),
        }
        for salary_key, salary_default in mobile_salary_defaults.items():
            st.session_state.setdefault(salary_key, salary_default)

        if _mobile_show("Panoramica"):
            salary_col1, salary_col2, salary_col3 = st.columns(3, gap="small")
            with salary_col1:
                st.markdown('<div class="mobile-salary-field-title green">Stipendio percepito</div>', unsafe_allow_html=True)
                stipendio_percepito = st.number_input(
                    "Stipendio percepito",
                    min_value=0.0,
                    value=float(st.session_state["mobile_salary_stipendio_percepito_value"]),
                    step=50.0,
                    key="mobile_salary_stipendio_percepito_num",
                    label_visibility="collapsed",
                    format="%.0f"
                )
            if "mobile_salary_budget_da_stipendio_num" in st.session_state:
                st.session_state["mobile_salary_budget_da_stipendio_num"] = min(
                    float(st.session_state["mobile_salary_budget_da_stipendio_num"]),
                    float(stipendio_percepito)
                )
            with salary_col2:
                st.markdown('<div class="mobile-salary-field-title blue">Quota stip. scelta</div>', unsafe_allow_html=True)
                budget_da_stipendio = st.number_input(
                    "Quota stip. scelta",
                    min_value=0.0,
                    max_value=float(stipendio_percepito),
                    value=min(float(st.session_state["mobile_salary_budget_da_stipendio_value"]), float(stipendio_percepito)),
                    step=50.0,
                    key="mobile_salary_budget_da_stipendio_num",
                    label_visibility="collapsed",
                    format="%.0f"
                )
            with salary_col3:
                st.markdown('<div class="mobile-salary-field-title yellow">Risparmi mese prec.</div>', unsafe_allow_html=True)
                risparmi_mese_precedente = st.number_input(
                    "Risparmi mese prec.",
                    min_value=0.0,
                    value=float(st.session_state["mobile_salary_risparmi_mese_precedente_value"]),
                    step=50.0,
                    key="mobile_salary_risparmi_mese_precedente_num",
                    label_visibility="collapsed",
                    format="%.0f"
                )
            st.session_state["mobile_salary_stipendio_percepito_value"] = stipendio_percepito
            st.session_state["mobile_salary_budget_da_stipendio_value"] = budget_da_stipendio
            st.session_state["mobile_salary_risparmi_mese_precedente_value"] = risparmi_mese_precedente
            st.markdown(
                '<div class="mobile-salary-note-grid">'
                '<span></span><span></span>'
                '<span class="mobile-compact-input-note">Il resto andrà nei risparmi.</span>'
                '</div>',
                unsafe_allow_html=True
            )
        else:
            stipendio_percepito = _parse_mobile_amount(
                st.session_state.get("mobile_salary_stipendio_percepito_value", input_stipendio_percepito),
                input_stipendio_percepito
            )
            budget_da_stipendio = _parse_mobile_amount(
                st.session_state.get("mobile_salary_budget_da_stipendio_value", input_budget_da_stipendio),
                input_budget_da_stipendio,
                max_value=stipendio_percepito
            )
            risparmi_mese_precedente = _parse_mobile_amount(
                st.session_state.get("mobile_salary_risparmi_mese_precedente_value", input_risparmi_mese_precedente),
                input_risparmi_mese_precedente
            )
            st.session_state["mobile_salary_stipendio_percepito_value"] = stipendio_percepito
            st.session_state["mobile_salary_budget_da_stipendio_value"] = budget_da_stipendio
            st.session_state["mobile_salary_risparmi_mese_precedente_value"] = risparmi_mese_precedente
    else:
        with col_stip_inserimento1:
            st.markdown('<div class="salary-input-label">Stipendio percepito</div>', unsafe_allow_html=True)
            stipendio_percepito = st.number_input("Inserisci lo stipendio effettivamente percepito:", min_value=input_stipendio_percepito, step=50, label_visibility="collapsed")
            st.markdown('<div style="height:10px;"></div><div class="salary-input-label">Risparmio mese prec.</div>', unsafe_allow_html=True)
            risparmi_mese_precedente = st.number_input("Inserisci quanto hai risparmiato nel mese precedente:", min_value=input_risparmi_mese_precedente, step=50, label_visibility="collapsed")
        with col_stip_inserimento2:
            st.markdown('<div class="salary-input-label">Quota stipendio scelta</div>', unsafe_allow_html=True)
            budget_da_stipendio_default = min(float(input_budget_da_stipendio), float(stipendio_percepito))
            budget_da_stipendio = st.number_input(
                "Inserisci la parte dello stipendio che scegli di usare:",
                min_value=0.0,
                max_value=float(stipendio_percepito),
                value=budget_da_stipendio_default,
                step=50.0,
                label_visibility="collapsed"
            )
            st.markdown('<div style="font-size:11px;color:rgba(255,255,255,.42);margin-top:4px;">Il resto andrà nei risparmi.</div>', unsafe_allow_html=True)
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

    if MOBILE_VIEW:
        col_stip_inserimento3 = st.container()
        col_stip_inserimento4 = st.container()

    with col_stip_inserimento3:
        _ts = f"€{entrate_mensili_totali:,.2f}"
        _tu = f"€{budget_mensile_disponibile:,.2f}"

        if MOBILE_VIEW:
            pass
        else:
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
    white-space: nowrap !important;
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
            worksheet_name = "Note e Obiettivo risparmio mensile"

            if "note_df_draft" not in st.session_state:
                df_note = load_data_gsheets(worksheet_name, NOTE_HEADERS)
                note_loaded_from_sheet = not df_note.empty
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
            if "risparmio_desiderato_promemoria" in st.session_state:
                risparmio_desiderato_corrente = float(st.session_state["risparmio_desiderato_promemoria"])
            target_budget = calcola_target_budget_dinamico(sum(SPESE["Fisse"].values()))
            budget_disponibile_target = target_budget["budget_disponibile_target"]
            risparmio_auto_variabili_target = target_budget["risparmio_auto_variabili"]

            if MOBILE_VIEW:
                budget_left_col, budget_card_col = st.columns([1, 1], gap="small")
                obiettivi_col = budget_card_col
            else:
                budget_card_col, obiettivi_col, budget_spacer = st.columns([1.06, 0.44, 1.20], gap="small")
            if MOBILE_VIEW or _mobile_show("Panoramica"):
                if MOBILE_VIEW:
                    with budget_left_col:
                        st.markdown(f"""
                        <div class="mobile-budget-left-marker"></div>
                        <div class="mobile-kpi-summary-grid">
                            <div class="kpi-card">
                                <div class="kpi-label">Entrate mensili totali</div>
                                <div class="kpi-value" style="color:#77DD77;">{_ts}</div>
                                <div style="font-size:10px;color:rgba(255,255,255,0.42);margin-top:3px;">
                                    Stipendio percepito + altre entrate
                                </div>
                            </div>
                            <div class="kpi-card">
                                <div class="kpi-label">Budget mensile disponibile</div>
                                <div class="kpi-value" style="color:#60a5fa;">{_tu}</div>
                                <div style="font-size:10px;color:rgba(255,255,255,0.42);margin-top:3px;">
                                    Quota stipendio scelta + altre entrate
                                </div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                with budget_card_col:
                    entrate_totali_target = budget_disponibile_target + max(0, risparmio_desiderato_corrente - risparmio_auto_variabili_target)
                    gap_budget_ideale = max(0, budget_disponibile_target - budget_mensile_disponibile)
                    gap_entrate_ideali = max(0, entrate_totali_target - entrate_mensili_totali)
                    budget_status = "ok" if gap_budget_ideale <= 0 else f"-€{gap_budget_ideale:,.2f}"
                    entrate_status = "ok" if gap_entrate_ideali <= 0 else f"-€{gap_entrate_ideali:,.2f}"
                    st.markdown(f"""
                    <div class="mobile-budget-right-marker"></div>
                    <div class="budget-memory-card">
                        <div class="budget-memory-title">Budget desiderato</div>
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
    if MOBILE_VIEW:
        col1 = st.container()
        col2 = st.container()
        col3 = st.container()
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
        if _mobile_show("Spese"):
            st.markdown('<div id="mobile-spese" class="mobile-anchor"></div><div class="section-pill">🏠 Spese Fisse</div>', unsafe_allow_html=True)
            tab_spese_fisse, tab_decisioni_fisse = st.tabs(["📋 Spese", "⚙️ Decisioni"])

            with tab_decisioni_fisse:
                settings = SPESE["Fisse"].copy()
                metadata = st.session_state.get("spese_fisse_metadata", {})
                gruppi_disponibili = _spesa_fissa_gruppi_disponibili(metadata)

                st.markdown("#### Aggiungi spesa")
                add_nome_col, add_importo_col, add_gruppo_nuovo_col = st.columns(3, gap="small")
                with add_nome_col:
                    if MOBILE_VIEW:
                        st.markdown('<span class="fixed-expense-add-main-marker"></span>', unsafe_allow_html=True)
                    nuova_spesa_nome = st.text_input("Nome nuova spesa", key="nuova_spesa_fissa_nome")
                with add_importo_col:
                    nuova_spesa_importo = st.number_input("Importo nuova spesa", min_value=0.0, value=0.0, step=5.0, key="nuova_spesa_fissa_importo")
                with add_gruppo_nuovo_col:
                    nuovo_gruppo = st.text_input(
                        "Nuovo gruppo visivo da aggiungere",
                        key="nuovo_gruppo_spese_fisse",
                        placeholder="Es. Animali, Viaggi, Donazioni..."
                    ).strip()
                if nuovo_gruppo and nuovo_gruppo not in gruppi_disponibili:
                    gruppi_disponibili.append(nuovo_gruppo)
                add_meta_col1, add_meta_col2, add_meta_col3 = st.columns(3, gap="small")
                with add_meta_col1:
                    if MOBILE_VIEW:
                        st.markdown('<span class="fixed-expense-add-meta-marker"></span>', unsafe_allow_html=True)
                    nuova_spesa_categoria = st.selectbox("Colore categoria nuova spesa", SPESA_FISSA_CATEGORIE, key="nuova_spesa_fissa_categoria")
                with add_meta_col2:
                    nuova_spesa_carta = st.selectbox("Carta nuova spesa", SPESA_FISSA_CARTE, key="nuova_spesa_fissa_carta")
                with add_meta_col3:
                    nuova_spesa_gruppo = st.selectbox("Gruppo visivo nuova spesa", gruppi_disponibili, key="nuova_spesa_fissa_gruppo")

                st.markdown("#### Elimina spesa")
                elimina_spesa = st.selectbox("Voce da eliminare", [""] + list(settings.keys()), key="elimina_spesa_fissa")
                st.markdown(
                    '<div style="border-top: 1px solid rgba(148, 163, 184, .28); margin: 26px 0 18px;"></div>'
                    '<h4 style="text-align: center; margin: 0 0 18px;">Modifica spese esistenti</h4>',
                    unsafe_allow_html=True,
                )

                editor_cols = st.columns(3 if MOBILE_VIEW else 2)
                editable_settings = {}
                editable_metadata = {}
                if MOBILE_VIEW:
                    for editor_col in editor_cols:
                        with editor_col:
                            st.markdown('<span class="fixed-expense-editor-marker"></span>', unsafe_allow_html=True)
                for idx, (voce, importo) in enumerate(settings.items()):
                    with editor_cols[idx % len(editor_cols)]:
                        current_categoria = metadata.get(voce, {}).get("Categoria", _infer_spesa_fissa_categoria(voce))
                        current_carta = metadata.get(voce, {}).get("Carta", _infer_spesa_fissa_carta(voce))
                        current_gruppo = metadata.get(voce, {}).get("Gruppo", _infer_spesa_fissa_gruppo(voce))
                        titolo_colore = SPESA_FISSA_CATEGORIA_COLORI.get(current_categoria, "#f8fafc")
                        st.markdown(
                            f'<div style="font-weight: 800; color: {titolo_colore}; margin: 0 0 8px; line-height: 1.15;">{html.escape(str(voce))}</div>',
                            unsafe_allow_html=True,
                        )
                        editable_settings[voce] = st.number_input(
                            "Importo",
                            min_value=0.0,
                            value=float(importo),
                            step=5.0,
                            key=f"spesa_fissa_importo_{voce}"
                        )
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
                    if MOBILE_VIEW:
                        st.markdown('<span class="fixed-expense-actions-marker"></span>', unsafe_allow_html=True)
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
                    if MOBILE_VIEW:
                        st.markdown('<span class="fixed-expense-actions-marker"></span>', unsafe_allow_html=True)
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

                spese_meta = st.session_state.get("spese_fisse_metadata", {})
                rendered_voci = set()
                ordered_groups = _ordered_spesa_fissa_groups(SPESE["Fisse"], spese_meta)
                if MOBILE_VIEW:
                    mobile_cols = ["", ""]
                    for group_index, group_name in enumerate(ordered_groups):
                        group_items = [
                            (voce, importo)
                            for voce, importo in SPESE["Fisse"].items()
                            if spese_meta.get(voce, {}).get("Gruppo", _infer_spesa_fissa_gruppo(voce)) == group_name
                        ]
                        if not group_items:
                            continue
                        group_html = (
                            f'<div style="font-size:10px;text-transform:uppercase;letter-spacing:.7px;color:rgba(255,255,255,.46);margin:8px 0 3px;">{html.escape(str(group_name))}</div>'
                        )
                        for voce, importo in group_items:
                            categoria = spese_meta.get(voce, {}).get("Categoria", _infer_spesa_fissa_categoria(voce))
                            carta = spese_meta.get(voce, {}).get("Carta", _infer_spesa_fissa_carta(voce))
                            group_html += _spesa_fissa_row_html(voce, importo, categoria, carta)
                            rendered_voci.add(voce)
                        mobile_cols[group_index % 2] += group_html

                    altre_voci = [(voce, importo) for voce, importo in SPESE["Fisse"].items() if voce not in rendered_voci]
                    if altre_voci:
                        altre_html = '<div style="font-size:10px;text-transform:uppercase;letter-spacing:.7px;color:rgba(255,255,255,.46);margin:8px 0 3px;">Altre</div>'
                        for voce, importo in altre_voci:
                            categoria = spese_meta.get(voce, {}).get("Categoria", _infer_spesa_fissa_categoria(voce))
                            carta = spese_meta.get(voce, {}).get("Carta", _infer_spesa_fissa_carta(voce))
                            altre_html += _spesa_fissa_row_html(voce, importo, categoria, carta)
                        mobile_cols[1] += altre_html

                    st.markdown(
                        f'<div class="mobile-fixed-expenses-grid"><div class="mobile-fixed-expenses-col">{mobile_cols[0]}</div><div class="mobile-fixed-expenses-col">{mobile_cols[1]}</div></div>',
                        unsafe_allow_html=True
                    )
                else:
                    col_left, col_right = st.columns(LAYOUT_COLONNE["spese_fisse_lista"], gap="large")
                    group_columns = [col_left, col_right]
                    for group_index, group_name in enumerate(ordered_groups):
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
            if MOBILE_VIEW:
                fisse_donut_html = _mobile_donut_html(
                    "Spese fisse",
                    df_fisse["Categoria"].tolist(),
                    df_fisse["Importo"].tolist(),
                    df_fisse["Categoria"].map(lambda c: color_map.get(str(c), "#999999")).tolist()
                )
                entrate_donut_html = _mobile_donut_html(
                    "Entrate",
                    df_totale_clean["Component"].tolist(),
                    df_totale_clean["Value"].tolist(),
                    df_totale_clean["Component"].map({
                        "Spese Fisse": "#FF6464",
                        "Budget dopo spese fisse": "#fef3c7",
                        "Risparmio Stipendi": "#888888",
                    }).fillna("#94a3b8").tolist()
                )
                budget_donut_html = _mobile_donut_html(
                    "Budget",
                    df_utilizzare_clean["Component"].tolist(),
                    df_utilizzare_clean["Value"].tolist(),
                    df_utilizzare_clean["Component"].map({
                        "Spese Fisse": "#FF6961",
                        "Budget dopo spese fisse": "#fef3c7",
                    }).fillna("#94a3b8").tolist()
                )
                st.markdown(
                    f'<div class="mobile-three-donut-row">{entrate_donut_html}{budget_donut_html}{fisse_donut_html}</div>',
                    unsafe_allow_html=True
                )
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
            else:
                st.markdown("**💶 Distribuzione entrate e budget:**")
                st.altair_chart(chart_donut, use_container_width=True)

        # --- COLONNA 2: SPESE VARIABILI ---
    with col2:
        if MOBILE_VIEW:
            col2_left = st.container()
            col2_right = st.container()
        else:
            col2_left, col2_right = st.columns(LAYOUT_COLONNE["centrale_variabili_altre"], gap="large")
        with col2_left:
            if _mobile_show("Variabili"):
                st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)
                st.markdown('<div id="mobile-variabili" class="mobile-anchor"></div><div class="section-pill">💸 Spese Variabili</div>', unsafe_allow_html=True)
                st.subheader("Spese Variabili:")
    
                da_spendere = 0
                spese_quotidiane = 0
                spese_variabili_totali = SPESE["Variabili"]["Emergenze/Compleanni"] + SPESE["Variabili"]["Viaggi"] + SPESE["Variabili"]["Da spendere"] + SPESE["Variabili"]["Spese quotidiane"]
                df_spese_variabili_mobile = pd.DataFrame({
                    'Voce': ['Emergenze/Compleanni', 'Viaggi', 'Da spendere', 'Spese quotidiane'],
                    'Value': [
                        SPESE["Variabili"]["Emergenze/Compleanni"],
                        SPESE["Variabili"]["Viaggi"],
                        SPESE["Variabili"]["Da spendere"],
                        SPESE["Variabili"]["Spese quotidiane"]
                    ]
                })
                df_spese_variabili_mobile = df_spese_variabili_mobile[df_spese_variabili_mobile["Value"] > 0].copy()
    
                risparmio_stipendi = stipendio_originale - stipendio_scelto
                risparmio_da_spendere = 0
                risparmio_spese_quotidiane = 0

                spese_emergenze_viaggi = SPESE["Variabili"]["Emergenze/Compleanni"] + SPESE["Variabili"]["Viaggi"]
                risparmiabili_dopo_emergenze_viaggi = risparmiabili - spese_emergenze_viaggi

                percentuale_emergenze = percentuali_variabili.get("Emergenze/Compleanni", 0) * 100
                percentuale_viaggi = percentuali_variabili.get("Viaggi", 0) * 100
                pct_rimanente = (da_spendere_senza_limite * 100 / risparmiabili_dopo_emergenze_viaggi) if risparmiabili_dopo_emergenze_viaggi != 0 else 0
                da_spendere = min(da_spendere_senza_limite, limite_da_spendere)
                risparmio_da_spendere = da_spendere_senza_limite - da_spendere
                spese_quotidiane = min(spese_quotidiane_senza_limite, max_spese_quotidiane)
                risparmio_spese_quotidiane = spese_quotidiane_senza_limite - spese_quotidiane

                if MOBILE_VIEW:
                    variabili_list_html = (
                        '<div class="mobile-variabili-list">'
                        '<div style="font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:rgba(255,255,255,.46);margin:4px 0 4px;">Quote fisse</div>'
                        + _spesa_variabile_row_html("Emergenze/Compleanni", SPESE["Variabili"]["Emergenze/Compleanni"], "#4ADE80", f"{percentuale_emergenze:.2f}% del budget dopo spese fisse")
                        + _spesa_variabile_row_html("Viaggi", SPESE["Variabili"]["Viaggi"], "#166534", f"{percentuale_viaggi:.2f}% del budget dopo spese fisse")
                        + '<div style="height:1px;background:rgba(148,163,184,.22);margin:10px 0 8px;"></div>'
                        + '<div style="font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:rgba(255,255,255,.46);margin:0 0 4px;">Dopo le quote</div>'
                        + _spesa_variabile_row_html("Da spendere", SPESE["Variabili"]["Da spendere"], "#FACC15", f"{pct_rimanente:.2f}% del rimanente €{risparmiabili_dopo_emergenze_viaggi:.2f}, limite €{limite_da_spendere:.2f}")
                        + f'<div style="font-size:12px;color:rgba(255,255,255,.36);margin:-4px 0 7px 10px;">reale €{da_spendere_senza_limite:.2f} · risparmiati €{risparmio_da_spendere:.2f}</div>'
                        + _spesa_variabile_row_html("Spese quotidiane", SPESE["Variabili"]["Spese quotidiane"], "#FB923C", f"rimanente, con limite a €{max_spese_quotidiane:.2f}")
                        + f'<div style="font-size:12px;color:rgba(255,255,255,.36);margin:-4px 0 7px 10px;">reale €{spese_quotidiane_senza_limite:.2f} · risparmiati €{risparmio_spese_quotidiane:.2f}</div>'
                        '</div>'
                    )
                    variabili_donut_html = _mobile_donut_html(
                        "Distribuzione",
                        df_spese_variabili_mobile["Voce"].tolist(),
                        df_spese_variabili_mobile["Value"].tolist(),
                        df_spese_variabili_mobile["Voce"].map({
                            "Emergenze/Compleanni": "#4ADE80",
                            "Viaggi": "#166534",
                            "Da spendere": "#FACC15",
                            "Spese quotidiane": "#FB923C",
                        }).fillna("#94a3b8").tolist()
                    )
                    st.markdown(
                        f'<div class="mobile-variabili-grid">{variabili_list_html}<div class="mobile-variabili-chart">{variabili_donut_html}</div></div>',
                        unsafe_allow_html=True
                    )
                else:
                    variabili_quote_col, variabili_budget_col = st.columns(LAYOUT_COLONNE["variabili_quote_budget"], gap="large")
                    with variabili_quote_col:
                        st.markdown(
                            '<div style="font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:rgba(255,255,255,.46);margin:4px 0 4px;">Quote fisse</div>',
                            unsafe_allow_html=True
                        )
                        st.markdown(
                            _spesa_variabile_row_html(
                                "Emergenze/Compleanni",
                                SPESE["Variabili"]["Emergenze/Compleanni"],
                                "#4ADE80",
                                f"{percentuale_emergenze:.2f}% del budget dopo spese fisse"
                            ),
                            unsafe_allow_html=True
                        )
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
                        st.markdown(
                            _spesa_variabile_row_html(
                                "Da spendere",
                                SPESE["Variabili"]["Da spendere"],
                                "#FACC15",
                                f"{pct_rimanente:.2f}% del rimanente €{risparmiabili_dopo_emergenze_viaggi:.2f}, limite €{limite_da_spendere:.2f}"
                            ),
                            unsafe_allow_html=True
                        )
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
                        st.markdown(
                            f'<div style="font-size:12px;color:rgba(255,255,255,.36);margin:-4px 0 7px 10px;">reale €{spese_quotidiane_senza_limite:.2f} · risparmiati €{risparmio_spese_quotidiane:.2f}</div>',
                            unsafe_allow_html=True
                        )
    
    
                st.markdown('<div style="clear:both;height:10px;"></div>', unsafe_allow_html=True)
                if MOBILE_VIEW:
                    col_spese_variabili_1 = st.container()
                    col_spese_variabili_2 = st.container()
                else:
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
                        if MOBILE_VIEW:
                            pass
                        else:
                            donut_inner = 40
                            donut_outer = 70
                            donut_width = 200
                            donut_height = 220
                            chart_spese_variabili = alt.Chart(df_spese_variabili).mark_arc(
                                innerRadius=donut_inner, outerRadius=donut_outer
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
                                width=donut_width,
                                height=donut_height
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
            if _mobile_show("Entrate"):
                st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)
                st.markdown('<div id="mobile-entrate" class="mobile-anchor"></div><div class="section-pill">➕ Altre Entrate</div>', unsafe_allow_html=True)
                tab_altre_view, tab_altre_decisioni = st.tabs(["➕ Altre Entrate", "⚙️ Decisioni"])

                with tab_altre_decisioni:
                    altre_settings = ALTRE_ENTRATE.copy()
                    st.markdown(
                        '<div style="border-top: 1px solid rgba(148, 163, 184, .28); margin: 8px 0 18px;"></div>'
                        '<h4 style="text-align: center; margin: 0 0 18px;">Modifica entrate esistenti</h4>',
                        unsafe_allow_html=True,
                    )
                    editor_cols = st.columns(3 if MOBILE_VIEW else 2)
                    edited_altre = {}
                    altre_entrate_title_colors = {
                        "Macchina (Mamma)": "#D2B48C",
                        "2° Entr. dal mese prec.": "#D8BFD8",
                        "Altro": "#89CFF0",
                    }
                    if MOBILE_VIEW:
                        for editor_col in editor_cols:
                            with editor_col:
                                st.markdown('<span class="other-income-editor-marker"></span>', unsafe_allow_html=True)
                    for idx, (voce, importo) in enumerate(altre_settings.items()):
                        with editor_cols[idx % len(editor_cols)]:
                            title_color = altre_entrate_title_colors.get(voce, "#E5E7EB")
                            st.markdown(
                                f'<div style="font-size:15px;font-weight:800;color:{title_color};margin:0 0 6px;">{html.escape(str(voce))}</div>',
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
                    new_col1, new_col2 = st.columns(2 if MOBILE_VIEW else LAYOUT_COLONNE["form_nome_importo"])
                    with new_col1:
                        if MOBILE_VIEW:
                            st.markdown('<span class="other-income-new-marker"></span>', unsafe_allow_html=True)
                        nuova_voce = st.text_input("Nuova entrata", key="nuova_altra_entrata_nome")
                    with new_col2:
                        if MOBILE_VIEW:
                            st.markdown('<span class="other-income-new-marker"></span>', unsafe_allow_html=True)
                        nuovo_importo = st.number_input("Importo", min_value=0.0, value=0.0, step=10.0, key="nuova_altra_entrata_importo")
                    if nuova_voce.strip():
                        edited_altre[nuova_voce.strip()] = float(nuovo_importo)

                    elimina_entrata = st.selectbox("Entrata da eliminare", [""] + list(altre_settings.keys()), key="elimina_altra_entrata")
                    save_altre_col, delete_altre_col = st.columns(2)
                    with save_altre_col:
                        if MOBILE_VIEW:
                            st.markdown('<span class="other-income-actions-marker"></span>', unsafe_allow_html=True)
                        if st.button("💾 Salva altre entrate", use_container_width=True, key="save_altre_entrate"):
                            if save_altre_entrate_settings(edited_altre):
                                st.success("Altre entrate salvate")
                                st.rerun()
                            else:
                                st.error("Errore salvataggio altre entrate")
                    with delete_altre_col:
                        if MOBILE_VIEW:
                            st.markdown('<span class="other-income-actions-marker"></span>', unsafe_allow_html=True)
                        if st.button("🗑️ Elimina entrata", use_container_width=True, key="delete_altra_entrata", disabled=not bool(elimina_entrata)):
                            edited_altre.pop(elimina_entrata, None)
                            if save_altre_entrate_settings(edited_altre):
                                st.success("Entrata eliminata")
                                st.rerun()
                            else:
                                st.error("Errore eliminazione entrata")

                with tab_altre_view:
                    totale_altre = sum(ALTRE_ENTRATE.values())
                    _ae = f"€{totale_altre:.2f}"
                    totale_entrate_target = stipendio_originale / totale_entrate_target_oltre_lo_stipendio
                    altre_entrate_target = totale_entrate_target - stipendio_originale
                    progresso = totale_altre / altre_entrate_target if altre_entrate_target > 0 else 0
                    progresso = min(max(progresso, 0), 1.0)
                    percentuale_stip = stipendio_originale / totale_entrate_target * 100 if totale_entrate_target else 0
                    percentuale_altre_su_totale_altre = totale_altre / altre_entrate_target if altre_entrate_target else 0
                    _ae_ipot = f"{percentuale_altre_su_totale_altre * 100:.2f}"
                    altre_entrate_colori = {
                        "Macchina (Mamma)": "#E6C48C",
                        "Altro": "#89CFF0",
                        "2° Entr. dal mese prec.": "#D8BFD8",
                    }
                    df_altre_entrate = pd.DataFrame({
                        'Voce': list(ALTRE_ENTRATE.keys()),
                        'Value': list(ALTRE_ENTRATE.values())
                    })
                    df_altre_entrate = df_altre_entrate[df_altre_entrate["Value"] > 0].copy()
                    totale_entrate = df_altre_entrate["Value"].sum()
                    df_altre_entrate["Percentuale"] = (df_altre_entrate["Value"] / totale_entrate * 100).round(1) if totale_entrate != 0 else 0
                    palette = ['#E6C48C', '#D8BFD8', '#89CFF0', '#A78BFA', '#34d399', '#fb923c', '#60a5fa']

                    if MOBILE_VIEW:
                        html_altre = '<h3 style="margin:0 0 10px;">Altre Entrate:</h3>'
                        for voce, importo in ALTRE_ENTRATE.items():
                            colore = altre_entrate_colori.get(voce, "#34d399")
                            peso = (importo / totale_altre * 100) if totale_altre else 0
                            html_altre += _money_row_html(voce, importo, colore, triangolino_verde_BNL, f"{peso:.1f}% delle altre entrate")
                        html_obiettivo = f"""
                        <div class="mobile-objective-block" style="margin-top:0;">
                            <div class="mobile-objective-title">🎯 Obiettivo Entrate</div>
                            <div class="mobile-objective-metric">
                                <div class="mobile-objective-label">Entrate totali desiderate</div>
                                <div class="mobile-objective-value">€{totale_entrate_target:,.2f}</div>
                                <div style="font-size:10px;color:rgba(255,255,255,.42);">Stipendio = {percentuale_stip:.0f}% delle entrate totali</div>
                            </div>
                            <div class="mobile-objective-metric">
                                <div class="mobile-objective-label">Altre entrate target</div>
                                <div class="mobile-objective-value" style="color:#8fe28f;">€{altre_entrate_target:,.2f}</div>
                            </div>
                            <div class="mobile-progress"><div class="mobile-progress-fill" style="width:{progresso * 100:.1f}%;"></div></div>
                            <div style="font-size:10px;color:rgba(255,255,255,.44);">Attuale: €{totale_altre:,.2f} / €{altre_entrate_target:,.2f}</div>
                        </div>
                        """
                        html_totale_altre = f"""
                        <div class="kpi-card" style="margin-top:0;border-color:rgba(52,211,153,0.2);">
                            <div class="kpi-label">Totale Altre Entrate</div>
                            <div class="kpi-value" style="color:#77DD77;">{_ae}</div>
                            <div style="font-size:10px;color:rgba(255,255,255,0.34);margin-top:3px;">{_ae_ipot}% di Obiettivo Entrate</div>
                        </div>
                        """
                        if not df_altre_entrate.empty:
                            donut_altre_html = _mobile_donut_html(
                                "Distribuzione",
                                df_altre_entrate["Voce"].tolist(),
                                df_altre_entrate["Value"].tolist(),
                                palette[:len(df_altre_entrate)]
                            )
                        else:
                            donut_altre_html = '<div class="mobile-donut-card"><div class="mobile-donut-title">Distribuzione</div><div style="font-size:10px;color:rgba(255,255,255,.44);">Nessuna entrata.</div></div>'
                        st.markdown(
                            f'<div class="mobile-altre-top-grid"><div>{html_altre}</div><div>{html_obiettivo}</div></div>'
                            f'<div class="mobile-altre-bottom-grid"><div>{html_totale_altre}</div><div>{donut_altre_html}</div></div>',
                            unsafe_allow_html=True
                        )
                    else:
                        col_altre_entrate_sx, col_altre_entrate_dx = st.columns(LAYOUT_COLONNE["altre_entrate_obiettivo"], gap="medium")
                        with col_altre_entrate_sx:
                            st.subheader("Altre Entrate:")
                            for voce, importo in ALTRE_ENTRATE.items():
                                colore = altre_entrate_colori.get(voce, "#34d399")
                                peso = (importo / totale_altre * 100) if totale_altre else 0
                                st.markdown(
                                    _money_row_html(voce, importo, colore, triangolino_verde_BNL, f"{peso:.1f}% delle altre entrate"),
                                    unsafe_allow_html=True
                                )

                        with col_altre_entrate_dx:
                            st.markdown("### 🎯 Obiettivo Entrate")
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
                            if not df_altre_entrate.empty:
                                donut_inner = 32
                                donut_outer = 56
                                donut_width = 150
                                donut_height = 170
                                chart_altre_entrate = alt.Chart(df_altre_entrate).mark_arc(
                                    innerRadius=donut_inner, outerRadius=donut_outer
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
                                    width=donut_width,
                                    height=donut_height
                                ).configure_title(
                                    anchor='middle'
                                ).configure_view(
                                    strokeWidth=0,
                                    fill='transparent'
                                )
                                st.altair_chart(chart_altre_entrate, use_container_width=True)

            # Visualizzazione grafici
        if not MOBILE_VIEW:
            col_center_pill = st.columns(LAYOUT_COLONNE["titolo_dashboard"])[1]
            with col_center_pill:
                st.markdown('<div class="section-pill">🏠 Spese Fisse</div>',unsafe_allow_html=True)
                st.markdown("<br>", unsafe_allow_html=True)

        if MOBILE_VIEW:
            col1_1 = st.container()
            col1_2 = st.container()
            col_vuoto_b = st.container()
        else:
            col_vuoto_a, col1_1, col1_2, col_vuoto_b= st.columns(LAYOUT_COLONNE["dettaglio_spese_fisse"])
        with col1_1:
            if not MOBILE_VIEW:
                st.altair_chart(chart_fisse, use_container_width=True)
                st.markdown(f'<span style="font-size:10pt;">Totale spese fisse:</span> <span style="color:#f87171">{_sf}</span>', unsafe_allow_html=True)


#####################################################################################################################################################################################################################################################################################
            # 📊 Costruzione barra segmentata per CATEGORIE (come il donut)

            if not MOBILE_VIEW:
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
#####################################################################################################################################################################################################################################################################################


        with col1_2:
            if not MOBILE_VIEW:
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
    
        def _render_promemoria_block():
            st.markdown('<div id="mobile-promemoria" class="mobile-anchor"></div><div class="section-pill">📝 Note</div>', unsafe_allow_html=True)

            def _memo_card(label, value):
                raw_text = str(value or "").strip()
                if raw_text:
                    preview = raw_text if len(raw_text) <= 230 else raw_text[:227].rstrip() + "..."
                    preview_html = html.escape(preview).replace("\n", "<br>")
                else:
                    preview_html = '<span class="memo-card-empty">Nessuna nota scritta.</span>'
                return (
                    '<div class="memo-card">'
                    f'<div class="memo-card-title">{html.escape(label)}</div>'
                    f'<div class="memo-card-preview">{preview_html}</div>'
                    '</div>'
                )

            if MOBILE_VIEW:
                note_keys = ["nota1", "nota2", "nota3", "nota4"]
                st.markdown(
                    '<div class="mobile-notes-html-grid">'
                    + "".join(_memo_card(f"Nota {idx}", _nota_value(note_key)) for idx, note_key in enumerate(note_keys, start=1))
                    + '</div>',
                    unsafe_allow_html=True
                )
                note_values_map = {
                    "nota1": _nota_value("nota1"),
                    "nota2": _nota_value("nota2"),
                    "nota3": _nota_value("nota3"),
                    "nota4": _nota_value("nota4"),
                }
                with st.expander("Apri / modifica note", expanded=False):
                    for idx, note_key in enumerate(note_keys, start=1):
                        note_values_map[note_key] = st.text_area(
                            f"Nota {idx}",
                            value=note_values_map[note_key],
                            height=180,
                            key=f"{note_key}_text"
                        )
                nota1 = note_values_map["nota1"]
                nota2 = note_values_map["nota2"]
                nota3 = note_values_map["nota3"]
                nota4 = note_values_map["nota4"]
            else:
                n1, n2 = st.columns(2, gap="small")
                with n1:
                    st.markdown(_memo_card("Nota 1", _nota_value("nota1")), unsafe_allow_html=True)
                    with st.popover("Apri / modifica 1", use_container_width=True):
                        nota1 = st.text_area("Nota 1", value=_nota_value("nota1"), height=420, label_visibility="collapsed", key="nota1_text")
                with n2:
                    st.markdown(_memo_card("Nota 2", _nota_value("nota2")), unsafe_allow_html=True)
                    with st.popover("Apri / modifica 2", use_container_width=True):
                        nota2 = st.text_area("Nota 2", value=_nota_value("nota2"), height=420, label_visibility="collapsed", key="nota2_text")
                n3, n4 = st.columns(2, gap="small")
                with n3:
                    st.markdown(_memo_card("Nota 3", _nota_value("nota3")), unsafe_allow_html=True)
                    with st.popover("Apri / modifica 3", use_container_width=True):
                        nota3 = st.text_area("Nota 3", value=_nota_value("nota3"), height=420, label_visibility="collapsed", key="nota3_text")
                with n4:
                    st.markdown(_memo_card("Nota 4", _nota_value("nota4")), unsafe_allow_html=True)
                    with st.popover("Apri / modifica 4", use_container_width=True):
                        nota4 = st.text_area("Nota 4", value=_nota_value("nota4"), height=420, label_visibility="collapsed", key="nota4_text")

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
            

        with col_vuoto_b:
            if not MOBILE_VIEW:
                note_wrap_left, note_wrap, note_wrap_right = st.columns([0.02, 0.96, 0.02], gap="small")
                with note_wrap:
                    _render_promemoria_block()


    with col3:
        if MOBILE_VIEW:
            col3_left = st.container()
            col3_right = st.container()
        else:
            col3_left, col3_right = st.columns(LAYOUT_COLONNE["destra_risparmi_carte"], gap="medium")
        with col3_left:
            if _mobile_show("Risparmi"):
                if not MOBILE_VIEW:
                    st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)
                st.markdown('<div id="mobile-risparmi" class="mobile-anchor"></div><div class="section-pill">💰 Risparmi del Mese</div>', unsafe_allow_html=True)
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
                if not MOBILE_VIEW:
                    st.markdown(html_risparmi, unsafe_allow_html=True)
                    st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)
            
                if MOBILE_VIEW:
                    col_risparmi_1 = st.container()
                    col_risparmi_2 = st.container()
                else:
                    col_risparmi_1, col_risparmi_2 = st.columns(LAYOUT_COLONNE["risparmi_kpi_grafico"], gap="small")
                with col_risparmi_1:
                    risparmi_kpi_html = f"""
                    <div class="kpi-card" style="border-color:rgba(52,211,153,0.25);">
                        <div class="kpi-label">Tot. Risparmiato</div>
                        <div class="kpi-value" style="color:#34d399;">{kpi_val}</div>
                        <div style="font-size:11px;color:rgba(255,255,255,0.34);margin-top:3px;">{kpi_pct}% del budget mensile disponibile</div>
                        <div style="font-size:11px;color:rgba(255,255,255,0.34);margin-top:3px;">{kpi_pctot}% delle entrate mensili totali</div>
                    </div>
                    """
                    if not MOBILE_VIEW:
                        st.markdown(risparmi_kpi_html, unsafe_allow_html=True)

        
                    if not MOBILE_VIEW:
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
                    if MOBILE_VIEW:
                        if not df_savings.empty:
                            risparmi_donut_html = _mobile_donut_html(
                                "Distribuzione",
                                df_savings["Component"].tolist(),
                                df_savings["Value"].tolist(),
                                df_savings["Component"].map({
                                    "Da Stipendi": "#9ca3af",
                                    "Da Mese Prec.": "#60a5fa",
                                    "Da Spendere": "#fde047",
                                    "Quotidiane": "#FB923C",
                                }).fillna("#94a3b8").tolist()
                            )
                        else:
                            risparmi_donut_html = (
                                '<div class="mobile-donut-card">'
                                '<div class="mobile-donut-title">Distribuzione</div>'
                                '<div class="mobile-donut-body">'
                                '<div class="mobile-donut-ring" style="background:conic-gradient(rgba(148,163,184,.24) 0deg 360deg);">'
                                '<div class="mobile-donut-hole"></div>'
                                '</div>'
                                '<div class="mobile-donut-legend">'
                                '<div class="mobile-donut-legend-row"><span class="mobile-donut-dot" style="background:#9ca3af;"></span><span class="mobile-donut-label">Tutto a zero</span></div>'
                                '</div></div></div>'
                            )
                        st.markdown(
                            f'<div class="mobile-side-grid"><div>{html_risparmi}</div><div>{risparmi_donut_html}</div></div>',
                            unsafe_allow_html=True
                        )
                        st.markdown(risparmi_kpi_html, unsafe_allow_html=True)
                
                with col_risparmi_2:
                    if not df_savings.empty:
                        if MOBILE_VIEW:
                            pass
                        else:
                            donut_inner = 32
                            donut_outer = 56
                            donut_width = 150
                            donut_height = 170
                            chart_savings_arc = alt.Chart(df_savings).mark_arc(innerRadius=donut_inner, outerRadius=donut_outer).encode(
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
                                        offset=5
                                    )
                                ),
                                tooltip=[
                                    alt.Tooltip('Component:N', title='Risparmi'),
                                    alt.Tooltip('Value:Q', title='Totale (€)', format='.2f'),
                                    alt.Tooltip("Percentuale:Q", title="%", format=".1f")
                                ]
                            ).properties(
                                title="💰 Distribuzione Risparmi",
                                width=donut_width,
                                height=donut_height
                            ).configure_title(
                                anchor='middle'
                            ).configure_view(
                                strokeWidth=0,
                                fill='transparent'
                            )
                            chart_donut_Distribuzione_Risparmi = chart_savings_arc.resolve_scale(color='independent')
                            st.altair_chart(chart_donut_Distribuzione_Risparmi, use_container_width=True)
    


                            
        with col3_right:
            if _mobile_show("Carte"):
                if not MOBILE_VIEW:
                    st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)
                st.markdown('<div id="mobile-carte" class="mobile-anchor"></div><div class="section-pill">💳 Trasferimenti Carte</div>', unsafe_allow_html=True)
                st.subheader("Trasferimenti sulle Carte:")
        
                html_carte = ""
                for carta in ["ING", "Revolut", "BNL"]:
                    spese_carta = {voce: SPESE["Fisse"].get(voce, 0) + SPESE["Variabili"].get(voce, 0) 
                                   for voce in SPESE[carta]}
                    spese_carta = {voce: importo for voce, importo in spese_carta.items() if importo != 0}
                    if carta == "Revolut":
                        totale_carta = revolut_expenses  # Usa il valore modificato per Revolut
                        colore = "#89CFF0"  # Azzurro
                        testo = "trasferire"
                        somma_spese_programmate_immediate = SPESE["Fisse"]["Psicologo"] + SPESE["Fisse"]["Sport"] + SPESE["Fisse"]["Amara"] + SPESE["Fisse"]["Trasporti"] + SPESE["Fisse"]["Bollette"] + SPESE["Fisse"]["Beneficienza"] + SPESE["Fisse"]["Pulizia Casa"] + SPESE["Fisse"]["Disney+"] + SPESE["Fisse"]["Netflix"] + SPESE["Fisse"]["Spotify"]
                        spese_che_anticipo_per_un_giorno_di_disney_spotify=18
                        somma_valori = risparmi_mese_precedente - somma_spese_programmate_immediate - spese_che_anticipo_per_un_giorno_di_disney_spotify + totale_carta
                        row_html = _money_row_html(
                            f"Da {testo} su {carta}",
                            totale_carta,
                            colore,
                            _triangle_for_card(carta),
                            f"+ €{risparmi_mese_precedente:.2f} dai risparmi - (€{somma_spese_programmate_immediate:.2f} - €{spese_che_anticipo_per_un_giorno_di_disney_spotify:.2f}) -> vedrai €{somma_valori:.2f}"
                        )
                        if MOBILE_VIEW:
                            html_carte += row_html
                        else:
                            st.markdown(row_html, unsafe_allow_html=True)
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
                        row_html = _money_row_html(
                            f"Da {testo} su {carta}",
                            totale_carta,
                            colore,
                            _triangle_for_card(carta),
                            "totale delle spese previste su questa carta"
                        )
                        if MOBILE_VIEW:
                            html_carte += row_html
                        else:
                            st.markdown(row_html, unsafe_allow_html=True)
                totale_risparmiato_carte_html = _money_row_html(
                    f"Totale {testo2} su {carta}",
                    risparmi_mensili,
                    colore2,
                    _triangle_for_card(carta),
                    "quota da lasciare come risparmio"
                )
                if MOBILE_VIEW:
                    html_carte += totale_risparmiato_carte_html
                else:
                    st.markdown(totale_risparmiato_carte_html, unsafe_allow_html=True)
    
                # FIX 4: NEW "Carte" donut chart
                if not MOBILE_VIEW:
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
        
                    if MOBILE_VIEW:
                        carte_donut_html = _mobile_donut_html(
                                "Distribuzione carte",
                                df_carte["Carta"].tolist(),
                                df_carte["Totale"].tolist(),
                                ['#D2691E', '#89CFF0', '#2E7D32', '#66BB6A']
                        )
                        st.markdown(
                            f'<div class="mobile-side-grid"><div>{html_carte}</div><div>{carte_donut_html}</div></div>',
                            unsafe_allow_html=True
                        )
                    else:
                        donut_inner = 32
                        donut_outer = 56
                        donut_width = 150
                        donut_height = 170
                        carte_arc = alt.Chart(df_carte).mark_arc(innerRadius=donut_inner, outerRadius=donut_outer).encode(
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
                                offset=5
                            )
            
                        ),
                        tooltip=[
                            alt.Tooltip("Carta:N", title="Carta"),
                            alt.Tooltip("Totale:Q", title="Totale (€)", format=".2f"),
                            alt.Tooltip("Percentuale:Q", title="%", format=".1f")
                        ]
                        ).properties(
                            title="💳 Distribuzione Carte",
                            width=donut_width,
                            height=donut_height,
                        ).configure_title(
                            anchor='middle'
                        ).configure_view(
                            strokeWidth=0,
                            fill='transparent',
                        )    
            
                        chart_carte = carte_arc.resolve_scale(color='independent')
                        st.altair_chart(chart_carte, use_container_width=True)
                st.markdown('<div style="height:12px;"></div>', unsafe_allow_html=True)
        if MOBILE_VIEW and _mobile_show("Note"):
            _render_promemoria_block()
            st.markdown('<div style="height:18px;"></div>', unsafe_allow_html=True)
        if _mobile_show("Turni"):
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
        "Media Stipendio NO 13°/PDR": "Media Stipendi Ordinari (no spikes)",
        "Media Risparmi": "Media Risparmi Mese Precedente",
        "Risparmi": "Risparmi Mese Precedente"
    })

    bar_categories = ["Risparmi Mese Precedente", "Messi da parte Totali"]
    # FIX 1: Risparmi bar overlapping inside Messi da parte Totali
    # Use opacity layering - Messi da parte Totali as base, Risparmi overlaid
    bar_color_range = ["rgba(255, 165, 0, 0.5)", "#4CAF50"]

    line_categories = ["Stipendi", "Media Stipendi", "Media Stipendi Ordinari (no spikes)", "Media Risparmi Mese Precedente", "Media Messi da parte Totali"]
    line_color_range = ["#5792E8", "#f87171", "#fb923c", "#FFA040", "#90EE90"]
    # FIX 2: Month labels - use full month names diagonal like Bollette chart
    data_completa["Mese"] = pd.to_datetime(data_completa["Mese"], errors="coerce")
    data_completa["Mese_str"] = data_completa["Mese"].dt.strftime("%B %Y")
    ordine_mesi = data_completa.sort_values("Mese")["Mese_str"].unique().tolist()

    df_bar = data_completa[data_completa["Categoria"].isin(bar_categories)]
    df_line = data_completa[~data_completa["Categoria"].isin(bar_categories)]

    # FIX 1: Messi da parte Totali as base bar
    df_messi = df_bar[df_bar["Categoria"] == "Messi da parte Totali"]
    df_risparmi = df_bar[df_bar["Categoria"] == "Risparmi Mese Precedente"]

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


def render_grafico_stipendi_desktop_style(data_stipendi, height=430, years_back=3):
    if data_stipendi is None or data_stipendi.empty:
        st.info("Nessun dato disponibile. Aggiungi i dati nella sezione Gestisci mese.")
        return
    try:
        chart_data = data_stipendi.copy()
        chart_data["Mese"] = pd.to_datetime(chart_data["Mese"], errors="coerce")
        chart_data = chart_data.dropna(subset=["Mese"])
        current_month_start = pd.Timestamp(_now_italy().date()).to_period("M").to_timestamp()
        chart_start = current_month_start - pd.DateOffset(years=years_back)
        chart_data = chart_data[(chart_data["Mese"] >= chart_start) & (chart_data["Mese"] <= current_month_start)]
        if chart_data.empty:
            st.info("Nessun dato disponibile nel periodo selezionato.")
            return
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
            color="#5792E8", strokeWidth=2
        ).encode(
            x=x_axis,
            y=alt.Y("Stipendio:Q", title="Stipendi (€)", axis=alt.Axis(orient="left")),
        )
        point_stipendi = alt.Chart(chart_data).mark_point(
            color="#5792E8", size=42, filled=True
        ).encode(
            x=x_axis,
            y=alt.Y("Stipendio:Q"),
            tooltip=[alt.Tooltip("Mese_str:N", title="Mese"), alt.Tooltip("Stipendio:Q", title="Stipendi", format=",.2f")]
        )

        line_media_stip = alt.Chart(chart_data).mark_line(
            color="#f87171", strokeWidth=2, strokeDash=[6, 3], opacity=0.4
        ).encode(x=x_axis, y=alt.Y("Media Stipendio:Q"))
        point_media_stip = alt.Chart(chart_data).mark_point(
            color="#f87171", size=36, filled=True, opacity=0.85
        ).encode(
            x=x_axis,
            y=alt.Y("Media Stipendio:Q"),
            tooltip=[alt.Tooltip("Mese_str:N", title="Mese"), alt.Tooltip("Media Stipendio:Q", title="Media stipendi", format=",.2f")]
        )

        line_media_no13 = alt.Chart(chart_data).mark_line(
            color="#fb923c", strokeWidth=2, strokeDash=[3, 3]
        ).encode(x=x_axis, y=alt.Y("Media Stipendio NO 13°/PDR:Q"))
        point_media_no13 = alt.Chart(chart_data).mark_point(
            color="#fb923c", size=36, filled=True
        ).encode(
            x=x_axis,
            y=alt.Y("Media Stipendio NO 13°/PDR:Q"),
            tooltip=[alt.Tooltip("Mese_str:N", title="Mese"), alt.Tooltip("Media Stipendio NO 13°/PDR:Q", title="Media stipendi ordinari (no spikes)", format=",.2f")]
        )

        risparmi_stack = chart_data.melt(
            id_vars=["Mese_str", "Risparmi tooltip", "Messi da parte Totali"],
            value_vars=["Risparmi", "Extra messi da parte"],
            var_name="Componente risparmio",
            value_name="Valore"
        )
        risparmi_stack["Voce"] = risparmi_stack["Componente risparmio"].replace({
            "Risparmi": "Risparmi mese precedente",
            "Extra messi da parte": "Messi da parte"
        })

        bars_risparmi = alt.Chart(risparmi_stack).mark_bar(
            opacity=0.38, size=17
        ).encode(
            x=x_axis,
            y=alt.Y("Valore:Q", title="Risparmi / messi da parte (€)", axis=alt.Axis(orient="right"), stack="zero"),
            color=alt.Color(
                "Voce:N",
                scale=alt.Scale(domain=["Risparmi mese precedente", "Messi da parte"], range=["#EF9F27", "#1D9E75"]),
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
            color="#FFA040", strokeWidth=2, strokeDash=[4, 4], opacity=0.9
        ).encode(x=x_axis, y=alt.Y("Media Risparmi:Q"))
        point_media_risp = alt.Chart(chart_data).mark_point(
            color="#FFA040", size=36, filled=True
        ).encode(
            x=x_axis,
            y=alt.Y("Media Risparmi:Q"),
            tooltip=[alt.Tooltip("Mese_str:N", title="Mese"), alt.Tooltip("Media Risparmi:Q", title="Media risparmi mese precedente", format=",.2f")]
        )

        line_media_messi = alt.Chart(chart_data).mark_line(
            color="#90EE90", strokeWidth=2, strokeDash=[5, 5]
        ).encode(x=x_axis, y=alt.Y("Media Messi da parte Totali:Q"))
        point_media_messi = alt.Chart(chart_data).mark_point(
            color="#90EE90", size=36, filled=True
        ).encode(
            x=x_axis,
            y=alt.Y("Media Messi da parte Totali:Q"),
            tooltip=[alt.Tooltip("Mese_str:N", title="Mese"), alt.Tooltip("Media Messi da parte Totali:Q", title="Media messi da parte", format=",.2f")]
        )

        stipendi_chart = alt.layer(line_stipendi, point_stipendi, line_media_stip, point_media_stip, line_media_no13, point_media_no13)
        risparmi_chart = alt.layer(bars_risparmi, line_media_risp, point_media_risp, line_media_messi, point_media_messi)
        grafico_finale = alt.layer(risparmi_chart, stipendi_chart).properties(
            title="Storico Stipendi e Risparmi",
            height=height
        ).resolve_scale(y="independent")

        st.altair_chart(grafico_finale, use_container_width=True)
        st.markdown("""
        <div style="display:flex; flex-wrap:wrap; gap:16px; margin-top:8px; padding:10px 16px; 
                    background:rgba(255,255,255,0.04); border-radius:10px;">
            <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);"><span style="width:14px;height:14px;border-radius:3px;background:#1D9E75;opacity:0.7;display:inline-block;"></span>Messi da parte</span>
            <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);"><span style="width:14px;height:14px;border-radius:3px;background:#EF9F27;display:inline-block;"></span>Risparmi mese precedente</span>
            <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);"><span style="width:28px;height:3px;background:#5792E8;display:inline-block;border-radius:2px;"></span>Stipendi</span>
            <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);"><span style="width:28px;height:2px;border-top:2px dashed #f87171;display:inline-block;"></span>Media Stipendi</span>
            <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);"><span style="width:28px;height:2px;border-top:2px dashed #fb923c;display:inline-block;"></span>Media stipendi ordinari (no spikes)</span>
            <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);"><span style="width:28px;height:2px;border-top:2px dashed #FFA040;display:inline-block;"></span>Media risparmi mese precedente</span>
            <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);"><span style="width:28px;height:2px;border-top:2px dashed #90EE90;display:inline-block;"></span>Media Messi da parte</span>
        </div>
        """, unsafe_allow_html=True)
    except Exception as e:
        st.error(f"Errore nel grafico: {e}")


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
        y=alt.Y("lower:Q", title="Bollette (€)"),
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

    df_media = df_totali.sort_values("Mese").copy()
    df_media["Media mensile bollette"] = df_media["Valore"].expanding().mean()

    linea_media = alt.Chart(df_media).mark_line(
        strokeWidth=2,
        strokeDash=[5, 5],
        color="#FFA500",
        opacity=0.95,
    ).encode(
        x=alt.X("Mese_str:N", sort=order),
        y=alt.Y("Media mensile bollette:Q"),
        tooltip=[
            alt.Tooltip("Mese_str:N", title="Mese"),
            alt.Tooltip("Media mensile bollette:Q", title="Media mensile bollette", format=",.2f"),
        ],
    )

    punti_media = alt.Chart(df_media).mark_point(
        size=55,
        filled=True,
        color="#FFA500",
    ).encode(
        x=alt.X("Mese_str:N", sort=order),
        y=alt.Y("Media mensile bollette:Q"),
        tooltip=[
            alt.Tooltip("Mese_str:N", title="Mese"),
            alt.Tooltip("Media mensile bollette:Q", title="Media mensile bollette", format=",.2f"),
        ],
    )
    
    testo_totale = alt.Chart(df_totali).mark_text(
        align="center", baseline="bottom", dy=-5, fontSize=10, color="white"
    ).encode(
        x=alt.X("Mese_str:N", sort=order),
        y=alt.Y("Valore:Q"),
        text=alt.Text("Valore:Q", format=".2f")
    )
    
    linea_saldo = linea_saldo_unica + punti_saldo_color
    linea_media_mensile = linea_media + punti_media
    grafico_finale = alt.layer(barre, linea_saldo, linea_media_mensile, testo_totale)
    return grafico_finale
    
def crea_confronto_anno_su_anno_stipendi(data):
    if data.empty:
        return alt.Chart(pd.DataFrame({'Mese_str': [], 'Stipendio': [], 'Anno': []})).mark_line()
    df = data.copy()
    df["Mese"] = pd.to_datetime(df["Mese"], errors="coerce")
    df = df.dropna(subset=["Mese"])
    if df.empty:
        return alt.Chart(pd.DataFrame({'Mese_str': [], 'Stipendio': [], 'Anno': []})).mark_line()
    current_month_start = pd.Timestamp(_now_italy().date()).to_period("M").to_timestamp()
    chart_start = current_month_start - pd.DateOffset(years=3)
    df = df[(df["Mese"] >= chart_start) & (df["Mese"] <= current_month_start)]
    if df.empty:
        return alt.Chart(pd.DataFrame({'Mese_str': [], 'Stipendio': [], 'Anno': []})).mark_line()
    df["Anno"] = df["Mese"].dt.year.astype(str)
    df["Mese_str"] = df["Mese"].dt.strftime("%b")
    chart = alt.Chart(df).mark_line(point=True).encode(
        x=alt.X("Mese_str:N", title="Mese",
                sort=["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
                axis=alt.Axis(labelAngle=-45, labelFontSize=10)),
        y=alt.Y("Stipendio:Q", title="Stipendi (€)", aggregate="mean"),
        color=alt.Color("Anno:N", title="Anno"),
        tooltip=[alt.Tooltip("Anno:N", title="Anno"), alt.Tooltip("Mese_str:N", title="Mese"), alt.Tooltip("Stipendio:Q", title="Stipendio", aggregate="mean", format=".2f")]
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
    current_month_start = pd.Timestamp(_now_italy().date()).to_period("M").to_timestamp()
    chart_start = current_month_start - pd.DateOffset(years=3)
    df = df[(df["Mese"] >= chart_start) & (df["Mese"] <= current_month_start)]
    if df.empty:
        return alt.Chart(pd.DataFrame({'Mese_str': [], 'Totale_Bollette': [], 'Anno': []})).mark_line()
    if "Totale_Bollette" not in df.columns:
        df["Totale_Bollette"] = df["Elettricità"] + df["Gas"] + df["Acqua"] + df["Internet"] + df["Tari"]
    df["Anno"] = df["Mese"].dt.year.astype(str)
    df["Mese_str"] = df["Mese"].dt.strftime("%b")
    chart = alt.Chart(df).mark_line(point=True).encode(
        x=alt.X("Mese_str:N", title="Mese",
                sort=["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
                axis=alt.Axis(labelAngle=-45, labelFontSize=10)),
        y=alt.Y("Totale_Bollette:Q", title="Spesa Totale (€)"),
        color=alt.Color("Anno:N", title="Anno"),
        tooltip=[alt.Tooltip("Anno:N", title="Anno"), alt.Tooltip("Mese_str:N", title="Mese"), alt.Tooltip("Totale_Bollette:Q", title="Totale bollette", format=".2f")]
    ).properties(title="")
    return chart


BUDGET_BOLLETTE_HEADERS = ["Mese", "Budget mensile"]
BUDGET_BOLLETTE_WORKSHEET = "BudgetBollette"


def normalizza_budget_bollette(data):
    if data is None or data.empty:
        return pd.DataFrame(columns=BUDGET_BOLLETTE_HEADERS)
    df = data.copy()
    for col in BUDGET_BOLLETTE_HEADERS:
        if col not in df.columns:
            df[col] = ""
    df = df[BUDGET_BOLLETTE_HEADERS]
    df["Mese"] = pd.to_datetime(df["Mese"], errors="coerce").dt.to_period("M").dt.to_timestamp()
    df["Budget mensile"] = pd.to_numeric(df["Budget mensile"], errors="coerce")
    df = df.dropna(subset=["Mese", "Budget mensile"])
    return df.sort_values("Mese").drop_duplicates("Mese", keep="last").reset_index(drop=True)


def budget_bollette_per_mese(budget_df, mese):
    mese = pd.Timestamp(mese).to_period("M").to_timestamp()
    if budget_df is None or budget_df.empty:
        return float(decisione_budget_bollette_mensili)
    validi = budget_df[budget_df["Mese"] <= mese].sort_values("Mese")
    if validi.empty:
        return float(decisione_budget_bollette_mensili)
    return float(validi.iloc[-1]["Budget mensile"])


def salva_budget_bollette_da_mese(budget_df, mese, importo):
    mese = pd.Timestamp(mese).to_period("M").to_timestamp()
    if budget_df is None or budget_df.empty:
        budget_df = pd.DataFrame(columns=BUDGET_BOLLETTE_HEADERS)
    else:
        budget_df = normalizza_budget_bollette(budget_df)
    budget_df = budget_df[budget_df["Mese"] != mese].copy()
    budget_df = pd.concat([
        budget_df,
        pd.DataFrame([{"Mese": mese, "Budget mensile": float(importo)}])
    ], ignore_index=True)
    budget_df = budget_df.sort_values("Mese").reset_index(drop=True)
    return save_data_gsheets(BUDGET_BOLLETTE_WORKSHEET, BUDGET_BOLLETTE_HEADERS, budget_df)


def calcola_saldo_bollette(data, budget_df):
    saldo_iniziale = 0
    saldi = []
    budget_mensili = []
    data = data.sort_values("Mese").reset_index(drop=True).copy()
    for _, row in data.iterrows():
        budget_mese = budget_bollette_per_mese(budget_df, row["Mese"])
        totale = row.get("Elettricità", 0) + row.get("Gas", 0) + row.get("Acqua", 0) + row.get("Internet", 0) + row.get("Tari", 0)
        saldo = saldo_iniziale + budget_mese - totale
        saldi.append(saldo)
        budget_mensili.append(budget_mese)
        saldo_iniziale = saldo
    data["Saldo"] = saldi
    data["Budget bollette mensile"] = budget_mensili
    return data


if (not MOBILE_VIEW) or mobile_section == "Storico":
    #######################################
    # SEZIONE: Storico Stipendi e Risparmi
    #######################################

    st.markdown('<div id="mobile-stipendi" class="mobile-anchor"></div><div class="section-pill">📈 Storico Stipendi</div>', unsafe_allow_html=True)
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

    if MOBILE_VIEW:
        col_sx_stip = st.container()
        col_dx_stip_chart = st.container()
    else:
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
        if MOBILE_VIEW:
            st.caption("Valori salvati per il mese selezionato; se il mese non esiste viene creato al salvataggio.")
            col_input1, col_input2, col_input3 = st.columns(3)
            with col_input1:
                stipendio = st.number_input("Stipendio (€)", min_value=0.0, step=100.0, value=stipendio_val, key=f"stipendio_input_{selected_mese}")
            with col_input2:
                risparmi = st.number_input("Risparmi mese prec. (€)", min_value=0.0, step=100.0, value=risparmi_val, key=f"risparmi_input_{selected_mese}")
            with col_input3:
                messi_da_parte_mese_corrente = st.number_input("Messi da parte (€)", min_value=0.0, step=100.0, value=messi_da_parte_mese_corrente_val, key=f"messi_da_parte_input_{selected_mese}", help="Messi da parte totali / risparmio su BNL")
            col_btn1, col_btn2 = st.columns(2)
            with col_btn1:
                st.markdown('<span class="mobile-stipendi-save-marker"></span>', unsafe_allow_html=True)
                aggiungi_button = st.button("Salva mese", key="aggiorna_stipendi", use_container_width=True)
            with col_btn2:
                st.markdown('<span class="mobile-stipendi-delete-marker"></span>', unsafe_allow_html=True)
                elimina_button = st.button("Elimina mese", key="elimina_stipendi", use_container_width=True)
        else:
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

    data_stipendi = calcola_medie(data_stipendi, ["Stipendio", "Risparmi", "Messi da parte Totali"])

    if MOBILE_VIEW:
        st.markdown("---")
        render_grafico_stipendi_desktop_style(data_stipendi, height=430, years_back=1)
        _render_stipendi_kpi_cards(data_stipendi)

    if not MOBILE_VIEW:
        with col_dx_stip_chart:
            st.markdown("### Confronto Anno su Anno degli Stipendi")
            if not data_stipendi.empty:
                confronto_chart = crea_confronto_anno_su_anno_stipendi(data_stipendi)
                st.altair_chart(confronto_chart, use_container_width=True)
            else:
                st.info("Nessun dato disponibile ancora.")

    st.markdown("---")
    st.subheader("Dati Storici Stipendi/Risparmi")

    if MOBILE_VIEW:
        col_table = st.container()
        col_chart = st.container()
    else:
        col_table, col_chart = st.columns(LAYOUT_COLONNE["storico_tabella_grafico"])

    with col_table:
        df_stip = data_stipendi.copy()
        history_html = (
            _mobile_history_table_html(
                df_stip,
                ["Stipendio", "Risparmi", "Messi da parte Totali"],
                {
                    "Stipendio": "#5792E8",
                    "Risparmi": "#EF9F27",
                    "Messi da parte Totali": "#1D9E75",
                },
            )
            if MOBILE_VIEW
            else _history_table_html(
                df_stip,
                ["Stipendio", "Risparmi", "Messi da parte Totali"],
                {
                    "Stipendio": "#5792E8",
                    "Risparmi": "#EF9F27",
                    "Messi da parte Totali": "#1D9E75",
                },
            )
        )
        st.markdown(history_html, unsafe_allow_html=True)

        if not MOBILE_VIEW:
            _render_stipendi_kpi_cards(data_stipendi)

    with col_chart:
        if MOBILE_VIEW:
            pass
        elif data_stipendi is not None and not data_stipendi.empty:
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
                    color="#5792E8", strokeWidth=2
                ).encode(
                    x=x_axis,
                    y=alt.Y("Stipendio:Q", title="Stipendi (€)", axis=alt.Axis(orient="left")),
                )
                point_stipendi = alt.Chart(chart_data).mark_point(
                    color="#5792E8", size=42, filled=True
                ).encode(
                    x=x_axis,
                    y=alt.Y("Stipendio:Q"),
                    tooltip=[alt.Tooltip("Mese_str:N", title="Mese"), alt.Tooltip("Stipendio:Q", title="Stipendi", format=",.2f")]
                )

                line_media_stip = alt.Chart(chart_data).mark_line(
                    color="#f87171", strokeWidth=2, strokeDash=[6,3], opacity=0.4
                ).encode(
                    x=x_axis,
                    y=alt.Y("Media Stipendio:Q"),
                )
                point_media_stip = alt.Chart(chart_data).mark_point(
                    color="#f87171", size=36, filled=True, opacity=0.85
                ).encode(
                    x=x_axis,
                    y=alt.Y("Media Stipendio:Q"),
                    tooltip=[alt.Tooltip("Mese_str:N", title="Mese"), alt.Tooltip("Media Stipendio:Q", title="Media stipendi", format=",.2f")]
                )

                line_media_no13 = alt.Chart(chart_data).mark_line(
                    color="#fb923c", strokeWidth=2, strokeDash=[3,3]
                ).encode(
                    x=x_axis,
                    y=alt.Y("Media Stipendio NO 13°/PDR:Q"),
                )
                point_media_no13 = alt.Chart(chart_data).mark_point(
                    color="#fb923c", size=36, filled=True
                ).encode(
                    x=x_axis,
                    y=alt.Y("Media Stipendio NO 13°/PDR:Q"),
                    tooltip=[alt.Tooltip("Mese_str:N", title="Mese"), alt.Tooltip("Media Stipendio NO 13°/PDR:Q", title="Media stipendi ordinari (no spikes)", format=",.2f")]
                )

                risparmi_stack = chart_data.melt(
                    id_vars=["Mese_str", "Risparmi tooltip", "Messi da parte Totali"],
                    value_vars=["Risparmi", "Extra messi da parte"],
                    var_name="Componente risparmio",
                    value_name="Valore"
                )
                risparmi_stack["Voce"] = risparmi_stack["Componente risparmio"].replace({
                    "Risparmi": "Risparmi mese precedente",
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
                            domain=["Risparmi mese precedente", "Messi da parte"],
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
                    color="#FFA040", strokeWidth=2, strokeDash=[4,4], opacity=0.9
                ).encode(
                    x=x_axis,
                    y=alt.Y("Media Risparmi:Q"),
                )
                point_media_risp = alt.Chart(chart_data).mark_point(
                    color="#FFA040", size=36, filled=True
                ).encode(
                    x=x_axis,
                    y=alt.Y("Media Risparmi:Q"),
                    tooltip=[alt.Tooltip("Mese_str:N", title="Mese"), alt.Tooltip("Media Risparmi:Q", title="Media risparmi mese precedente", format=",.2f")]
                )

                line_media_messi = alt.Chart(chart_data).mark_line(
                    color="#90EE90", strokeWidth=2, strokeDash=[5,5]
                ).encode(
                    x=x_axis,
                    y=alt.Y("Media Messi da parte Totali:Q"),
                )
                point_media_messi = alt.Chart(chart_data).mark_point(
                    color="#90EE90", size=36, filled=True
                ).encode(
                    x=x_axis,
                    y=alt.Y("Media Messi da parte Totali:Q"),
                    tooltip=[alt.Tooltip("Mese_str:N", title="Mese"), alt.Tooltip("Media Messi da parte Totali:Q", title="Media messi da parte", format=",.2f")]
                )

                stipendi_chart = alt.layer(line_stipendi, point_stipendi, line_media_stip, point_media_stip, line_media_no13, point_media_no13)
                risparmi_chart = alt.layer(bars_risparmi, line_media_risp, point_media_risp, line_media_messi, point_media_messi)

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
                        <span style="width:14px;height:14px;border-radius:3px;background:#EF9F27;display:inline-block;"></span>Risparmi mese precedente
                    </span>
                    <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);">
                        <span style="width:28px;height:3px;background:#5792E8;display:inline-block;border-radius:2px;"></span>Stipendi
                    </span>
                    <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);">
                        <span style="width:28px;height:2px;border-top:2px dashed #f87171;display:inline-block;"></span>Media Stipendi
                    </span>
                    <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);">
                        <span style="width:28px;height:2px;border-top:2px dashed #fb923c;display:inline-block;"></span>Media stipendi ordinari (no spikes)
                    </span>
                    <span style="display:flex;align-items:center;gap:6px;font-size:12px;color:rgba(255,255,255,0.7);">
                        <span style="width:28px;height:2px;border-top:2px dashed #FFA040;display:inline-block;"></span>Media risparmi mese precedente
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

    if MOBILE_VIEW:
        st.markdown("---")
        st.markdown("### Confronto Anno su Anno degli Stipendi")
        if not data_stipendi.empty:
            confronto_chart = crea_confronto_anno_su_anno_stipendi(data_stipendi).properties(height=320)
            st.altair_chart(confronto_chart, use_container_width=True)
        else:
            st.info("Nessun dato disponibile ancora.")

    st.markdown('<hr style="width: 100%; height:1px;border-width:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.18),transparent);">', unsafe_allow_html=True)


if (not MOBILE_VIEW) or mobile_section == "Bollette":
    ############################
    # SEZIONE: Storico Bollette
    #############################

    st.markdown('<div id="mobile-bollette" class="mobile-anchor"></div><div class="section-pill">🧾 Storico Bollette</div>', unsafe_allow_html=True)
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

    budget_bollette_df = normalizza_budget_bollette(
        load_data_gsheets(BUDGET_BOLLETTE_WORKSHEET, BUDGET_BOLLETTE_HEADERS)
    )

    if MOBILE_VIEW:
        col_sx_bol = st.container()
        col_dx_bol_chart = st.container()
    else:
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

            if MOBILE_VIEW:
                col_bol_input1, col_bol_input2, col_bol_input3 = st.columns(3)
                with col_bol_input1:
                    elettricita = st.number_input("Elettricità (€)", min_value=0.0, step=10.0, value=elettricita_val, key=f"elettricita_input_{selected_mese_bol}")
                with col_bol_input2:
                    gas = st.number_input("Gas (€)", min_value=0.0, step=10.0, value=gas_val, key=f"gas_input_{selected_mese_bol}")
                with col_bol_input3:
                    acqua = st.number_input("Acqua (€)", min_value=0.0, step=10.0, value=acqua_val, key=f"acqua_input_{selected_mese_bol}")

                col_bol_input4, col_bol_input5 = st.columns(2)
                with col_bol_input4:
                    internet = st.number_input("Internet (€)", min_value=0.0, step=10.0, value=internet_val, key=f"internet_input_{selected_mese_bol}")
                with col_bol_input5:
                    tari = st.number_input("Tari (€)", min_value=0.0, step=10.0, value=tari_val, key=f"tari_input_{selected_mese_bol}")

                col_bol_btn1, col_bol_btn2 = st.columns(2)
                with col_bol_btn1:
                    st.markdown('<span class="mobile-bollette-save-marker"></span>', unsafe_allow_html=True)
                    aggiungi_bollette = st.button("Salva mese", key="aggiorna_bollette", use_container_width=True)
                with col_bol_btn2:
                    st.markdown('<span class="mobile-bollette-delete-marker"></span>', unsafe_allow_html=True)
                    elimina_bollette = st.button("Elimina mese", key="elimina_bollette", use_container_width=True)
            else:
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

            st.markdown('<div style="height:10px;"></div>', unsafe_allow_html=True)
            budget_bollette_corrente_mese = budget_bollette_per_mese(budget_bollette_df, mese_dt_bol)
            with st.expander("Budget mensile bollette", expanded=False):
                nuovo_budget_bollette = st.number_input(
                    "Importo messo da parte al mese",
                    min_value=0.0,
                    value=float(budget_bollette_corrente_mese),
                    step=10.0,
                    key=f"budget_bollette_input_{selected_mese_bol}",
                    help="Vale dal mese selezionato in poi; i mesi precedenti restano calcolati con il budget precedente."
                )
                if st.button("💾 Salva budget bollette da questo mese", use_container_width=True, key=f"save_budget_bollette_{selected_mese_bol}"):
                    if salva_budget_bollette_da_mese(budget_bollette_df, mese_dt_bol, nuovo_budget_bollette):
                        st.success("Budget bollette salvato")
                        st.rerun()
                    else:
                        st.error("Errore salvataggio budget bollette")

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

    if not MOBILE_VIEW:
        with col_dx_bol_chart:
            st.markdown("### Confronto Anno su Anno delle Bollette")
            if not data_bollette.empty:
                confronto_bollette_chart = crea_confronto_anno_su_anno_bollette(data_bollette)
                st.altair_chart(confronto_bollette_chart, use_container_width=True)
            else:
                st.info("Nessun dato disponibile ancora.")

    stats_bollette = calcola_statistiche(data_bollette, ["Elettricità", "Gas", "Acqua", "Internet", "Tari"])
    data_bollette = calcola_saldo_bollette(data_bollette, budget_bollette_df)
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
    chart_years_bol = 1 if MOBILE_VIEW else 3
    chart_start_bol = current_month_start_bol - pd.DateOffset(years=chart_years_bol)
    data_completa_bollette = data_completa_bollette[
        (data_completa_bollette["Mese"] >= chart_start_bol)
        & (data_completa_bollette["Mese"] <= current_month_start_bol)
    ].copy()
    data_completa_bollette["Mese_str"] = data_completa_bollette["Mese"].dt.strftime("%b %Y")
    ordine = data_completa_bollette.sort_values("Mese")["Mese_str"].unique().tolist()

    total_bollette = (stats_bollette["Elettricità"]["somma"] + stats_bollette["Gas"]["somma"] +
                    stats_bollette["Acqua"]["somma"] + stats_bollette["Internet"]["somma"] + stats_bollette["Tari"]["somma"])
    n_mesi = data_bollette["Mese"].nunique() if data_bollette["Mese"].nunique() > 0 else 1
    media_annua = total_bollette / n_mesi
    budget_bollette_attuale = budget_bollette_per_mese(budget_bollette_df, current_month_start_bol)
    saldo_bollette_attuale = float(data_bollette["Saldo"].iloc[-1]) if not data_bollette.empty and "Saldo" in data_bollette.columns else 0.0
    saldo_bollette_color = "#77DD77" if saldo_bollette_attuale >= 0 else "#FF6961"

    if MOBILE_VIEW:
        st.markdown("---")
        st.markdown("### Storico Bollette")
        if not data_completa_bollette.empty:
            st.altair_chart(crea_grafico_bollette_linea_continua(data_completa_bollette, ordine).properties(height=420), use_container_width=True)
            st.markdown(f"""
            <div style="display:flex;gap:18px;align-items:flex-start;flex-wrap:wrap;margin-top:8px;">
                <div><b>Media mensile bollette:</b> <span style="color:#FFA500;">{media_annua:,.2f} €</span></div>
                <div style="line-height:1.55;">
                    <div><b>Budget mensile bollette:</b> <span style="color:#a8b0bd;">{budget_bollette_attuale:,.2f} €</span></div>
                    <div><b>Saldo bollette:</b> <span style="color:{saldo_bollette_color};">{saldo_bollette_attuale:,.2f} €</span></div>
                </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.info("Nessun dato disponibile ancora.")

        st.markdown(
            '<div style="height:18px;margin:12px 0 16px;border-top:1px solid rgba(255,255,255,.08);"></div>',
            unsafe_allow_html=True
        )
        st.markdown(f"""
        <div class="mobile-bollette-kpi-grid">
            <div class="kpi-card">
                <div class="kpi-label">Somma Elettricità</div>
                <div class="kpi-value" style="color:#84B6F4;font-size:16px;">{stats_bollette['Elettricità']['somma']:,.2f} €</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Somma Gas</div>
                <div class="kpi-value" style="color:#FF6961;font-size:16px;">{stats_bollette['Gas']['somma']:,.2f} €</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Somma Acqua</div>
                <div class="kpi-value" style="color:#96DED1;font-size:16px;">{stats_bollette['Acqua']['somma']:,.2f} €</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Somma Tari</div>
                <div class="kpi-value" style="color:#C19A6B;font-size:16px;">{stats_bollette['Tari']['somma']:,.2f} €</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Somma Internet</div>
                <div class="kpi-value" style="color:#FFF5A1;font-size:16px;">{stats_bollette['Internet']['somma']:,.2f} €</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("Dati Storici Bollette")
    if MOBILE_VIEW:
        col_bol_table = st.container()
        col_bol_chart = st.container()
    else:
        col_bol_table, col_bol_chart = st.columns(LAYOUT_COLONNE["bollette_tabella_grafico"])
    with col_bol_table:
        df_bol = data_bollette.copy()
        bollette_colors = {
            "Elettricità": "#84B6F4",
            "Gas": "#FF6961",
            "Acqua": "#96DED1",
            "Internet": "#FFF5A1",
            "Tari": "#C19A6B",
        }
        st.markdown(
            (_mobile_history_table_html if MOBILE_VIEW else _history_table_html)(
                df_bol,
                ["Elettricità", "Gas", "Acqua", "Internet", "Tari"],
                bollette_colors,
            ),
            unsafe_allow_html=True,
        )
    
        if not MOBILE_VIEW:
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
    
    with col_bol_chart:
        if not MOBILE_VIEW:
            st.altair_chart(crea_grafico_bollette_linea_continua(data_completa_bollette, ordine).properties(height=500), use_container_width=True)

            st.markdown(f"""
            <div style="display:inline-grid;grid-template-columns:max-content max-content;gap:34px;align-items:center;margin-top:8px;">
                <div><b>Media mensile bollette:</b> <span style="color:#FFA500;">{media_annua:,.2f} €</span></div>
                <div style="line-height:1.55;">
                    <div><b>Budget mensile bollette:</b> <span style="color:#a8b0bd;">{budget_bollette_attuale:,.2f} €</span></div>
                    <div><b>Saldo bollette:</b> <span style="color:{saldo_bollette_color};">{saldo_bollette_attuale:,.2f} €</span></div>
                </div>
            </div>
            """, unsafe_allow_html=True)

    if MOBILE_VIEW:
        st.markdown("---")
        st.markdown("### Confronto Anno su Anno delle Bollette")
        if not data_bollette.empty:
            st.altair_chart(crea_confronto_anno_su_anno_bollette(data_bollette).properties(height=320), use_container_width=True)
        else:
            st.info("Nessun dato disponibile ancora.")

    st.markdown('<hr style="width: 100%; height:1px;border-width:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.18),transparent);">', unsafe_allow_html=True)
