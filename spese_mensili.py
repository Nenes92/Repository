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
import re
import hashlib
import urllib.request
from payroll_engine import (
    DEFAULT_RULES as PAYROLL_V2_DEFAULTS,
    Shift as PayrollShift,
    VariableBreakdown,
    add_months as add_payroll_months,
    calculate_month_variables,
    calibrate as calibrate_payroll,
    estimate_live_net_accrual,
    estimate_payslip,
    migrate_rules as migrate_payroll_rules,
)
from turni_excel_import import merge_turni_history, read_turni_excel
from payslip_parser import (
    extract_payslip_month,
    extract_pdf_text,
    find_adjustment_candidates,
    label_signature,
)
from payslip_drive import pending_drive_files, safe_pdf_filename, unique_pdf_filename
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

# Google Sheets imports
try:
    import gspread
    from google.oauth2.credentials import Credentials as OAuthCredentials
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
    GSHEETS_AVAILABLE = True
except ImportError:
    GSHEETS_AVAILABLE = False

# ─── GOOGLE SHEETS CONFIG ───────────────────────────────────────────────────
SHEET_URL = st.secrets["SHEET_URL"]
# Cartella Drive dedicata ai cedolini: l'icona in alto apre la cartella Google
# nativa, dove è possibile caricare, consultare e scaricare tutti i PDF.
CEDOLINI_DRIVE_URL = "https://drive.google.com/drive/folders/1Uq9SGfCKy5vNJN2FOw4imrtbI32nHdvj"
CEDOLINI_DRIVE_FOLDER_ID = "1Uq9SGfCKy5vNJN2FOw4imrtbI32nHdvj"

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

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


@st.cache_resource
def get_google_credentials():
    if not GSHEETS_AVAILABLE:
        return None
    try:
        return Credentials.from_service_account_info(CREDENTIALS_INFO, scopes=SCOPES)
    except Exception:
        return None

@st.cache_resource
def get_gsheet_client():
    creds = get_google_credentials()
    if creds is None:
        return None
    try:
        client = gspread.authorize(creds)
        return client
    except Exception:
        return None


@st.cache_resource
def get_drive_service():
    creds = None
    try:
        oauth_config = st.secrets.get("google_drive_oauth", {})
        required = ("client_id", "client_secret", "refresh_token", "token_uri")
        if all(str(oauth_config.get(key, "") or "").strip() for key in required):
            creds = OAuthCredentials(
                token=None,
                refresh_token=str(oauth_config["refresh_token"]),
                token_uri=str(oauth_config["token_uri"]),
                client_id=str(oauth_config["client_id"]),
                client_secret=str(oauth_config["client_secret"]),
                scopes=["https://www.googleapis.com/auth/drive"],
            )
    except Exception:
        creds = None
    if creds is None:
        creds = get_google_credentials()
    if creds is None:
        return None
    try:
        return build("drive", "v3", credentials=creds, cache_discovery=False)
    except Exception:
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


def _ensure_worksheet_headers(worksheet, expected_headers):
    """Append missing columns while preserving every existing header/value."""
    try:
        current_headers = worksheet.row_values(1)
        if not current_headers:
            worksheet.update(values=[expected_headers], range_name="A1")
            return list(expected_headers)
        merged_headers = list(current_headers)
        for header in expected_headers:
            if header not in merged_headers:
                merged_headers.append(header)
        if merged_headers != current_headers:
            worksheet.update(values=[merged_headers], range_name="A1")
        return merged_headers
    except TypeError:
        # Compatibilità con versioni precedenti di gspread.
        current_headers = worksheet.row_values(1)
        merged_headers = list(current_headers)
        for header in expected_headers:
            if header not in merged_headers:
                merged_headers.append(header)
        if merged_headers != current_headers:
            worksheet.update("A1", [merged_headers])
        return merged_headers


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
        _ensure_worksheet_headers(worksheet, headers)
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
    # Le quote Google per letture e scritture sono indipendenti: una lettura in
    # backoff non deve impedire un salvataggio che può ancora andare a buon fine.
    # Gli effettivi errori di scrittura restano gestiti dal blocco try/except.
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
.main-view-switch a.sheet-link {
    min-width: 24px;
    justify-content: center;
    padding: 4px 7px;
    color: #86efac !important;
}
.main-view-switch a.sheet-link:hover {
    background: rgba(134,239,172,.14);
}
.main-view-switch a.payslip-link {
    min-width: 24px;
    justify-content: center;
    padding: 4px 7px;
    color: #fbbf24 !important;
}
.main-view-switch a.payslip-link:hover {
    background: rgba(251,191,36,.14);
}
@media (max-width: 767px) {
    div[data-testid="stHorizontalBlock"]:has(.carte-summary-mobile-marker) {
        display: grid !important;
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
        gap: 8px !important;
        width: 100% !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.carte-summary-mobile-marker) > div[data-testid="column"] {
        width: auto !important;
        min-width: 0 !important;
        max-width: 100% !important;
        flex: initial !important;
    }
}
</style>
""", unsafe_allow_html=True)

VISTA_APP = _default_view
_desktop_active = "active" if VISTA_APP == "Desktop" else ""
_mobile_active = "active" if VISTA_APP == "Telefono" else ""
_sheet_url = html.escape(SHEET_URL, quote=True)
_cedolini_drive_url = html.escape(CEDOLINI_DRIVE_URL, quote=True)
st.markdown(
    f'<div class="main-view-switch">'
    f'<a class="{_desktop_active}" href="?view=desktop" target="_self">Desktop</a>'
    f'<a class="{_mobile_active}" href="?view=mobile" target="_self">Telefono</a>'
    f'<a class="sheet-link" href="{_sheet_url}" target="_blank" rel="noopener noreferrer" title="Apri il foglio di riferimento" aria-label="Apri il foglio di riferimento">📊</a>'
    f'<a class="payslip-link" href="{_cedolini_drive_url}" target="_blank" rel="noopener noreferrer" title="Apri archivio cedolini PDF" aria-label="Apri archivio cedolini PDF">📄</a>'
    f'</div>',
    unsafe_allow_html=True
)

MOBILE_VIEW = VISTA_APP == "Telefono"
MOBILE_SECTIONS = ["Panoramica", "Spese", "Variabili", "Entrate", "Risparmi", "Carte", "Note", "Turni", "Storico", "Bollette"]

def _query_param_first(name):
    value = st.query_params.get(name)
    if isinstance(value, list):
        return value[0] if value else None
    return value

def _query_param_float(name):
    raw = _query_param_first(name)
    if raw is None:
        return None
    text = str(raw).strip().replace("€", "").replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return max(0.0, float(text))
    except ValueError:
        return None

def _float_default(value, fallback=0.0):
    try:
        if pd.isna(value):
            return float(fallback)
        text = str(value).strip().replace("€", "").replace(" ", "")
        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            text = text.replace(".", "").replace(",", ".")
        return float(text)
    except Exception:
        return float(fallback)

STIPENDI_HEADERS = ["Mese", "Stipendio", "Quota stipendio scelta", "Risparmi", "Messi da parte Totali"]


def _latest_salary_defaults_from_history():
    fallback_stipendio = 2350.0
    fallback_quota = 2350.0
    fallback_risparmi = 0.0
    try:
        data = load_data_gsheets("Stipendi", STIPENDI_HEADERS)
        if data is None or data.empty or "Mese" not in data.columns:
            return fallback_stipendio, fallback_quota, fallback_risparmi

        storico = data.copy()
        storico["_mese_dt"] = pd.to_datetime(storico["Mese"], errors="coerce")
        storico = storico.dropna(subset=["_mese_dt"])
        if storico.empty:
            return fallback_stipendio, fallback_quota, fallback_risparmi

        storico["_stipendio_default"] = storico.get(
            "Stipendio", pd.Series([0] * len(storico), index=storico.index)
        ).apply(lambda value: _float_default(value, 0.0))
        storico["_risparmi_default"] = storico.get(
            "Risparmi", pd.Series([0] * len(storico), index=storico.index)
        ).apply(lambda value: _float_default(value, 0.0))
        storico = storico[storico["_stipendio_default"] > 0].sort_values("_mese_dt")
        if storico.empty:
            return fallback_stipendio, fallback_quota, fallback_risparmi

        ultimo_mese = storico.iloc[-1]
        stipendio = float(ultimo_mese["_stipendio_default"])
        quota_col = next(
            (
                col
                for col in (
                    "Quota stipendio scelta",
                    "Quota scelta",
                    "Budget da stipendio",
                    "Quota Stipendio",
                    "Quota",
                )
                if col in storico.columns
            ),
            None,
        )
        # La quota scelta è indipendente dallo stipendio percepito: se lo
        # storico non la contiene, manteniamo il valore iniziale previsto.
        quota = _float_default(ultimo_mese.get(quota_col), fallback_quota) if quota_col else fallback_quota
        risparmi = float(ultimo_mese["_risparmi_default"])
        return stipendio, min(quota, stipendio), risparmi
    except Exception:
        return fallback_stipendio, fallback_quota, fallback_risparmi


def salva_stipendio_corrente(stipendio, quota_scelta, risparmi_precedenti, messi_da_parte):
    """Crea o aggiorna nello storico il riepilogo del mese corrente."""
    mese_corrente = pd.Timestamp(_now_italy().date()).to_period("M").to_timestamp()
    data = load_data_gsheets("Stipendi", STIPENDI_HEADERS, force_reload=True)
    if data is None or data.empty:
        data = pd.DataFrame(columns=STIPENDI_HEADERS)
    else:
        data = data.copy()
        for col in STIPENDI_HEADERS:
            if col not in data.columns:
                data[col] = ""
        data["Mese"] = pd.to_datetime(data["Mese"], errors="coerce").dt.to_period("M").dt.to_timestamp()
        data = data.dropna(subset=["Mese"])

    nuovo_record = {
        "Mese": mese_corrente,
        "Stipendio": float(stipendio),
        "Quota stipendio scelta": float(quota_scelta),
        "Risparmi": float(risparmi_precedenti),
        "Messi da parte Totali": float(messi_da_parte),
    }
    data = data[data["Mese"] != mese_corrente]
    data = pd.concat([data, pd.DataFrame([nuovo_record])], ignore_index=True)
    data = data[STIPENDI_HEADERS].sort_values("Mese").reset_index(drop=True)
    return save_data_gsheets("Stipendi", STIPENDI_HEADERS, data)


DEFAULT_STIPENDIO_PERCEPITO, DEFAULT_QUOTA_STIPENDIO, DEFAULT_RISPARMI_MESE_PRECEDENTE = (
    _latest_salary_defaults_from_history()
)

if MOBILE_VIEW:
    mobile_section_param = st.query_params.get("mobile_section")
    if isinstance(mobile_section_param, list):
        mobile_section_param = mobile_section_param[0] if mobile_section_param else None
    if mobile_section_param == "Promemoria":
        mobile_section_param = "Note"
    if mobile_section_param in MOBILE_SECTIONS:
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
    _mobile_salary_query_values = {
        "mobile_salary_stipendio_percepito_value": _query_param_float("stip"),
        "mobile_salary_budget_da_stipendio_value": _query_param_float("quota"),
        "mobile_salary_risparmi_mese_precedente_value": _query_param_float("risp"),
    }
    _mobile_salary_query_signature = (
        mobile_section_param,
        _mobile_salary_query_values["mobile_salary_stipendio_percepito_value"],
        _mobile_salary_query_values["mobile_salary_budget_da_stipendio_value"],
        _mobile_salary_query_values["mobile_salary_risparmi_mese_precedente_value"],
    )
    if (
        any(value is not None for value in _mobile_salary_query_values.values())
        and st.session_state.get("_mobile_salary_query_signature_applied") != _mobile_salary_query_signature
    ):
        stipendio_query = _mobile_salary_query_values["mobile_salary_stipendio_percepito_value"]
        quota_query = _mobile_salary_query_values["mobile_salary_budget_da_stipendio_value"]
        risp_query = _mobile_salary_query_values["mobile_salary_risparmi_mese_precedente_value"]
        if stipendio_query is not None:
            st.session_state["mobile_salary_stipendio_percepito_value"] = stipendio_query
        if quota_query is not None:
            quota_max = st.session_state.get(
                "mobile_salary_stipendio_percepito_value",
                stipendio_query or DEFAULT_STIPENDIO_PERCEPITO,
            )
            st.session_state["mobile_salary_budget_da_stipendio_value"] = min(quota_query, float(quota_max))
        if risp_query is not None:
            st.session_state["mobile_salary_risparmi_mese_precedente_value"] = risp_query
        st.session_state["_mobile_salary_query_signature_applied"] = _mobile_salary_query_signature
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
    justify-self: center;
    font-size: 10px;
    color: rgba(255,255,255,.42);
    margin-top: 0;
    margin-bottom: 10px;
    line-height: 1.1;
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
    justify-items: center;
    align-items: start;
    margin-top: 0;
    margin-bottom: 10px;
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
        grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
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
    .mobile-card-caption.variabili { --section-color:#f59e0b; }
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
    .mobile-nav a.variabili { --section-color:#f59e0b; }
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
    .mobile-home-recap {
        display: flex;
        flex-direction: column;
        gap: 9px;
        margin: 18px 0 4px;
    }
    .mobile-home-recap-row {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 8px;
        align-items: stretch;
    }
    .mobile-home-recap-pair {
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 6px;
        align-items: stretch;
    }
    .mobile-home-recap-card {
        min-width: 0;
        min-height: 52px;
        padding: 8px 10px;
        border-radius: 13px;
        background:
            linear-gradient(135deg, color-mix(in srgb, var(--recap-color) 17%, transparent), rgba(255,255,255,.035));
        border: 0.5px solid color-mix(in srgb, var(--recap-color) 38%, rgba(255,255,255,.12));
        border-left: 3px solid var(--recap-color);
        box-shadow: 0 10px 22px rgba(0,0,0,.16);
        overflow: hidden;
    }
    .mobile-home-recap-card.wide {
        grid-column: span 2;
    }
    .mobile-home-carte-row {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
        align-items: stretch;
    }
    .mobile-home-carte-stack {
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 6px;
    }
    .mobile-home-carte-list {
        min-width: 0;
        padding: 9px 10px;
        border-radius: 13px;
        background:
            linear-gradient(135deg, color-mix(in srgb, #7dd3fc 15%, transparent), rgba(255,255,255,.035));
        border: 0.5px solid color-mix(in srgb, #7dd3fc 38%, rgba(255,255,255,.12));
        border-left: 3px solid #7dd3fc;
        box-shadow: 0 10px 22px rgba(0,0,0,.16);
    }
    .mobile-home-carte-title {
        color: rgba(255,255,255,.90);
        font-size: 10px;
        font-weight: 900;
        margin-bottom: 6px;
    }
    .mobile-home-carte-item {
        display: flex;
        justify-content: space-between;
        gap: 8px;
        color: var(--carte-color, rgba(255,255,255,.70));
        font-size: 8.5px;
        line-height: 1.45;
    }
    .mobile-home-carte-item strong {
        color: inherit;
        font-family: "DM Mono", monospace;
        font-size: 8.5px;
        white-space: nowrap;
    }
    .mobile-home-turni-row {
        width: 100%;
        margin: 0;
        display: grid;
        grid-template-columns: minmax(0, 1fr);
        gap: 6px;
    }
    .mobile-home-carte-live-left-marker,
    .mobile-home-carte-live-spacer-marker,
    .mobile-home-carte-live-right-marker {
        display: none !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-home-carte-live-left-marker):has(.mobile-home-carte-live-right-marker) {
        display: grid !important;
        grid-template-columns: minmax(0, 1.22fr) minmax(0, .52fr) minmax(0, 2.26fr) !important;
        gap: 10px !important;
        align-items: start !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-home-carte-live-left-marker):has(.mobile-home-carte-live-right-marker) > div[data-testid="column"] {
        width: 100% !important;
        min-width: 0 !important;
        flex: unset !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.mobile-home-carte-live-left-marker):has(.mobile-home-carte-live-right-marker) .mobile-home-recap {
        margin-top: 0 !important;
    }
    .mobile-home-recap-label {
        color: rgba(255,255,255,.48);
        font-size: 8.5px;
        font-weight: 900;
        letter-spacing: .7px;
        text-transform: uppercase;
        line-height: 1.15;
        margin-bottom: 5px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .mobile-home-recap-value {
        color: var(--recap-color);
        font-family: "DM Mono", monospace;
        font-size: 13px;
        font-weight: 900;
        line-height: 1.05;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .mobile-home-recap-sub {
        color: rgba(255,255,255,.46);
        font-size: 9px;
        line-height: 1.2;
        margin-top: 5px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .mobile-home-recap-sub:empty {
        display: none !important;
        margin: 0 !important;
    }
    .mobile-home-recap .mobile-donut-card {
        min-height: 66px;
        padding: 8px;
        border-radius: 13px;
    }
    .mobile-home-recap .mobile-donut-title {
        font-size: 8.5px;
        margin-bottom: 4px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .mobile-home-recap .mobile-donut-body {
        gap: 5px;
        align-items: center;
    }
    .mobile-home-recap .mobile-donut-ring {
        width: 44px;
        height: 44px;
        min-width: 44px;
    }
    .mobile-home-recap .mobile-donut-hole {
        width: 24px;
        height: 24px;
    }
    .mobile-home-recap .mobile-donut-legend {
        gap: 1px;
    }
    .mobile-home-recap .mobile-donut-legend-row {
        font-size: 7px;
        gap: 3px;
    }
    .mobile-home-recap .mobile-donut-dot {
        width: 5px;
        height: 5px;
    }
    .mobile-home-donut-empty {
        color: rgba(255,255,255,.38);
        font-size: 8px;
        line-height: 1.25;
        padding-top: 4px;
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
    div[data-testid="stRadio"] [role="radiogroup"] > label input[type="radio"],
    div[data-testid="stRadio"] [role="radiogroup"] > label [role="radio"],
    div[data-testid="stRadio"] [role="radiogroup"] > label [data-baseweb="radio"] {
        position: absolute !important;
        width: 0 !important;
        min-width: 0 !important;
        height: 0 !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        opacity: 0 !important;
        pointer-events: none !important;
        overflow: hidden !important;
    }
    div[data-testid="stRadio"] [role="radiogroup"] > label > div {
        width: 100% !important;
        max-width: 100% !important;
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
    .mobile-section-grid {
        display: grid;
        grid-template-columns: repeat(8, minmax(0, 1fr));
        grid-template-rows: repeat(2, auto);
        gap: 8px 9px;
        align-items: stretch;
        margin: 0 auto 26px;
        width: 100%;
    }
    .mobile-section-link {
        min-width: 0;
        width: 100%;
        min-height: 38px;
        display: flex;
        align-items: center;
        justify-content: center;
        text-align: center;
        padding: 6px 3px;
        border-radius: 11px;
        border: 0.5px solid color-mix(in srgb, var(--mobile-section-color, #60a5fa) 52%, rgba(255,255,255,.13));
        border-bottom: 3px solid var(--mobile-section-color, #60a5fa);
        background: linear-gradient(135deg, color-mix(in srgb, var(--mobile-section-color, #60a5fa) 26%, rgba(15,23,42,.92)), rgba(255,255,255,.035));
        color: rgba(255,255,255,.90) !important;
        font-size: 8.5px;
        font-weight: 900;
        line-height: 1.05;
        text-decoration: none !important;
        box-shadow: 0 8px 18px rgba(0,0,0,.16);
    }
    .mobile-section-link.active {
        background: linear-gradient(135deg, color-mix(in srgb, var(--mobile-section-color, #60a5fa) 48%, rgba(15,23,42,.88)), rgba(255,255,255,.08));
        box-shadow: 0 0 0 1px color-mix(in srgb, var(--mobile-section-color, #60a5fa) 48%, transparent), 0 10px 22px rgba(0,0,0,.22);
        color: #ffffff !important;
    }
    .mobile-section-link.panoramica { --mobile-section-color:#38bdf8; grid-column:1 / span 2; grid-row:1; }
    .mobile-section-link.spese { --mobile-section-color:#f87171; grid-column:4; grid-row:1; }
    .mobile-section-link.variabili { --mobile-section-color:#f59e0b; grid-column:5; grid-row:1; }
    .mobile-section-link.entrate { --mobile-section-color:#34d399; grid-column:7; grid-row:1; }
    .mobile-section-link.risparmi { --mobile-section-color:#facc15; grid-column:8; grid-row:1; }
    .mobile-section-link.carte { --mobile-section-color:#89cff0; grid-column:1; grid-row:2; }
    .mobile-section-link.promemoria { --mobile-section-color:#fde68a; grid-column:2; grid-row:2; }
    .mobile-section-link.turni { --mobile-section-color:#60a5fa; grid-column:4 / span 2; grid-row:2; }
    .mobile-section-link.storico { --mobile-section-color:#a78bfa; grid-column:7; grid-row:2; }
    .mobile-section-link.bollette { --mobile-section-color:#fb923c; grid-column:8; grid-row:2; }
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
    #mobile-variabili { border-top-color: rgba(245,158,11,.48); }
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
        gap:6px 10px;
        flex-wrap:wrap;
        margin-top:10px;
        font-size:11px;
        line-height:1.35;
        color:rgba(255,255,255,.62);
        align-items:center;
    }
    .mobile-calendar-legend .legend-item {
        display:inline-flex;
        align-items:center;
        gap:4px;
        white-space:nowrap;
    }
    .mobile-calendar-legend .legend-shift {
        padding-bottom:2px;
        border-bottom-width:3px !important;
        border-bottom-style:solid;
    }
    .mobile-calendar-legend .legend-muted {
        color:rgba(255,255,255,.72);
    }
    .mobile-calendar-legend .legend-sep {
        width:1px;
        height:13px;
        background:rgba(255,255,255,.16);
    }
    .mobile-calendar-legend .legend-current {
        color:#fb923c;
        font-weight:1000;
        font-size:13px;
        line-height:1;
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
    mobile_section = st.session_state.get("mobile_section_select", "Panoramica")
    def _mobile_nav_salary_params():
        stipendio_nav = float(st.session_state.get("mobile_salary_stipendio_percepito_value", DEFAULT_STIPENDIO_PERCEPITO))
        quota_nav = float(st.session_state.get("mobile_salary_budget_da_stipendio_value", DEFAULT_QUOTA_STIPENDIO))
        risp_nav = float(st.session_state.get("mobile_salary_risparmi_mese_precedente_value", DEFAULT_RISPARMI_MESE_PRECEDENTE))
        quota_nav = min(quota_nav, stipendio_nav)
        return f"&stip={stipendio_nav:.2f}&quota={quota_nav:.2f}&risp={risp_nav:.2f}"

    _salary_nav_params = _mobile_nav_salary_params()
    _mobile_nav_html = ['<div class="mobile-section-grid">']
    for _section, _css_class, _fallback_label, _description in _mobile_cards:
        _label = mobile_section_labels.get(_section, _fallback_label)
        _active = " active" if _section == mobile_section else ""
        _mobile_nav_html.append(
            f'<a class="mobile-section-link {_css_class}{_active}" '
            f'href="?view=mobile&mobile_section={html.escape(_section)}{_salary_nav_params}#mobile-top" '
            f'target="_self">{html.escape(_label)}</a>'
        )
    _mobile_nav_html.append("</div>")
    st.markdown("\n".join(_mobile_nav_html), unsafe_allow_html=True)

def _mobile_show(*sections):
    return (not MOBILE_VIEW) or (mobile_section in sections)


# Flag per controllare se la configurazione della pagina è già stata impostata
page_config_set = False

def set_page_config():
    pass # Rimuoviamo il contenuto di questa funzione, non è più necessario

# /////  
# Variabili inizializzate
input_stipendio_originale=DEFAULT_STIPENDIO_PERCEPITO
input_risparmi_mese_precedente=DEFAULT_RISPARMI_MESE_PRECEDENTE
input_stipendio_scelto=DEFAULT_QUOTA_STIPENDIO
input_stipendio_percepito = input_stipendio_originale
input_budget_da_stipendio = input_stipendio_scelto
totale_entrate_target_oltre_lo_stipendio= 0.9
budget_mensile_disponibile_ideale = 2615
budget_mensile_disponibile_ideale_precedente = 2515
risparmio_mensile_desiderato = 200

percentuale_limite_da_spendere=0.15
limite_da_spendere=80
limite_emergenze_compleanni=90
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
    "turni_calendario_riepilogo": [1.68, 0.50],
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
    if "Media Stipendio" in data_stipendi.columns and data_stipendi["Media Stipendio"].notna().any():
        stats_stip["Stipendio"]["media"] = float(data_stipendi["Media Stipendio"].dropna().iloc[-1])
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
    if emergenze_compleanni > 0:
        soglie.append(limite_emergenze_compleanni / emergenze_compleanni)
    if coeff_da_spendere > 0:
        soglie.append(limite_da_spendere / coeff_da_spendere)
    if coeff_spese_quotidiane > 0:
        soglie.append(max_spese_quotidiane / coeff_spese_quotidiane)

    budget_dopo_spese_fisse_target = max(soglie) if soglie else 0
    emergenze_reale = emergenze_compleanni * budget_dopo_spese_fisse_target
    da_spendere_reale = coeff_da_spendere * budget_dopo_spese_fisse_target
    spese_quotidiane_reali = coeff_spese_quotidiane * budget_dopo_spese_fisse_target
    risparmio_auto_variabili = (
        max(0, emergenze_reale - limite_emergenze_compleanni)
        + max(0, da_spendere_reale - limite_da_spendere)
        + max(0, spese_quotidiane_reali - max_spese_quotidiane)
    )

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
        title="Distribuzione",
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
.turni-giornata { border-left-color:#c084fc; }
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
CALENDAR_SEDE_ICAL_URLS = {
    "Sede": "https://calendar.google.com/calendar/ical/ff7imcief5ud32g9u3852njf94%40group.calendar.google.com/private-2a37c613b6ca1fc73b5691927398db4a/basic.ics",
}

TURNI_ORARI = {
    "Mattina": ("06:00", "14:00"),
    "Pomeriggio": ("14:00", "22:00"),
    "Notte": ("22:00", "06:00"),
    "Giornata": ("09:00", "17:00"),
    "Ferie": ("09:00", "17:00"),
    "Riposo": ("00:00", "00:00"),
}

DEFAULT_TURNI_RULES = {
    # ``paga_oraria`` resta come alias legacy per non rompere widget e fogli
    # esistenti; il cedolino V2 usa esclusivamente paga_oraria_lorda.
    "paga_oraria": 18.01988,
    "quota_fissa_mensile": 0.0,
    "paga_oraria_lorda": 18.01988,
    "netto_fisso_mensile": 2200.0,
    "coefficiente_netto_variabili": 0.60,
    "errore_medio_calibrazione": 0.0,
    "finestra_calibrazione_mesi": 12.0,
    "rettifica_mensile": -63.0,
    "ritardo_competenze_mesi": 1.0,
    "m_p_feriale_pct": 20.0,
    "m_p_festivo_giorno_pct": 50.0,
    "notte_feriale_pct": 20.0,
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
    "ind_notte_feriale": 18.0,
    "ind_m_p_festivo": 15.0,
    "ind_notte_festiva": 25.0,
}
TURNI_RULES_WORKSHEET = "Regole Turni"
TURNI_RULES_HEADERS = list(DEFAULT_TURNI_RULES.keys())
PAYROLL_ADJUSTMENTS_WORKSHEET = "Rettifiche Cedolino"
PAYROLL_ADJUSTMENTS_HEADERS = ["Mese", "Importo", "Descrizione"]
PAYSLIP_ALIASES_WORKSHEET = "Voci Cedolino"
PAYSLIP_ALIASES_HEADERS = [
    "Voce",
    "Segno",
    "Includi",
    "Categoria",
    "Ultimo mese",
    "Ultimo importo",
]
PAYSLIP_FILES_WORKSHEET = "Cedolini PDF"
PAYSLIP_FILES_HEADERS = [
    "File ID",
    "Nome file",
    "Mese",
    "Stato",
    "Rettifica",
    "Descrizione",
    "Modificato il",
    "Revisionato il",
]
DEFAULT_PAYROLL_ADJUSTMENT = -63.0
DEFAULT_PAYROLL_ADJUSTMENT_DESCRIPTION = "Solite trattenute + accrediti + trattenute"


def _money_turni(value):
    try:
        return f"€{float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "€0,00"


def _signed_money_turni(value):
    amount = float(value or 0.0)
    if abs(amount) < 0.005:
        return "€0,00"
    sign = "+" if amount > 0 else "−"
    return f"{sign}{_money_turni(abs(amount))}"


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


@st.cache_data(ttl=120, show_spinner=False)
def list_drive_payslip_pdfs():
    """Elenca i PDF della cartella cedolini condivisa con l'app."""
    service = get_drive_service()
    if service is None:
        raise RuntimeError("Il collegamento Google Drive non è disponibile.")
    files = []
    page_token = None
    try:
        while True:
            response = service.files().list(
                q=(
                    f"'{CEDOLINI_DRIVE_FOLDER_ID}' in parents and trashed = false "
                    "and mimeType = 'application/pdf'"
                ),
                fields=(
                    "nextPageToken,files(id,name,modifiedTime,md5Checksum,size,webViewLink)"
                ),
                orderBy="modifiedTime desc",
                pageToken=page_token,
                spaces="drive",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            files.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return files
    except HttpError as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status in (401, 403, 404):
            raise RuntimeError(
                "La cartella Cedolini non è ancora accessibile al servizio dell’app. "
                "Deve essere condivisa con permesso di modifica."
            ) from None
        raise RuntimeError("Google Drive non risponde in questo momento.") from None


@st.cache_data(ttl=600, show_spinner=False)
def download_drive_payslip_pdf(file_id):
    service = get_drive_service()
    if service is None or not str(file_id or "").strip():
        raise RuntimeError("Il PDF su Drive non è disponibile.")
    try:
        request = service.files().get_media(
            fileId=str(file_id),
            supportsAllDrives=True,
        )
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buffer.getvalue()
    except HttpError:
        raise RuntimeError("Non sono riuscito a leggere questo PDF da Google Drive.") from None


def upload_payslip_pdf_to_drive(pdf_bytes, filename):
    """Archivia il PDF senza sovrascrivere file diversi con lo stesso nome."""
    service = get_drive_service()
    if service is None:
        raise RuntimeError("Il collegamento Google Drive non è disponibile.")
    data = bytes(pdf_bytes or b"")
    if not data:
        raise ValueError("Il PDF è vuoto.")
    existing_files = list_drive_payslip_pdfs()
    checksum = hashlib.md5(data, usedforsecurity=False).hexdigest()
    for item in existing_files:
        if item.get("md5Checksum") == checksum:
            return dict(item), False
    target_name = unique_pdf_filename(
        safe_pdf_filename(filename),
        [item.get("name", "") for item in existing_files],
        _now_italy().strftime("%Y%m%d-%H%M"),
    )
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="application/pdf", resumable=False)
    try:
        created = service.files().create(
            body={"name": target_name, "parents": [CEDOLINI_DRIVE_FOLDER_ID]},
            media_body=media,
            fields="id,name,modifiedTime,md5Checksum,size,webViewLink",
            supportsAllDrives=True,
        ).execute()
        list_drive_payslip_pdfs.clear()
        return dict(created), True
    except HttpError as exc:
        message = str(exc).lower()
        if "storagequota" in message or "storage quota" in message:
            raise RuntimeError(
                "La cartella consente la lettura ma non il caricamento del servizio. "
                "Per l’upload dall’app serve una cartella in un Drive condiviso oppure "
                "un’autorizzazione Google personale."
            ) from None
        raise RuntimeError(
            "Non sono riuscito a salvare il PDF nella cartella Cedolini di Google Drive."
        ) from None


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
            "Giornata": {"red": 0.55, "green": 0.36, "blue": 0.96},
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
        rules = DEFAULT_TURNI_RULES.copy()
        try:
            saved_rules = load_data_gsheets(TURNI_RULES_WORKSHEET, TURNI_RULES_HEADERS)
            if saved_rules is not None and not saved_rules.empty:
                saved_row = saved_rules.iloc[-1].to_dict()
                migrated = migrate_payroll_rules(saved_row, PAYROLL_V2_DEFAULTS)
                for key, default_value in DEFAULT_TURNI_RULES.items():
                    saved_value = saved_row.get(key)
                    if (
                        key in saved_row
                        and pd.notna(saved_value)
                        and str(saved_value).strip() != ""
                    ):
                        rules[key] = _parse_float_turni(saved_row[key])
                    elif key in migrated:
                        rules[key] = float(migrated[key])
                # Sincronizza l'alias usato dal vecchio contatore live.
                rules["paga_oraria"] = float(rules["paga_oraria_lorda"])
        except Exception:
            # Senza collegamento a Google Sheets rimangono valide le regole locali.
            pass
        st.session_state.turni_rules = rules
    else:
        for key, value in DEFAULT_TURNI_RULES.items():
            st.session_state.turni_rules.setdefault(key, value)
    return st.session_state.turni_rules


def save_turni_rules(rules):
    """Salva l'ultima configurazione delle regole in un foglio dedicato."""
    row = {
        key: float(rules.get(key, default_value))
        for key, default_value in DEFAULT_TURNI_RULES.items()
    }
    return save_data_gsheets(
        TURNI_RULES_WORKSHEET,
        TURNI_RULES_HEADERS,
        pd.DataFrame([row], columns=TURNI_RULES_HEADERS),
    )


def load_payroll_adjustments(force_reload=False):
    """Carica le rettifiche indicizzate per mese cedolino (YYYY-MM)."""
    data = load_data_gsheets(
        PAYROLL_ADJUSTMENTS_WORKSHEET,
        PAYROLL_ADJUSTMENTS_HEADERS,
        force_reload=force_reload,
    )
    adjustments = {}
    if data is None or data.empty:
        return adjustments
    for _, row in data.iterrows():
        month = pd.to_datetime(row.get("Mese"), errors="coerce")
        if pd.isna(month):
            continue
        month_key = month.strftime("%Y-%m")
        adjustments[month_key] = {
            "amount": _parse_float_turni(row.get("Importo", 0.0)),
            "description": str(row.get("Descrizione", "") or "").strip(),
        }
    return adjustments


def save_payroll_adjustment(month_key, amount, description=""):
    """Crea, aggiorna o rimuove la rettifica di un singolo cedolino."""
    data = load_data_gsheets(
        PAYROLL_ADJUSTMENTS_WORKSHEET,
        PAYROLL_ADJUSTMENTS_HEADERS,
        force_reload=True,
    )
    if data is None:
        data = pd.DataFrame(columns=PAYROLL_ADJUSTMENTS_HEADERS)
    data = data.copy()
    for column in PAYROLL_ADJUSTMENTS_HEADERS:
        if column not in data.columns:
            data[column] = ""
    parsed_months = pd.to_datetime(data["Mese"], errors="coerce").dt.strftime("%Y-%m")
    data = data[parsed_months != month_key].copy()
    clean_description = str(description or "").strip()
    if abs(float(amount)) > 1e-9 or clean_description:
        data = pd.concat([
            data,
            pd.DataFrame([{
                "Mese": f"{month_key}-01",
                "Importo": float(amount),
                "Descrizione": clean_description,
            }]),
        ], ignore_index=True)
    return save_data_gsheets(
        PAYROLL_ADJUSTMENTS_WORKSHEET,
        PAYROLL_ADJUSTMENTS_HEADERS,
        data[PAYROLL_ADJUSTMENTS_HEADERS],
    )


def load_payslip_aliases(force_reload=False):
    """Carica le decisioni confermate senza confonderle con le rettifiche mensili."""
    data = load_data_gsheets(
        PAYSLIP_ALIASES_WORKSHEET,
        PAYSLIP_ALIASES_HEADERS,
        force_reload=force_reload,
    )
    aliases = {}
    if data is None or data.empty:
        return aliases
    for _, row in data.iterrows():
        label = str(row.get("Voce", "") or "").strip()
        if not label:
            continue
        aliases[label] = {
            "sign": 1 if _parse_float_turni(row.get("Segno", 1)) >= 0 else -1,
            "include": _parse_bool_turni(row.get("Includi", True)),
            "category": str(row.get("Categoria", "Voce già confermata") or "").strip(),
        }
    return aliases


def save_payslip_aliases(reviewed_rows, month_key):
    """Aggiorna la memoria delle voci revisionate preservando quelle precedenti."""
    data = load_data_gsheets(
        PAYSLIP_ALIASES_WORKSHEET,
        PAYSLIP_ALIASES_HEADERS,
        force_reload=True,
    )
    if data is None:
        data = pd.DataFrame(columns=PAYSLIP_ALIASES_HEADERS)
    data = data.copy()
    for column in PAYSLIP_ALIASES_HEADERS:
        if column not in data.columns:
            data[column] = ""
    existing_by_signature = {
        label_signature(row.get("Voce", "")): index
        for index, row in data.iterrows()
        if label_signature(row.get("Voce", ""))
    }
    for row in reviewed_rows:
        label = str(row.get("Voce", "") or "").strip()
        signature = label_signature(label)
        if not signature:
            continue
        sign = 1 if str(row.get("Segno", "+ Accredito")).startswith("+") else -1
        values = {
            "Voce": label,
            "Segno": sign,
            "Includi": _parse_bool_turni(row.get("Includi", False)),
            "Categoria": str(row.get("Categoria", "") or "Voce confermata"),
            "Ultimo mese": month_key,
            "Ultimo importo": abs(_parse_float_turni(row.get("Importo", 0.0))),
        }
        if signature in existing_by_signature:
            index = existing_by_signature[signature]
            for column, value in values.items():
                data.at[index, column] = value
        else:
            data = pd.concat([data, pd.DataFrame([values])], ignore_index=True)
            existing_by_signature[signature] = data.index[-1]
    return save_data_gsheets(
        PAYSLIP_ALIASES_WORKSHEET,
        PAYSLIP_ALIASES_HEADERS,
        data[PAYSLIP_ALIASES_HEADERS],
    )


def load_payslip_file_registry(force_reload=False):
    data = load_data_gsheets(
        PAYSLIP_FILES_WORKSHEET,
        PAYSLIP_FILES_HEADERS,
        force_reload=force_reload,
    )
    registry = {}
    if data is None or data.empty:
        return registry
    for _, row in data.iterrows():
        file_id = str(row.get("File ID", "") or "").strip()
        if not file_id:
            continue
        month = pd.to_datetime(row.get("Mese"), errors="coerce")
        registry[file_id] = {
            "name": str(row.get("Nome file", "") or "").strip(),
            "month": month.strftime("%Y-%m") if pd.notna(month) else "",
            "status": str(row.get("Stato", "") or "").strip(),
            "adjustment": _parse_float_turni(row.get("Rettifica", 0.0)),
            "description": str(row.get("Descrizione", "") or "").strip(),
            "modified_time": str(row.get("Modificato il", "") or "").strip(),
            "reviewed_time": str(row.get("Revisionato il", "") or "").strip(),
        }
    return registry


def save_payslip_file_record(file_meta, month_key, adjustment, description):
    data = load_data_gsheets(
        PAYSLIP_FILES_WORKSHEET,
        PAYSLIP_FILES_HEADERS,
        force_reload=True,
    )
    if data is None:
        data = pd.DataFrame(columns=PAYSLIP_FILES_HEADERS)
    data = data.copy()
    for column in PAYSLIP_FILES_HEADERS:
        if column not in data.columns:
            data[column] = ""
    file_id = str(file_meta.get("id", "") or "").strip()
    if not file_id:
        return False
    data = data[data["File ID"].astype(str) != file_id].copy()
    row = {
        "File ID": file_id,
        "Nome file": str(file_meta.get("name", "") or "").strip(),
        "Mese": f"{month_key}-01",
        "Stato": "Confermato",
        "Rettifica": float(adjustment),
        "Descrizione": str(description or "")[:1000],
        "Modificato il": str(file_meta.get("modifiedTime", "") or ""),
        "Revisionato il": _now_italy().isoformat(timespec="seconds"),
    }
    data = pd.concat([data, pd.DataFrame([row])], ignore_index=True)
    return save_data_gsheets(
        PAYSLIP_FILES_WORKSHEET,
        PAYSLIP_FILES_HEADERS,
        data[PAYSLIP_FILES_HEADERS],
    )


def payroll_adjustment_for_month(month_key, adjustment_rows=None):
    """Restituisce la rettifica esplicita o il valore netto medio predefinito."""
    rows = load_payroll_adjustments() if adjustment_rows is None else adjustment_rows
    saved = rows.get(month_key)
    if saved is None:
        return {
            "amount": DEFAULT_PAYROLL_ADJUSTMENT,
            "description": DEFAULT_PAYROLL_ADJUSTMENT_DESCRIPTION,
            "is_default": True,
        }
    return {
        "amount": float(saved.get("amount", DEFAULT_PAYROLL_ADJUSTMENT)),
        "description": str(saved.get("description", "") or "").strip(),
        "is_default": False,
    }


def _render_payslip_pdf_review(default_month_key):
    """Sincronizza Drive e app; nessun importo viene applicato senza conferma."""
    with st.expander("🧾 Analizza un nuovo cedolino PDF", expanded=False):
        st.caption(
            "I PDF caricati qui vengono archiviati nella cartella Cedolini su Google Drive; "
            "quelli aggiunti direttamente su Drive compaiono qui come da controllare. "
            "La rettifica cambia solo dopo la tua conferma."
        )
        drive_files = []
        drive_error = ""
        registry = load_payslip_file_registry()
        try:
            drive_files = list_drive_payslip_pdfs()
        except RuntimeError as exc:
            drive_error = str(exc)
        # Lo storico fino a giugno 2026 resta intenzionalmente sulla rettifica
        # media; luglio 2026 ha già una rettifica esplicita. I file più vecchi
        # rimangono comunque selezionabili per una revisione facoltativa.
        review_start_month = "2026-08"
        file_months = {
            str(item.get("id", "")): extract_payslip_month(
                "", str(item.get("name", "") or "")
            )
            for item in drive_files
        }
        pending_candidates = [
            item
            for item in drive_files
            if not file_months.get(str(item.get("id", "")))
            or file_months[str(item.get("id", ""))] >= review_start_month
        ]
        pending_files = pending_drive_files(pending_candidates, registry)

        drive_cols = st.columns(2)
        drive_cols[0].metric("PDF su Drive", len(drive_files) if not drive_error else "—")
        drive_cols[1].metric("Da controllare", len(pending_files) if not drive_error else "—")
        if drive_error:
            st.warning(
                f"{drive_error} Puoi comunque analizzare il PDF dall’app; per ottenere la "
                "sincronizzazione completa bisogna abilitare l’accesso della cartella."
            )
        if st.button(
            "🔄 Aggiorna elenco da Drive",
            key="refresh_drive_payslips",
            use_container_width=True,
        ):
            list_drive_payslip_pdfs.clear()
            st.session_state.pop("active_drive_payslip", None)
            load_data_gsheets(
                PAYSLIP_FILES_WORKSHEET,
                PAYSLIP_FILES_HEADERS,
                force_reload=True,
            )
            st.rerun()

        source_options = ["Google Drive", "Carica dall’app"] if drive_files else ["Carica dall’app"]
        source_mode = st.radio(
            "Da dove vuoi prenderlo?",
            source_options,
            horizontal=True,
            key="payroll_pdf_source",
        )
        pdf_bytes = None
        pdf_name = ""
        file_meta = None
        source_key = ""
        if source_mode == "Google Drive":
            pending_ids = {str(item.get("id", "")) for item in pending_files}
            drive_by_id = {str(item.get("id", "")): item for item in drive_files}
            ordered_ids = [str(item.get("id", "")) for item in pending_files]
            ordered_ids.extend(file_id for file_id in drive_by_id if file_id not in pending_ids)

            def _drive_file_label(file_id):
                item = drive_by_id[file_id]
                reviewed = str(registry.get(file_id, {}).get("status", "")).lower() == "confermato"
                if file_id in pending_ids:
                    status_icon = "🟠"
                elif reviewed:
                    status_icon = "✅"
                else:
                    status_icon = "⚪"
                return f"{status_icon} {item.get('name', 'Cedolino PDF')}"

            selected_file_id = st.selectbox(
                "Cedolino presente su Drive",
                ordered_ids,
                format_func=_drive_file_label,
                key="selected_drive_payslip",
                help=(
                    "Arancione: nuovo e da controllare. Verde: già confermato. "
                    "Bianco: storico facoltativo, ancora revisionabile."
                ),
            )
            if st.button(
                "📄 Apri e analizza il PDF selezionato",
                key="analyze_selected_drive_payslip",
                use_container_width=True,
            ):
                st.session_state["active_drive_payslip"] = selected_file_id
            active_file_id = str(st.session_state.get("active_drive_payslip", ""))
            if active_file_id == selected_file_id:
                file_meta = drive_by_id.get(selected_file_id)
            if file_meta is not None:
                pdf_name = str(file_meta.get("name", "cedolino.pdf"))
                source_key = str(file_meta.get("id", ""))
                try:
                    pdf_bytes = download_drive_payslip_pdf(source_key)
                except RuntimeError as exc:
                    st.error(str(exc))
                    return
                previous_review = registry.get(source_key)
                if previous_review and str(previous_review.get("status", "")).lower() == "confermato":
                    st.info(
                        f"Questo PDF era già stato confermato per {previous_review.get('month', '—')} "
                        f"con rettifica {_signed_money_turni(previous_review.get('adjustment', 0))}."
                    )
        else:
            uploaded_pdf = st.file_uploader(
                "Cedolino PDF",
                type=["pdf"],
                key="payroll_pdf_upload",
                help=(
                    "Alla conferma viene salvato nella cartella Cedolini su Drive. "
                    "I PDF scansionati come immagine richiedono OCR e per ora non sono supportati."
                ),
            )
            if uploaded_pdf is not None:
                pdf_bytes = uploaded_pdf.getvalue()
                pdf_name = uploaded_pdf.name
                source_key = hashlib.sha256(pdf_bytes).hexdigest()[:16]

        if pdf_bytes is None:
            st.info(
                "I cedolini da marzo 2024 a giugno 2026 restano sulla rettifica media di "
                "−€63 finché non li revisioni. Luglio 2026 è già stato corretto a +€171."
            )
            return
        try:
            pdf_text = extract_pdf_text(pdf_bytes)
            inferred_month = extract_payslip_month(pdf_text, pdf_name)
            learned_aliases = load_payslip_aliases()
            candidates = find_adjustment_candidates(pdf_text, learned_aliases)
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))
            return

        review_month = st.text_input(
            "Mese del cedolino (AAAA-MM)",
            value=inferred_month or default_month_key,
            key=f"payroll_pdf_month::{source_key}",
            help="È il mese scritto sul cedolino, non il mese delle variabili pagate.",
        ).strip()
        if inferred_month:
            st.caption(f"Mese riconosciuto dal PDF: **{inferred_month}**.")
        else:
            st.warning("Non ho riconosciuto il mese: controllalo prima di confermare.")

        review_rows = pd.DataFrame([
            {
                "Includi": candidate.include,
                "Voce": candidate.description,
                "Categoria": candidate.category,
                "Segno": "+ Accredito" if candidate.sign > 0 else "− Trattenuta",
                "Importo": candidate.amount,
                "Confidenza": round(candidate.confidence * 100),
                "Riga PDF": candidate.source_line,
            }
            for candidate in candidates
        ], columns=[
            "Includi", "Voce", "Categoria", "Segno", "Importo", "Confidenza", "Riga PDF"
        ])
        if review_rows.empty:
            st.warning(
                "Non ho trovato voci riconoscibili. Puoi aggiungerle manualmente nella "
                "tabella oppure continuare a usare la rettifica mensile nelle Regole."
            )
        elif any("possibile importo lordo" in str(value) for value in review_rows["Categoria"]):
            st.warning(
                "EDR, premi e arretrati possono essere esposti lordi nel PDF: per sicurezza "
                "non sono preselezionati. Includili solo dopo aver sostituito l’importo con "
                "l’effetto netto che vuoi applicare."
            )
        edited_rows = st.data_editor(
            review_rows,
            hide_index=True,
            use_container_width=True,
            num_rows="dynamic",
            column_config={
                "Includi": st.column_config.CheckboxColumn("Includi", default=False),
                "Segno": st.column_config.SelectboxColumn(
                    "Segno",
                    options=["+ Accredito", "− Trattenuta"],
                    required=True,
                ),
                "Importo": st.column_config.NumberColumn(
                    "Importo",
                    min_value=0.0,
                    step=0.01,
                    format="€ %.2f",
                ),
                "Confidenza": st.column_config.NumberColumn("Sicurezza %", format="%d%%"),
                "Riga PDF": st.column_config.TextColumn("Testo originale", width="large"),
            },
            disabled=["Categoria", "Confidenza", "Riga PDF"],
            key=f"payroll_pdf_review::{source_key}",
        )
        reviewed = []
        total = 0.0
        for _, row in edited_rows.iterrows():
            values = row.to_dict()
            include = _parse_bool_turni(values.get("Includi", False))
            amount = abs(_parse_float_turni(values.get("Importo", 0.0)))
            sign = 1 if str(values.get("Segno", "")).startswith("+") else -1
            values["Includi"] = include
            values["Importo"] = amount
            reviewed.append(values)
            if include:
                total += sign * amount

        current = load_payroll_adjustments().get(review_month)
        if current:
            st.warning(
                f"Per {review_month} è già salvata una rettifica di "
                f"{_signed_money_turni(current.get('amount', 0))}: la conferma la sostituirà."
            )
        st.metric("Rettifica netta proposta", _signed_money_turni(total))
        confirmed = st.checkbox(
            "Ho controllato voci, segni e importi: confermo questa rettifica netta.",
            key=f"confirm_payroll_pdf::{source_key}::{review_month}",
        )
        if st.button(
            "✅ Conferma, sincronizza Drive e aggiorna calibrazione",
            key=f"save_payroll_pdf::{source_key}::{review_month}",
            use_container_width=True,
            disabled=not confirmed,
        ):
            if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", review_month):
                st.error("Il mese deve avere il formato AAAA-MM, per esempio 2026-07.")
                return
            selected = [row for row in reviewed if row.get("Includi")]
            if not selected:
                st.error("Seleziona almeno una voce da includere.")
                return
            summary_parts = []
            for row in selected:
                sign = "+" if str(row.get("Segno", "")).startswith("+") else "−"
                label = str(row.get("Voce", "Voce PDF") or "Voce PDF").strip()
                summary_parts.append(f"{sign}{_money_turni(row.get('Importo', 0))} {label}")
            description = "PDF verificato: " + "; ".join(summary_parts)
            if file_meta is None:
                try:
                    file_meta, created_on_drive = upload_payslip_pdf_to_drive(pdf_bytes, pdf_name)
                except (ValueError, RuntimeError) as exc:
                    st.error(
                        f"{exc} La rettifica non è stata modificata: puoi caricare il PDF "
                        "direttamente nella cartella Drive e poi premere “Aggiorna elenco da Drive”."
                    )
                    return
            else:
                created_on_drive = False
            adjustment_saved = save_payroll_adjustment(review_month, total, description[:1000])
            if not adjustment_saved:
                st.error("Non sono riuscito a salvare la rettifica su Google Sheets.")
                return
            aliases_saved = save_payslip_aliases(reviewed, review_month)
            registry_saved = save_payslip_file_record(
                file_meta,
                review_month,
                total,
                description,
            )
            st.session_state.pop("payroll_calibration_editor", None)
            archive_message = (
                "PDF archiviato su Drive. " if created_on_drive else "PDF collegato a Drive. "
            )
            st.success(
                f"{archive_message}Rettifica di {_signed_money_turni(total)} salvata per {review_month}. "
                "La calibrazione qui sotto usa già il nuovo valore."
            )
            if not aliases_saved:
                st.warning(
                    "La rettifica è salva, ma non sono riuscito ad aggiornare la memoria delle voci."
                )
            if not registry_saved:
                st.warning(
                    "PDF e rettifica sono salvi, ma non sono riuscito a marcarlo come revisionato."
                )


def _payroll_v2_rules(rules):
    migrated = migrate_payroll_rules(rules, PAYROLL_V2_DEFAULTS)
    migrated["paga_oraria_lorda"] = float(
        rules.get("paga_oraria_lorda", migrated["paga_oraria_lorda"])
    )
    if migrated["paga_oraria_lorda"] <= 0:
        migrated["paga_oraria_lorda"] = PAYROLL_V2_DEFAULTS["paga_oraria_lorda"]
    return migrated


def _payroll_shifts_from_df(df_turni):
    shifts = []
    for _, row in _normalize_turni_df(df_turni).iterrows():
        turno = str(row.get("Turno", ""))
        if turno not in TURNI_ORARI or not turno:
            continue
        try:
            shifts.append(PayrollShift(
                day=pd.to_datetime(row["Data"]).date(),
                kind=turno,
                forced_holiday=bool(row.get("Festivo", False)),
                overtime_minutes=_turni_row_straordinario_minuti(row),
                onsite=_turni_row_sede(row),
            ))
        except (TypeError, ValueError):
            continue
    return shifts


def _payroll_variables_by_month(df_turni, rules):
    grouped = {}
    for shift in _payroll_shifts_from_df(df_turni):
        grouped.setdefault(shift.day.strftime("%Y-%m"), []).append(shift)
    v2_rules = _payroll_v2_rules(rules)
    return {
        month: calculate_month_variables(month_shifts, v2_rules)
        for month, month_shifts in grouped.items()
    }


def _payroll_estimate_for_month(df_turni, rules, month_key):
    uncertainty = float(st.session_state.get(
        "payroll_calibration_mae",
        rules.get("errore_medio_calibrazione", 0.0),
    ))
    adjustment_rows = load_payroll_adjustments()
    adjustments = {
        month: float(values.get("amount", 0.0))
        for month, values in adjustment_rows.items()
    }
    adjustments.setdefault(month_key, DEFAULT_PAYROLL_ADJUSTMENT)
    return estimate_payslip(
        month_key,
        _payroll_variables_by_month(df_turni, rules),
        _payroll_v2_rules(rules),
        uncertainty=uncertainty,
        adjustments=adjustments,
    )


def _apply_turni_rules_from_widgets(rules):
    widget_to_rule = {
        "turni_paga": "paga_oraria",
        "turni_paga_lorda": "paga_oraria_lorda",
        "turni_netto_fisso": "netto_fisso_mensile",
        "turni_coeff_variabili": "coefficiente_netto_variabili",
        "turni_finestra_calibrazione": "finestra_calibrazione_mesi",
        "turni_ritardo_competenze": "ritardo_competenze_mesi",
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
        "turni_buono_pasto": "buono_pasto",
        "turni_smart_target": "smart_target",
        "turni_ind_mp_f": "ind_m_p_feriale",
        "turni_ind_n_f": "ind_notte_feriale",
        "turni_ind_mp_fe": "ind_m_p_festivo",
        "turni_ind_n_fe": "ind_notte_festiva",
    }
    for widget_key, rule_key in widget_to_rule.items():
        if widget_key in st.session_state:
            rules[rule_key] = float(st.session_state[widget_key])
    rules["paga_oraria"] = float(rules.get("paga_oraria_lorda", rules["paga_oraria"]))
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
    minutes = dt_obj.hour * 60 + dt_obj.minute
    # Il sabato tra le 06:00 e le 18:00 matura l'indennità prevista,
    # ma non la maggiorazione oraria. Dalle 18:00 si torna alla regola
    # ordinaria del turno (per il pomeriggio: 18:00-22:00).
    if dt_obj.weekday() == 5 and 6 * 60 <= minutes < 18 * 60:
        return 0.0
    festive = _is_festive_at(dt_obj, forced_festivo)
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
    if turno in ["Ferie", "Riposo", "Giornata"]:
        return 0.0
    start, _ = _shift_bounds(data_str, turno)
    festive_at_start = _is_festive_at(start, forced_festivo)
    is_saturday = start.weekday() == 5

    # Le indennità maturano solo di sabato, domenica e nei festivi.
    if not festive_at_start and not is_saturday:
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
    else:
        key = "stra_mattina_festivo_pct" if festive else "stra_mattina_feriale_pct"
    return float(rules.get(key, fallback))


def _calc_straordinario_minuti(data_str, turno, forced_festivo, rules, until=None, only_day=None, straordinario_minuti=0):
    minuti = int(round(max(0, _parse_float_turni(straordinario_minuti))))
    if minuti <= 0 or turno in ["", "Ferie", "Riposo"]:
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
        "turn_counts": {"Mattina": 0, "Pomeriggio": 0, "Notte": 0, "Giornata": 0, "Ferie": 0},
        "turn_type_counts": {},
        "allowance_turn_type_counts": {},
        "sede_days": 0,
        "sede_required": 0,
        "sede_remaining": 0,
        "buoni_pasto_days": 0,
        "buoni_pasto_total": 0.0,
        "straordinario_minutes": 0,
        "straordinario_total": 0.0,
        "hours_by_pct": {},
        "straordinario_hours_by_pct": {},
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
            if _allowance_for_turno(data, turno, festivo, rules) != 0:
                report["allowance_turn_type_counts"][key] = report["allowance_turn_type_counts"].get(key, 0) + 1
            for pct, hours in _calc_turno_hours_by_pct(data, turno, festivo, rules).items():
                if abs(float(pct)) < 0.001:
                    continue
                report["hours_by_pct"][pct] = report["hours_by_pct"].get(pct, 0.0) + hours
        if sede:
            report["sede_days"] += 1
        if _is_sede_buono_pasto(data, turno, festivo, sede):
            report["buoni_pasto_days"] += 1
        if stra_minuti and turno not in {"Ferie", "Riposo"}:
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
                report["straordinario_hours_by_pct"][pct] = (
                    report["straordinario_hours_by_pct"].get(pct, 0.0) + hours
                )
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
        return {
            **stra_calc,
            "maggiorazione": stra_calc["extra"],
            "indennita": 0.0,
            "rate_min": 0.0,
        }

    if turno == "Ferie":
        start, end = _shift_bounds(data_str, turno)
        if only_day is not None and data_str != only_day:
            return {
                "total": stra_calc["total"], "base": stra_calc["base"],
                "extra": stra_calc["extra"], "maggiorazione": stra_calc["extra"],
                "indennita": 0.0, "hours": stra_calc["hours"], "rate_min": 0.0,
            }
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
            "maggiorazione": stra_calc["extra"],
            "indennita": 0.0,
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
        return {
            "total": stra_calc["total"], "base": stra_calc["base"],
            "extra": stra_calc["extra"], "maggiorazione": stra_calc["extra"],
            "indennita": 0.0, "hours": stra_calc["hours"], "rate_min": 0.0,
        }

    base = 0.0
    maggiorazione = 0.0
    hours = 0.0
    t = start
    while t < effective_end:
        nxt = min(t + timedelta(minutes=1), effective_end)
        h = (nxt - t).total_seconds() / 3600
        pct = _pct_for_turno(turno, t, forced_festivo, rules)
        base += paga * h
        maggiorazione += paga * pct / 100 * h
        hours += h
        t = nxt

    allowance = _allowance_for_turno(data_str, turno, forced_festivo, rules)
    if only_day is not None and data_str != only_day:
        allowance = 0.0
    base += stra_calc["base"]
    maggiorazione += stra_calc["extra"]
    hours += stra_calc["hours"]
    extra = maggiorazione + allowance

    rate_min = 0.0
    current_now = _now_italy()
    if start <= current_now <= end:
        rate_min = paga * (1 + _pct_for_turno(turno, current_now, forced_festivo, rules) / 100) / 60

    return {
        "total": base + extra, "base": base, "extra": extra,
        "maggiorazione": maggiorazione, "indennita": allowance,
        "hours": hours, "rate_min": rate_min,
    }


def _live_net_hourly_base(df_turni, rules, month_key):
    """Distribuisce il fisso netto sulle ore ordinarie pianificate del mese."""
    month_df = _normalize_turni_df(df_turni)
    month_df = month_df[month_df["Data"].str.startswith(month_key)]
    paid_days = month_df[month_df["Turno"].isin(["Mattina", "Pomeriggio", "Notte", "Giornata", "Ferie"])]
    planned_hours = float(len(paid_days) * 8)
    # Se il calendario del mese non è ancora completo, evitiamo una paga oraria
    # artificiosamente alta usando un riferimento prudente di 20 giornate.
    denominator = planned_hours if planned_hours >= 120.0 else 160.0
    return max(0.0, float(rules.get("netto_fisso_mensile", 0.0))) / denominator


def compute_turno_net_estimate(
    data_str,
    turno,
    forced_festivo,
    rules,
    ordinary_net_hourly,
    until=None,
    only_day=None,
    straordinario_minuti=0,
):
    """Versione netta stimata del contatore live, senza alterare i calcoli lordi."""
    gross = compute_turno(
        data_str,
        turno,
        forced_festivo,
        rules,
        until=until,
        only_day=only_day,
        straordinario_minuti=straordinario_minuti,
    )
    overtime = _calc_straordinario_minuti(
        data_str,
        turno,
        forced_festivo,
        rules,
        until=until,
        only_day=only_day,
        straordinario_minuti=straordinario_minuti,
    )
    regular_hours = max(0.0, float(gross.get("hours", 0.0)) - float(overtime.get("hours", 0.0)))
    regular_premium_gross = max(
        0.0,
        float(gross.get("maggiorazione", 0.0)) - float(overtime.get("extra", 0.0)),
    )
    variable_gross = (
        regular_premium_gross
        + float(gross.get("indennita", 0.0))
        + float(overtime.get("total", 0.0))
    )
    coefficient = float(rules.get("coefficiente_netto_variabili", 0.60))
    premium_net = regular_premium_gross * coefficient
    allowance_net = float(gross.get("indennita", 0.0)) * coefficient
    overtime_net = float(overtime.get("total", 0.0)) * coefficient
    total_net = estimate_live_net_accrual(
        regular_hours,
        ordinary_net_hourly,
        variable_gross,
        coefficient,
    )
    rate_min = 0.0
    gross_rate_min = float(gross.get("rate_min", 0.0))
    if gross_rate_min > 0:
        gross_hourly = max(0.0, float(rules.get("paga_oraria_lorda", rules.get("paga_oraria", 0.0))))
        premium_gross_hourly = max(0.0, gross_rate_min * 60.0 - gross_hourly)
        rate_min = (ordinary_net_hourly + premium_gross_hourly * coefficient) / 60.0
    return {
        **gross,
        "total": total_net,
        "base": regular_hours * ordinary_net_hourly,
        "extra": variable_gross * coefficient,
        "rate_min": rate_min,
        "variable_gross": variable_gross,
        "premium_net": premium_net,
        "allowance_net": allowance_net,
        "overtime_net": overtime_net,
    }


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
    turno_kpi_label = "Turno — netto live / totale netto"
    work_days_done = 0
    work_days_total = 0
    ferie_days_total = 0
    live_net_hourly = _live_net_hourly_base(df_turni, rules, current_m)

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
            calc_live = compute_turno_net_estimate(
                data, turno, festivo, rules, live_net_hourly,
                until=now, straordinario_minuti=stra_minuti,
            )
            live_month += calc_live["total"]
            hours_live += calc_live["hours"]
            calc_full = compute_turno_net_estimate(
                data, turno, festivo, rules, live_net_hourly,
                until=datetime.max.replace(tzinfo=None), straordinario_minuti=stra_minuti,
            )
            current_base_full += calc_full["base"]
            start, end = _shift_bounds(data, turno)
            if turno == "Ferie" and start.strftime("%Y-%m-%d") == today:
                is_on_leave = True
                rate_min = calc_live["rate_min"]
                current_shift = f"Ferie {start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
                current_shift_type = "Ferie · base 8h"
                turno_kpi_label = "Ferie — netto live / totale giornata"
                current_turno = "Ferie"
                current_shift_date = _turni_short_date_label(start)
                current_shift_start_date = data
                if now < end:
                    current_shift_end = end
                    current_rate_change_at = start if now < start else None
                live_today = calc_live["total"]
                expected_today = compute_turno_net_estimate(data, turno, festivo, rules, live_net_hourly, until=datetime.max.replace(tzinfo=None), straordinario_minuti=stra_minuti)["total"]
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
                expected_today = compute_turno_net_estimate(data, turno, festivo, rules, live_net_hourly, until=datetime.max.replace(tzinfo=None), straordinario_minuti=stra_minuti)["total"]

        if has_turno and data[:7] == prev_m:
            prev_live_net_hourly = _live_net_hourly_base(df_turni, rules, prev_m)
            calc_prev = compute_turno_net_estimate(data, turno, festivo, rules, prev_live_net_hourly, until=datetime.max.replace(tzinfo=None), straordinario_minuti=stra_minuti)
            prev_extras += calc_prev["extra"]

        if not has_turno:
            continue
        start, end = _shift_bounds(data, turno)
        if turno not in ["Ferie", "Riposo"] and start > now and (next_shift_start is None or start < next_shift_start):
            next_shift_start = start
            next_shift_label = f"{turno} {start.strftime('%d/%m %H:%M')}"
            next_shift_total = compute_turno_net_estimate(data, turno, festivo, rules, live_net_hourly, until=datetime.max.replace(tzinfo=None), straordinario_minuti=stra_minuti)["total"]
        if turno not in ["Ferie", "Riposo"] and end <= now and (last_shift_end is None or end > last_shift_end):
            last_shift_end = end
            last_shift_label = f"{turno} {start.strftime('%d/%m %H:%M')}"
            last_shift_total = compute_turno_net_estimate(data, turno, festivo, rules, live_net_hourly, until=datetime.max.replace(tzinfo=None), straordinario_minuti=stra_minuti)["total"]
        if turno != "Ferie" and current_shift_end is None and start.strftime("%Y-%m-%d") <= today <= end.strftime("%Y-%m-%d"):
            live_today += compute_turno_net_estimate(data, turno, festivo, rules, live_net_hourly, until=now, only_day=today, straordinario_minuti=stra_minuti)["total"]
            expected_today += compute_turno_net_estimate(data, turno, festivo, rules, live_net_hourly, until=datetime.max.replace(tzinfo=None), only_day=today, straordinario_minuti=stra_minuti)["total"]

    if current_shift_end is None and not is_on_leave:
        live_today = last_shift_total
        expected_today = next_shift_total
        turno_kpi_label = "Ultimo / prossimo turno"

    month_report = compute_turni_month_report(df_turni, rules, current_m)
    buoni_pasto_total = float(month_report.get("buoni_pasto_total", 0.0))
    payroll_v2 = _payroll_estimate_for_month(df_turni, rules, current_m)
    monthly_adjustments = float(payroll_v2.adjustment)
    # V2: il fisso netto non viene ricostruito dalle ore ordinarie. Le sole
    # variabili del mese di competenza vengono convertite col coefficiente
    # configurabile; i buoni pasto restano separati dal netto accreditato.
    payslip_estimate = float(payroll_v2.credited_net)

    return {
        "live_month": live_month,
        "payslip_estimate": payslip_estimate,
        "live_today": live_today,
        "expected_today": expected_today,
        "current_base_full": current_base_full,
        "prev_extras": prev_extras,
        "hours_live": hours_live,
        "rate_min": rate_min,
        "live_net_hourly_base": live_net_hourly,
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
        "next_shift_total": next_shift_total,
        "last_shift_total": last_shift_total,
        "work_days_done": work_days_done,
        "work_days_total": work_days_total,
        "ferie_days_total": ferie_days_total,
        "monthly_adjustments": monthly_adjustments,
        "buoni_pasto_total": buoni_pasto_total,
        "fixed_net": float(payroll_v2.fixed_net),
        "variables_gross": float(payroll_v2.variables_gross),
        "variables_net": float(payroll_v2.variables_net),
        "adjustment": float(payroll_v2.adjustment),
        "realistic_low": float(payroll_v2.realistic_low),
        "realistic_high": float(payroll_v2.realistic_high),
        "competence_month": payroll_v2.competence_month,
        "premiums_gross": float(payroll_v2.breakdown.premiums_gross),
        "allowances_gross": float(payroll_v2.breakdown.allowances_gross),
        "overtime_gross": float(payroll_v2.breakdown.overtime_gross),
        "sede_days_total": int(month_report.get("sede_days", 0)),
        "sede_days_required": int(month_report.get("sede_required", 0)),
        "sede_days_remaining": int(month_report.get("sede_remaining", 0)),
    }




def _turno_color_info(turno):
    mapping = {
        "Mattina": {"emoji": "🔵", "short": "M", "class": "turni-mattina", "color": "#60a5fa", "md_color": "blue"},
        "Pomeriggio": {"emoji": "🟠", "short": "P", "class": "turni-pomeriggio", "color": "#fb923c", "md_color": "orange"},
        "Notte": {"emoji": "⚫", "short": "N", "class": "turni-notte", "color": "#64748b", "md_color": "grey"},
        "Giornata": {"emoji": "🟣", "short": "G", "class": "turni-giornata", "color": "#c084fc", "md_color": "violet"},
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


def _change_turni_calendar_month(months):
    current_month = st.session_state.get("turni_calendar_month", _now_italy().date())
    st.session_state.turni_calendar_month = _add_months_turni(current_month, months)


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
    live_net_hourly = _live_net_hourly_base(df_turni, rules, month_key)
    for _, row in month_df.iterrows():
        calc = compute_turno_net_estimate(
            row["Data"],
            row["Turno"],
            bool(row["Festivo"]),
            rules,
            live_net_hourly,
            until=datetime.max.replace(tzinfo=None),
            straordinario_minuti=_turni_row_straordinario_minuti(row),
        )
        turni_total += float(calc.get("total", 0.0))
        turni_base += float(calc.get("base", 0.0))
        turni_extra += float(calc.get("extra", 0.0))
    # I buoni pasto restano un beneficio separato e i vecchi campi
    # accrediti/trattenute non appartengono al nuovo netto maturato dei turni.
    monthly_adjustments = 0.0
    return {
        **report,
        "turni_total": turni_total,
        "turni_base": turni_base,
        "turni_extra": turni_extra,
        "monthly_adjustments": monthly_adjustments,
        "month_total": turni_total + monthly_adjustments,
    }


def render_selected_month_turni_kpis(
    df_turni,
    rules,
    month_key,
    payroll_estimate,
    side_html="",
):
    month_date = datetime.strptime(f"{month_key}-01", "%Y-%m-%d").date()
    month_label = html.escape(_turni_month_label(month_date))
    summary = _turni_month_money_summary(df_turni, rules, month_key)
    storico_stipendio = _storico_stipendio_for_month(month_key)
    actual_value = "—" if storico_stipendio is None else _money_turni(storico_stipendio)
    actual_subline = "Netto accreditato da storico" if storico_stipendio is not None else "Cedolino reale non presente nello storico"
    if storico_stipendio is None:
        estimate_difference = "Differenza non disponibile senza il cedolino reale"
    else:
        delta = float(storico_stipendio) - float(payroll_estimate.credited_net)
        if abs(delta) < 0.005:
            estimate_difference = "Stima coincidente con il netto reale"
        else:
            estimate_difference = f"Differenza reale {_signed_money_turni(delta)} rispetto alla stima"
    work_days = int(summary.get("work_days", 0))
    ferie_days = int(summary.get("ferie_days", 0))
    total_days = work_days + ferie_days
    buoni = float(summary.get("buoni_pasto_total", 0.0))
    next_month_date = _add_months_turni(month_date, 1)
    next_month_label = _turni_month_label(next_month_date)
    side_block = f'<div class="turni-live-side">{side_html}</div>' if side_html else ""
    shell_class = "turni-static-shell has-side" if side_html else "turni-static-shell"
    component_height = 286 if (MOBILE_VIEW and side_html) else (330 if MOBILE_VIEW else 126)
    components.html(f"""
    <div class="{shell_class}">
      <div class="turni-live-grid">
        <div class="kpi-card" style="border-color:rgba(52,211,153,0.25);">
          <div class="kpi-label">{month_label} — cedolino reale</div>
          <div class="kpi-value" style="color:#34d399;">{actual_value}</div>
          <div class="turni-subline">{html.escape(actual_subline)}</div>
        </div>
        <div class="kpi-card" style="border-color:rgba(96,165,250,0.25);">
          <div class="kpi-label">Cedolino stimato</div>
          <div class="kpi-value" style="color:#60a5fa;">{_money_turni(payroll_estimate.credited_net)}</div>
          <div class="turni-subline">{html.escape(estimate_difference)}</div>
        </div>
        <div class="kpi-card" style="border-color:rgba(254,243,199,0.25);">
          <div class="kpi-label">Netto maturato dai turni</div>
          <div class="kpi-value" style="color:#fef3c7;">{_money_turni(summary["month_total"])}</div>
          <div class="turni-subline">{work_days} lavorati + {ferie_days} ferie = {total_days}</div>
          <div class="turni-subline">Variabili pagate in {html.escape(next_month_label)} · buoni separati {_money_turni(buoni)}</div>
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
        font-size: 22px;
        line-height: 1.08;
        font-weight: 600;
        white-space: nowrap;
      }}
      .turni-subline {{
        font-size: 12px;
        color: rgba(255,255,255,0.42);
        margin-top: 5px;
      }}
      .turni-live-side {{
        min-width: 0;
        position: relative;
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
      .turni-giornata {{ border-left-color:#c084fc; }}
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
    df["turno_priority"] = df["Turno"].map({"Mattina": 1, "Pomeriggio": 2, "Notte": 3, "Giornata": 4, "Ferie": 5}).fillna(9)
    df = df.sort_values(["Data", "turno_priority"]).drop_duplicates(subset=["Data"], keep="first")
    return _normalize_turni_df(df.drop(columns=["turno_priority"])), errors


def import_sede_dates_from_calendar_ics(ical_url, selected_month):
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

    month_key = selected_month.strftime("%Y-%m")
    sede_dates = set()
    for event in events:
        summary = event.get("SUMMARY", "").strip().lower()
        if "sede" not in summary:
            continue
        start = _parse_ics_datetime(event.get("DTSTART", ""))
        if not start:
            continue
        data_str = start.strftime("%Y-%m-%d")
        if data_str.startswith(month_key):
            sede_dates.add(data_str)
    return sede_dates


def import_sede_dates_from_calendar_sources(calendar_sources, selected_month):
    sede_dates = set()
    errors = []
    for source_name, ical_url in calendar_sources.items():
        if not ical_url:
            continue
        try:
            sede_dates.update(import_sede_dates_from_calendar_ics(ical_url, selected_month))
        except Exception as e:
            errors.append(f"{source_name}: {e}")
    return sede_dates, errors


def sync_turni_month_from_calendar(df_turni, calendar_sources, selected_month, sede_calendar_sources=None):
    imported, errors = import_turni_from_calendar_sources(calendar_sources, selected_month)
    sede_dates, sede_errors = import_sede_dates_from_calendar_sources(sede_calendar_sources or {}, selected_month)
    errors.extend(sede_errors)
    if imported.empty:
        return df_turni.copy(), 0, errors
    month_key = selected_month.strftime("%Y-%m")
    existing_month = df_turni[df_turni["Data"].str.startswith(month_key)].copy()
    if not existing_month.empty:
        existing_extra = existing_month.set_index("Data")[["Straordinario minuti"]].to_dict("index")
        for idx, row in imported.iterrows():
            extra = existing_extra.get(row["Data"])
            if extra:
                imported.at[idx, "Straordinario minuti"] = extra.get("Straordinario minuti", 0)
    # La sede deve riflettere esattamente il calendario a ogni sincronizzazione,
    # incluse le rimozioni degli eventi. Conserviamo soltanto gli straordinari
    # inseriti manualmente.
    imported["Sede"] = imported["Data"].isin(sede_dates)
    other_months = df_turni[~df_turni["Data"].str.startswith(month_key)].copy()
    manual_festivi = df_turni[
        df_turni["Data"].str.startswith(month_key)
        & (~df_turni["Turno"].isin(TURNI_ORARI.keys()) | (df_turni["Turno"] == ""))
        & (df_turni["Festivo"] == True)
    ].copy()
    manual_sedi = df_turni[
        df_turni["Data"].str.startswith(month_key)
        & (~df_turni["Turno"].isin(TURNI_ORARI.keys()) | (df_turni["Turno"] == ""))
        & (df_turni["Sede"] == True)
    ].copy()
    manual_sedi = manual_sedi[~manual_sedi["Data"].isin(imported["Data"])]
    synced = pd.concat([other_months, manual_festivi, manual_sedi, imported], ignore_index=True)
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


def _default_sede_calendar_ical_urls():
    urls = {source_name: url for source_name, url in CALENDAR_SEDE_ICAL_URLS.items() if url}
    try:
        secret_url = st.secrets.get("GOOGLE_CALENDAR_SEDE_ICAL_URL", "")
        if secret_url:
            urls["Sede"] = secret_url
        secret_urls = st.secrets.get("GOOGLE_CALENDAR_SEDE_ICAL_URLS", {})
        if hasattr(secret_urls, "items"):
            for source_name, url in secret_urls.items():
                if url:
                    urls[str(source_name)] = url
    except Exception:
        pass
    return urls


def ensure_turni_month_synced(selected_month, df_turni=None):
    """Sincronizza una sola volta il mese e restituisce la sorgente comune alla UI."""
    month_key = selected_month.strftime("%Y-%m")
    auto_sync_key = f"turni_calendar_autosync_sede_v2::{month_key}"
    current_df = load_turni_data() if df_turni is None else _normalize_turni_df(df_turni)
    if st.session_state.get(auto_sync_key, False):
        draft = st.session_state.get("turni_df_draft")
        return (_normalize_turni_df(draft) if draft is not None else current_df), []

    calendar_sources = _default_calendar_ical_urls()
    if not calendar_sources:
        st.session_state[auto_sync_key] = True
        return current_df, []
    synced_df, imported_count, errors = sync_turni_month_from_calendar(
        current_df,
        calendar_sources,
        selected_month,
        _default_sede_calendar_ical_urls(),
    )
    st.session_state[auto_sync_key] = True
    if imported_count > 0:
        st.session_state.turni_df_draft = synced_df.copy()
        st.session_state.turni_dirty = False
        return synced_df.copy(), errors
    return current_df, errors


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
    turno_kpi_label = str(stats.get("turno_kpi_label", "Turno — netto live / totale netto")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
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
          <div class="kpi-label">Mese corrente — netto maturato / cedolino stimato</div>
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
          <div class="turni-subline">Valori netti stimati da fisso e maggiorazioni</div>
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
        font-size: 22px;
        line-height: 1.08;
        font-weight: 600;
        white-space: nowrap;
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
      .turni-giornata {{ border-left-color:#c084fc; }}
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
          position: static;
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


def render_payroll_v2_details(estimate, adjustment_description=""):
    adjustment_label = _signed_money_turni(estimate.adjustment)
    adjustment_formula = (
        f"+ {_money_turni(estimate.adjustment)}"
        if float(estimate.adjustment) >= 0
        else f"− {_money_turni(abs(float(estimate.adjustment)))}"
    )
    spread = max(0.0, float(estimate.credited_net) - float(estimate.realistic_low))
    cards = [
        (
            "Netto cedolino stimato",
            _money_turni(estimate.credited_net),
            f"{_money_turni(estimate.fixed_net)} fisso + {_money_turni(estimate.variables_net)} variabili {adjustment_formula} rettifica",
            "#34d399",
            "16,185,129",
        ),
        (
            "Intervallo realistico",
            f"{_money_turni(estimate.realistic_low)} – {_money_turni(estimate.realistic_high)}",
            f"Stima ± {_money_turni(spread)} di errore medio storico",
            "#34d399",
            "16,185,129",
        ),
        (
            f"Variabili lorde {estimate.competence_month}",
            _money_turni(estimate.variables_gross),
            f"Maturate in {estimate.competence_month}, pagate in {estimate.month}",
            "#60a5fa",
            "59,130,246",
        ),
        (
            "Variabili nette stimate",
            _money_turni(estimate.variables_net),
            "Variabili lorde × coefficiente netto calibrato",
            "#60a5fa",
            "59,130,246",
        ),
        ("Fisso netto", _money_turni(estimate.fixed_net), "Quota ordinaria mensile calibrata", "#a78bfa", "139,92,246"),
        ("Buoni pasto separati", _money_turni(estimate.meal_vouchers), "Non inclusi nel netto accreditato", "#a78bfa", "139,92,246"),
        (
            "Rettifica del mese",
            adjustment_label,
            adjustment_description or "Nessuna rettifica registrata",
            "#fb923c",
            "249,115,22",
        ),
        (
            "Componenti lorde: mag. / indenn. / straord.",
            f"{_money_turni(estimate.breakdown.premiums_gross)} / {_money_turni(estimate.breakdown.allowances_gross)} / {_money_turni(estimate.breakdown.overtime_gross)}",
            "Dettaglio lordo già compreso nelle variabili",
            "#fb923c",
            "249,115,22",
        ),
    ]
    cards_html = "".join(
        f'<div class="payroll-v2-card" style="--card-color:{color};--card-rgb:{rgb};">'
        f'<div class="payroll-v2-label">{html.escape(label)}</div>'
        f'<div class="payroll-v2-value">{html.escape(value)}</div>'
        f'<div class="payroll-v2-sub">{html.escape(subline)}</div>'
        '</div>'
        for label, value, subline, color, rgb in cards
    )
    st.markdown(f"""
    <style>
      .payroll-v2-heading {{
        width:100%; margin:2px 0 10px; font-size:18px; font-weight:850;
        color:rgba(255,255,255,.92);
      }}
      .payroll-v2-grid {{
        display:grid; grid-template-columns:repeat(4,minmax(0,1fr));
        gap:10px; width:100%; align-items:stretch;
      }}
      .payroll-v2-card {{
        min-width:0; min-height:108px; box-sizing:border-box;
        display:flex; flex-direction:column; justify-content:center;
        padding:10px 12px; border-radius:13px;
        border:1px solid rgba(var(--card-rgb),.34);
        background:linear-gradient(145deg,rgba(var(--card-rgb),.16),rgba(15,23,42,.84));
        box-shadow:0 10px 24px rgba(0,0,0,.16);
      }}
      .payroll-v2-label {{
        min-height:25px; color:rgba(255,255,255,.58); font-size:11px;
        font-weight:750; letter-spacing:.45px; line-height:1.28;
        text-transform:uppercase;
      }}
      .payroll-v2-value {{
        color:var(--card-color); font-size:20px; line-height:1.18;
        font-weight:750; overflow-wrap:anywhere;
      }}
      .payroll-v2-sub {{
        min-height:0; margin-top:5px; color:rgba(255,255,255,.48);
        font-size:10px; line-height:1.3; overflow-wrap:anywhere;
      }}
      @media (max-width:767px) {{
        .payroll-v2-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); gap:7px; }}
        .payroll-v2-card {{ min-height:94px; padding:8px 9px; }}
        .payroll-v2-label {{ min-height:23px; font-size:9px; letter-spacing:.3px; }}
        .payroll-v2-value {{ font-size:15px; }}
        .payroll-v2-sub {{ min-height:0; margin-top:4px; font-size:8.5px; line-height:1.25; }}
      }}
    </style>
    <div class="payroll-v2-heading">🧾 Previsione cedolino</div>
    <div class="payroll-v2-grid">{cards_html}</div>
    """, unsafe_allow_html=True)
    st.caption(
        "Formula: fisso netto + (maggiorazioni, indennità e straordinari lordi "
        "del mese di competenza × coefficiente netto variabili) + rettifica. "
        "Le ore ordinarie e i buoni pasto non vengono sommati al netto."
    )


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
    live_net_hourly = _live_net_hourly_base(df_turni, rules, month_key)
    for _, r in month_df.iterrows():
        turno = r["Turno"]
        info = _turno_color_info(turno)
        stra_minuti = _turni_row_straordinario_minuti(r)
        sede = _turni_row_sede(r)
        calc = compute_turno_net_estimate(
            r["Data"],
            turno,
            bool(r["Festivo"]),
            rules,
            live_net_hourly,
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
            f'<div class="meta">{html.escape(seg)} · Netto stimato {html.escape(_money_turni(calc["total"]))}</div>'
            f'<div class="meta">Base netta {html.escape(_money_turni(calc["base"]))} · Variabili nette: Mag. {html.escape(_money_turni(calc.get("premium_net", 0)))} + indenn. {html.escape(_money_turni(calc.get("allowance_net", 0)))} + straord. {html.escape(_money_turni(calc.get("overtime_net", 0)))}</div>'
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

    turno_esistente, festivo_esistente, stra_esistente, _sede_esistente = _existing_turni_row_values(df_turni, action_day)
    overtime_allowed = turno_esistente not in {"", "Ferie", "Riposo"}
    durata_options = [0, 30, 45, 60, 75, 90, 105, 120]
    durata_default = (
        min(durata_options, key=lambda value: abs(value - int(stra_esistente or 0)))
        if overtime_allowed
        else 0
    )
    action_day_label = pd.to_datetime(action_day).strftime("%d/%m/%Y")
    turno_label = f" · {turno_esistente}" if turno_esistente else ""

    st.markdown(f"#### Modifica giorno · {action_day_label}{turno_label}")
    with st.form(f"turni_day_action_form_{action_day}", clear_on_submit=False):
        menu_cols = st.columns(2, gap="small")
        with menu_cols[0]:
            st.markdown('<span class="turni-day-menu-marker"></span>', unsafe_allow_html=True)
            festivo_value = st.checkbox(
                "Festivo",
                value=bool(festivo_esistente),
                key=f"turni_day_festivo_{action_day}",
            )
        with menu_cols[1]:
            durata_value = st.selectbox(
                "Straordinario",
                durata_options,
                index=durata_options.index(durata_default),
                format_func=lambda value: "No" if value == 0 else _format_minutes_label(value),
                key=f"turni_day_stra_{action_day}",
                disabled=not overtime_allowed,
                help="Lo straordinario non è previsto durante ferie o riposo.",
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


def _render_turni_report(report, previous_report=None, current_month_label="Corr.", previous_month_label="Prec."):
    previous_report = previous_report or {}
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
    turn_colors = {
        "Mattina": "#60a5fa",
        "Pomeriggio": "#fb923c",
        "Notte": "#64748b",
        "Giornata": "#c084fc",
        "Ferie": "#34d399",
    }
    previous_turn_counts = previous_report.get("turn_counts", {})
    type_counts = report.get("allowance_turn_type_counts", {})
    previous_type_counts = previous_report.get("allowance_turn_type_counts", {})
    allowance_order = [
        "Mattina feriale", "Mattina festivo",
        "Pomeriggio feriale", "Pomeriggio festivo",
        "Notte feriale", "Notte festivo",
    ]
    present_type_names = set(type_counts) | set(previous_type_counts)
    type_names = [name for name in allowance_order if name in present_type_names]
    type_names.extend(sorted(present_type_names - set(allowance_order)))
    compare_header = (
        '<div class="turni-report-compare-head"><span></span>'
        f'<b>{html.escape(str(current_month_label))}</b>'
        f'<b>{html.escape(str(previous_month_label))}</b></div>'
    )
    turn_order = ["Mattina", "Pomeriggio", "Notte", "Giornata", "Ferie"]
    present_turn_names = set(turn_counts) | set(previous_turn_counts)
    turn_names = [name for name in turn_order if name in present_turn_names]
    turn_names.extend(sorted(present_turn_names - set(turn_order)))
    turn_rows = "".join(
        f'<div class="turni-report-compare-row" style="--turn-color:{turn_colors.get(str(name), "#fef3c7")};">'
        f'<span>{html.escape(str(name))}</span>'
        f'<strong>{int(turn_counts.get(name, 0))}</strong>'
        f'<strong>{int(previous_turn_counts.get(name, 0))}</strong></div>'
        for name in turn_names
    ) or '<div class="turni-report-compare-row"><span>Nessun turno</span><strong>0</strong><strong>0</strong></div>'
    type_rows = "".join(
        f'<div class="turni-report-compare-row"><span>{html.escape(str(name))}</span>'
        f'<strong>{int(type_counts.get(name, 0))}</strong>'
        f'<strong>{int(previous_type_counts.get(name, 0))}</strong></div>'
        for name in type_names
    ) or '<div class="turni-report-compare-row"><span>Nessun dettaglio</span><strong>0</strong><strong>0</strong></div>'
    hours_by_pct = report.get("hours_by_pct", {})
    previous_hours_by_pct = previous_report.get("hours_by_pct", {})
    hour_percentages = sorted(set(hours_by_pct) | set(previous_hours_by_pct))
    hours_rows = "".join(
        f'<div class="turni-report-compare-row"><span>Magg. {float(pct):g}%</span>'
        f'<strong>{float(hours_by_pct.get(pct, 0.0)):.2f}h</strong>'
        f'<strong>{float(previous_hours_by_pct.get(pct, 0.0)):.2f}h</strong></div>'
        for pct in hour_percentages
        if abs(float(hours_by_pct.get(pct, 0.0))) > 0.001 or abs(float(previous_hours_by_pct.get(pct, 0.0))) > 0.001
    ) or '<div class="turni-report-compare-row"><span>Nessuna maggiorazione</span><strong>0h</strong><strong>0h</strong></div>'
    straordinario_hours_by_pct = report.get("straordinario_hours_by_pct", {})
    previous_straordinario_hours_by_pct = previous_report.get("straordinario_hours_by_pct", {})
    straordinario_percentages = sorted(set(straordinario_hours_by_pct) | set(previous_straordinario_hours_by_pct))
    straordinario_rows = "".join(
        f'<div class="turni-report-compare-row turni-report-straordinario"><span>Straord. {float(pct):g}%</span>'
        f'<strong>{float(straordinario_hours_by_pct.get(pct, 0.0)):.2f}h</strong>'
        f'<strong>{float(previous_straordinario_hours_by_pct.get(pct, 0.0)):.2f}h</strong></div>'
        for pct in straordinario_percentages
        if abs(float(straordinario_hours_by_pct.get(pct, 0.0))) > 0.001 or abs(float(previous_straordinario_hours_by_pct.get(pct, 0.0))) > 0.001
    )
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
      .turni-report-list h4 + div {{
        border-top:0 !important;
      }}
      .turni-report-list strong {{
        color:#fef3c7;
      }}
      .turni-report-list div[style*="--turn-color"] span,
      .turni-report-list div[style*="--turn-color"] strong {{
        color:var(--turn-color);
      }}
      .turni-report-compare-head,
      .turni-report-compare-row {{
        display:grid !important;
        grid-template-columns:minmax(0,1fr) 44px 44px;
        align-items:center;
        column-gap:0;
      }}
      .turni-report-compare-head {{
        padding:0 0 4px !important;
        border-top:0 !important;
        color:rgba(255,255,255,.40) !important;
        font-size:9px !important;
        text-transform:uppercase;
      }}
      .turni-report-compare-head b {{
        font-weight:800;
        text-align:center;
        white-space:normal;
        line-height:1.1;
      }}
      .turni-report-compare-row span {{
        padding-right:5px;
      }}
      .turni-report-compare-row strong {{
        text-align:center;
        white-space:nowrap;
      }}
      .turni-report-compare-head b:nth-child(3),
      .turni-report-compare-row strong:nth-child(3) {{
        border-left:1px solid rgba(148,163,184,.32);
      }}
      .turni-report-straordinario span,
      .turni-report-straordinario strong {{
        color:#c084fc !important;
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
      <div class="turni-report-list"><h4>Turni</h4>{compare_header}{turn_rows}</div>
      <div class="turni-report-list"><h4>Indennità</h4>{compare_header}{type_rows}</div>
      <div class="turni-report-list"><h4>Ore maggiorazione</h4>{compare_header}{hours_rows}{straordinario_rows}</div>
    </div>
    """, unsafe_allow_html=True)


def render_turni_guadagni_section():
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
    st.markdown(
        f"""
        <div id="mobile-turni" class="mobile-anchor"></div>
        <div style="margin:0 0 14px;text-align:center;font-size:25px;font-weight:900;color:rgba(255,255,255,.94);">
          {_turni_month_label(selected_month)}
        </div>
        <div class="section-pill">⏱️ Guadagni Turni</div>
        """,
        unsafe_allow_html=True,
    )

    df_turni, calendar_errors = ensure_turni_month_synced(selected_month)
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
    selected_payroll_estimate = _payroll_estimate_for_month(df_turni, rules, month_key)
    selected_adjustment_description = payroll_adjustment_for_month(month_key).get(
        "description",
        "",
    )
    if is_selected_current_month:
        render_live_turni_kpis(stats, mobile_summary_html)
    else:
        render_selected_month_turni_kpis(
            df_turni,
            rules,
            month_key,
            selected_payroll_estimate,
            mobile_summary_html,
        )
    render_payroll_v2_details(
        selected_payroll_estimate,
        selected_adjustment_description,
    )

    tab_cal, tab_rules, tab_report, tab_calibration = st.tabs(
        ["📅 Turni", "⚙️ Regole", "📊 Riepilogo", "🎯 Calibrazione"]
    )

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
                    st.button(
                        "←",
                        key="turni_prev_month",
                        use_container_width=True,
                        on_click=_change_turni_calendar_month,
                        args=(-1,),
                    )
                with title_col:
                    st.markdown(f"#### 📅 Calendario · {_turni_month_label(selected_month)}")
                with next_col:
                    st.button(
                        "→",
                        key="turni_next_month",
                        use_container_width=True,
                        on_click=_change_turni_calendar_month,
                        args=(1,),
                    )
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
              <span class="legend-item legend-shift" style="border-bottom-color:#60a5fa;">Mattina</span>
              <span class="legend-item legend-shift" style="border-bottom-color:#fb923c;">Pomeriggio</span>
              <span class="legend-item legend-shift" style="border-bottom-color:#64748b;">Notte</span>
              <span class="legend-item legend-shift" style="border-bottom-color:#c084fc;">Giornata</span>
              <span class="legend-item legend-shift" style="border-bottom-color:#34d399;">Ferie</span>
              <span class="legend-sep"></span>
              <span class="legend-item legend-muted"><span style="color:#ef4444;font-weight:900;">Numero rosso</span> = festivo</span>
              <span class="legend-item legend-muted"><span class="legend-current">•</span> Giorno corrente</span>
              <span class="legend-item legend-muted"><span class="mobile-day-sede">S</span> Sede</span>
              <span class="legend-item legend-muted"><span class="mobile-day-extra">+</span> Straordinario</span>
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
                live_net_hourly = _live_net_hourly_base(df_turni, rules, month_key)
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
                    calc = compute_turno_net_estimate(
                        r["Data"],
                        turno,
                        bool(r["Festivo"]),
                        rules,
                        live_net_hourly,
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
                        f'<div class="meta">{seg} · Netto stimato {_money_turni(calc["total"])}</div>'
                        f'<div class="meta">Base netta {_money_turni(calc["base"])} · Variabili nette: Mag. {_money_turni(calc.get("premium_net", 0))} + indenn. {_money_turni(calc.get("allowance_net", 0))} + straord. {_money_turni(calc.get("overtime_net", 0))}</div>'
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
                  .turni-giornata {{ border-left-color:#c084fc; }}
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
        # I parametri che governano il cedolino sono raggruppati qui,
        # prima delle regole tecniche dei turni, per essere immediatamente
        # individuabili anche da smartphone.
        if float(rules.get("paga_oraria_lorda", 0.0)) <= 0:
            rules["paga_oraria_lorda"] = PAYROLL_V2_DEFAULTS["paga_oraria_lorda"]
            rules["paga_oraria"] = rules["paga_oraria_lorda"]
            if "turni_paga_lorda" in st.session_state:
                st.session_state["turni_paga_lorda"] = rules["paga_oraria_lorda"]
        st.markdown("""
        <div style="
            margin:0 0 12px;
            padding:12px 14px;
            border:1px solid rgba(52,211,153,.30);
            border-radius:14px;
            background:linear-gradient(135deg,rgba(16,185,129,.13),rgba(59,130,246,.08));
        ">
          <div style="font-size:15px;font-weight:900;color:#6ee7b7;">🧾 Parametri cedolino</div>
          <div style="font-size:11px;color:rgba(255,255,255,.58);margin-top:4px;">
            Questi parametri determinano la previsione. La rettifica è salvata sul singolo mese del cedolino.
          </div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("""
        <style>
        div[data-testid="stHorizontalBlock"]:has(.payroll-rules-grid-marker) {
          display:grid !important;
          grid-template-columns:repeat(2,minmax(0,1fr)) !important;
          gap:8px !important;
          align-items:start !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.payroll-rules-grid-marker)
          > div[data-testid="stColumn"] {
          width:auto !important; min-width:0 !important; max-width:100% !important;
          flex:initial !important;
        }
        div[data-testid="stElementContainer"]:has(.payroll-rules-grid-marker) {
          display:none !important;
        }
        @media (max-width:767px) {
          div[data-testid="stHorizontalBlock"]:has(.payroll-rules-grid-marker)
            [data-testid="stNumberInput"] label p { font-size:10px !important; }
          div[data-testid="stHorizontalBlock"]:has(.payroll-rules-grid-marker)
            [data-testid="stNumberInput"] input { font-size:13px !important; }
        }
        </style>
        """, unsafe_allow_html=True)
        v2_row_1 = st.columns(2)
        with v2_row_1[0]:
            st.markdown('<span class="payroll-rules-grid-marker"></span>', unsafe_allow_html=True)
            rules["paga_oraria_lorda"] = st.number_input(
                "Paga oraria lorda contrattuale",
                min_value=0.01,
                value=float(rules.get("paga_oraria_lorda", 18.01988)),
                step=0.01,
                format="%.5f",
                key="turni_paga_lorda",
                help="Serve solo a calcolare maggiorazioni, indennità e straordinari lordi.",
            )
        with v2_row_1[1]:
            rules["netto_fisso_mensile"] = st.number_input(
                "Netto fisso mensile",
                min_value=0.0,
                value=float(rules.get("netto_fisso_mensile", 2200.0)),
                step=10.0,
                key="turni_netto_fisso",
                help="La parte ordinaria netta che non dipende dalle ore del mese.",
            )
        v2_row_2 = st.columns(3)
        with v2_row_2[0]:
            st.markdown('<span class="payroll-rules-grid-marker"></span>', unsafe_allow_html=True)
            rules["coefficiente_netto_variabili"] = st.number_input(
                "Coefficiente netto variabili",
                min_value=0.0,
                max_value=1.0,
                value=float(rules.get("coefficiente_netto_variabili", 0.60)),
                step=0.01,
                key="turni_coeff_variabili",
                help="Trasforma le variabili lorde in una stima netta.",
            )
        with v2_row_2[1]:
            rules["ritardo_competenze_mesi"] = st.number_input(
                "Ritardo competenze (mesi)",
                min_value=0,
                max_value=3,
                value=int(round(rules.get("ritardo_competenze_mesi", 1))),
                step=1,
                key="turni_ritardo_competenze",
                help="Normalmente 1: le variabili maturate nel mese M sono pagate in M+1.",
            )
        with v2_row_2[2]:
            rules["finestra_calibrazione_mesi"] = st.number_input(
                "Finestra calibrazione (mesi)",
                min_value=3,
                max_value=36,
                value=int(round(rules.get("finestra_calibrazione_mesi", 12))),
                step=1,
                key="turni_finestra_calibrazione",
                help="Dà priorità al livello retributivo recente: i mesi più vecchi restano visibili ma sono esclusi automaticamente dal fit.",
            )
        adjustment_rows = load_payroll_adjustments()
        selected_adjustment = payroll_adjustment_for_month(month_key, adjustment_rows)
        st.markdown(f"##### Rettifica cedolino · {_turni_month_label(selected_month)}")
        v2_row_3 = st.columns(2)
        with v2_row_3[0]:
            st.markdown('<span class="payroll-rules-grid-marker"></span>', unsafe_allow_html=True)
            monthly_adjustment = st.number_input(
                "Importo netto (+ rimborso / − trattenuta)",
                value=float(selected_adjustment.get("amount", DEFAULT_PAYROLL_ADJUSTMENT)),
                step=10.0,
                key=f"turni_rettifica_mese::{month_key}",
                help="Importo netto: 730, premi, arretrati o trattenute del mese selezionato.",
            )
        with v2_row_3[1]:
            monthly_adjustment_note = st.text_input(
                "Descrizione",
                value=str(selected_adjustment.get("description", DEFAULT_PAYROLL_ADJUSTMENT_DESCRIPTION)),
                key=f"turni_rettifica_nota::{month_key}",
                placeholder="es. rimborso 730",
            )
        if st.button(
            f"💾 Salva rettifica per {_turni_month_label(selected_month)}",
            key=f"save_payroll_adjustment::{month_key}",
            use_container_width=True,
        ):
            if save_payroll_adjustment(month_key, monthly_adjustment, monthly_adjustment_note):
                st.success("Rettifica salvata. Previsione e calibrazione sono state aggiornate.")
                st.rerun()
            else:
                st.error("Non sono riuscito a salvare la rettifica su Google Sheets.")
        rules["paga_oraria"] = rules["paga_oraria_lorda"]
        st.caption(
            "Valori iniziali consigliati: paga lorda 18,01988 €/h, fisso netto "
            "2.200 €, coefficiente 0,60 e ritardo 1 mese."
        )
        st.info(
            "Per correggere un cedolino passato, torna al suo mese con le frecce del "
            "calendario e salva qui l’importo esatto: positivo se aumenta il netto, "
            "negativo se lo riduce. La calibrazione sottrae la rettifica prima di "
            "imparare fisso e coefficiente, quindi quel mese può restare incluso."
        )
        st.markdown("---")

        c1, c2 = st.columns(2)
        with c1:
            if MOBILE_VIEW:
                st.markdown('<span class="turni-rules-marker"></span>', unsafe_allow_html=True)
            st.markdown("""
            <div style="margin:0 0 12px; padding-top:0;">
              <h5 style="margin:0;color:#93c5fd;">Maggiorazioni</h5>
            </div>
            """, unsafe_allow_html=True)
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
            with stra_cols[1]:
                rules["stra_mattina_festivo_pct"] = st.number_input("M festivo %", value=float(rules.get("stra_mattina_festivo_pct", 55.0)), step=1.0, key="turni_stra_m_festivo")
                rules["stra_pomeriggio_festivo_pct"] = st.number_input("P festivo %", value=float(rules.get("stra_pomeriggio_festivo_pct", 60.0)), step=1.0, key="turni_stra_p_festivo")
                rules["stra_notte_festivo_pct"] = st.number_input("N festivo %", value=float(rules.get("stra_notte_festivo_pct", 70.0)), step=1.0, key="turni_stra_n_festivo")
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
              <h5 style="margin:0;color:#34d399;">Sede e buoni pasto</h5>
            </div>
            """, unsafe_allow_html=True)
            rules["buono_pasto"] = st.number_input("Buono pasto", value=float(rules.get("buono_pasto", 7.0)), step=0.50, key="turni_buono_pasto")
            rules["smart_target"] = st.number_input("Smart target mensile", value=float(rules.get("smart_target", 15.0)), step=1.0, key="turni_smart_target")
            st.markdown(f"""
            <div class="kpi-card">
                <div class="kpi-label">Regole applicate</div>
                <div style="font-size:12px;color:rgba(255,255,255,0.72);line-height:1.55;">
                <b style="color:#fef3c7;">Paga lorda contrattuale:</b> {_money_turni(rules['paga_oraria_lorda'])}/h<br>
                <b style="color:#34d399;">Fisso netto:</b> {_money_turni(rules['netto_fisso_mensile'])}; variabili × {rules['coefficiente_netto_variabili']:.2f}; ritardo {rules['ritardo_competenze_mesi']:g} mese/i.<br>
                <b style="color:#93c5fd;">Mattina 06–14:</b> feriale {rules['m_p_feriale_pct']:g}%, festivo {rules['m_p_festivo_giorno_pct']:g}%. Sabato: nessuna maggiorazione.<br>
                <b style="color:#fb923c;">Pomeriggio 14–22:</b> feriale {rules['m_p_feriale_pct']:g}%; festivo 14–18 {rules['m_p_festivo_giorno_pct']:g}% e 18–22 {rules['festivo_sera_notte_pct']:g}%. Sabato: 14–18 senza maggiorazione, 18–22 {rules['m_p_feriale_pct']:g}%.<br>
                <b style="color:#94a3b8;">Notte 22–06:</b> {rules['notte_feriale_pct']:g}% feriale e {rules['festivo_sera_notte_pct']:g}% festivo; le ore sono attribuite al giorno effettivo, anche dopo mezzanotte.<br>
                <b style="color:#fef3c7;">Indennità V2:</b> M/P {_money_turni(rules['ind_m_p_feriale'])} feriale / {_money_turni(rules['ind_m_p_festivo'])} festivo; Notte {_money_turni(rules['ind_notte_feriale'])} feriale / {_money_turni(rules['ind_notte_festiva'])} festiva.<br>
                <b style="color:#c084fc;">Straordinari:</b> massimo 2 ore dopo il turno. M {rules['stra_mattina_feriale_pct']:g}%/{rules['stra_mattina_festivo_pct']:g}%, P {rules['stra_pomeriggio_feriale_pct']:g}%/{rules['stra_pomeriggio_festivo_pct']:g}%, N {rules['stra_notte_feriale_pct']:g}%/{rules['stra_notte_festivo_pct']:g}% (feriale/festivo).<br>
                <b style="color:#34d399;">Ferie:</b> 8 ore base. <b style="color:#fde68a;">Buono pasto:</b> {_money_turni(rules['buono_pasto'])}, se in sede e non mattina feriale.<br>
                <b style="color:#fb923c;">Sede:</b> target Smart {rules['smart_target']:g} giorni/mese; sedi richieste = giorni lavorati − target Smart.
                </div>
            </div>
            """, unsafe_allow_html=True)
            if st.button("💾 Salva regole su Google", key="save_turni_rules_google", use_container_width=True):
                rules = _apply_turni_rules_from_widgets(rules)
                if save_turni_rules(rules):
                    st.success("Regole salvate nel foglio Google “Regole Turni”.")
                else:
                    st.error("Non sono riuscito a salvare le regole su Google Sheets.")

        st.session_state.turni_rules = rules
        st.caption("Le regole restano attive subito nella sessione. Con “Salva regole su Google” vengono conservate nel foglio “Regole Turni” e ricaricate al prossimo accesso.")

    with tab_report:
        month_report = compute_turni_month_report(df_turni, rules, month_key)
        previous_month_key = _add_months_turni(selected_month, -1).strftime("%Y-%m")
        previous_month_report = compute_turni_month_report(df_turni, rules, previous_month_key)
        current_month_label = f"{_turni_month_label(selected_month).split()[0]} corr."
        previous_month_label = f"{_turni_month_label(_add_months_turni(selected_month, -1)).split()[0]} prec."
        _render_turni_report(month_report, previous_month_report, current_month_label, previous_month_label)

    with tab_calibration:
        st.markdown("### Calibrazione su storico reale")
        st.caption(
            "Ogni cedolino viene abbinato alle variabili maturate nel mese precedente. "
            "Le rettifiche mensili registrate vengono neutralizzate prima del calcolo. "
            f"Il modello usa automaticamente gli ultimi {int(round(rules.get('finestra_calibrazione_mesi', 12)))} mesi, "
            "così gli aumenti recenti pesano più dello storico remoto. Tredicesima, premi elevati e anomalie "
            "sono esclusi; la colonna “Includi” consente comunque di correggere ogni scelta."
        )
        _render_payslip_pdf_review(month_key)
        try:
            with st.expander("📥 Importa storico turni dal prototipo Excel", expanded=False):
                uploaded_turni_excel = st.file_uploader(
                    "File Turni guadagni.xlsx",
                    type=["xlsx"],
                    key="turni_excel_history_upload",
                    help="Legge i fogli mensili e unisce lo storico senza sovrascrivere le date già presenti su Google.",
                )
                imported_history = None
                if uploaded_turni_excel is not None:
                    imported_history = read_turni_excel(uploaded_turni_excel)
                if imported_history is not None:
                    existing_dates = set(_normalize_turni_df(df_turni)["Data"].astype(str))
                    imported_dates = set(imported_history["Data"].astype(str))
                    new_dates = imported_dates - existing_dates
                    imported_months = sorted(imported_history["Data"].str[:7].unique()) if not imported_history.empty else []
                    st.info(
                        f"Rilevati {len(imported_history)} turni in {len(imported_months)} mesi "
                        f"({imported_months[0] if imported_months else '—'} → {imported_months[-1] if imported_months else '—'}). "
                        f"Nuove date da aggiungere: {len(new_dates)}."
                    )
                    if st.button(
                        "✅ Unisci e salva lo storico su Google",
                        key="save_imported_turni_history",
                        use_container_width=True,
                        disabled=imported_history.empty,
                    ):
                        fresh_existing = load_turni_data(force_reload=True)
                        merged_history = merge_turni_history(fresh_existing, imported_history)
                        if save_turni_data(merged_history):
                            st.session_state.pop("payroll_calibration_editor", None)
                            st.success(
                                f"Storico importato: {len(merged_history)} giornate complessive; "
                                "le date già presenti su Google sono state conservate."
                            )
                            st.rerun()
                        else:
                            st.error("Importazione pronta, ma il salvataggio su Google Sheets non è riuscito.")
            refresh_calibration = st.button(
                "🔄 Aggiorna cedolini e turni da Google",
                key="refresh_payroll_calibration",
                use_container_width=True,
                help="Forza una nuova lettura del foglio Stipendi e del foglio TurniGuadagni.",
            )
            salary_df = load_data_gsheets(
                "Stipendi",
                STIPENDI_HEADERS,
                force_reload=refresh_calibration,
            )
            calibration_turni_df = (
                load_turni_data(force_reload=True) if refresh_calibration else df_turni
            )
            salaries = {}
            if salary_df is not None and not salary_df.empty:
                for _, salary_row in salary_df.iterrows():
                    salary_month = pd.to_datetime(salary_row.get("Mese"), errors="coerce")
                    salary_value = _parse_float_turni(salary_row.get("Stipendio", 0.0))
                    if pd.notna(salary_month) and salary_value > 0:
                        salaries[salary_month.strftime("%Y-%m")] = salary_value
            variables_by_month = _payroll_variables_by_month(calibration_turni_df, rules)
            calibration_adjustment_rows = load_payroll_adjustments(
                force_reload=refresh_calibration,
            )
            calibration_adjustments = {
                month: float(values.get("amount", 0.0))
                for month, values in calibration_adjustment_rows.items()
            }
            for salary_month in salaries:
                calibration_adjustments.setdefault(
                    salary_month,
                    DEFAULT_PAYROLL_ADJUSTMENT,
                )
            delay_months = int(round(rules.get("ritardo_competenze_mesi", 1)))
            matched_salary_months = {
                month
                for month in salaries
                if add_payroll_months(month, -delay_months) in variables_by_month
            }
            missing_salary_months = sorted(set(salaries) - matched_salary_months)
            status_cards = [
                ("Cedolini letti", str(len(salaries)), "#60a5fa", "59,130,246"),
                ("Cedolini abbinati", str(len(matched_salary_months)), "#34d399", "16,185,129"),
                ("Senza turni precedenti", str(len(missing_salary_months)), "#fb923c", "249,115,22"),
                ("Mesi variabili disponibili", str(len(variables_by_month)), "#a78bfa", "139,92,246"),
            ]
            status_html = "".join(
                f'<div class="calibration-status-card" style="--cal-color:{color};--cal-rgb:{rgb};">'
                f'<div class="calibration-status-label">{html.escape(label)}</div>'
                f'<div class="calibration-status-value">{html.escape(value)}</div>'
                '</div>'
                for label, value, color, rgb in status_cards
            )
            st.markdown(f"""
            <style>
              .calibration-status-grid,.calibration-result-grid {{
                display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
                gap:8px; width:100%; margin:8px 0 12px;
              }}
              .calibration-status-card,.calibration-result-card {{
                min-width:0; min-height:92px; box-sizing:border-box;
                display:flex; flex-direction:column; justify-content:center;
                padding:11px 12px; border-radius:13px;
                border:1px solid rgba(var(--cal-rgb),.33);
                background:linear-gradient(145deg,rgba(var(--cal-rgb),.15),rgba(15,23,42,.84));
              }}
              .calibration-status-label,.calibration-result-label {{
                min-height:27px; color:rgba(255,255,255,.58); font-size:10px;
                font-weight:750; letter-spacing:.4px; line-height:1.25;
                text-transform:uppercase;
              }}
              .calibration-status-value,.calibration-result-value {{
                color:var(--cal-color); font-size:20px; font-weight:780; line-height:1.15;
                overflow-wrap:anywhere;
              }}
              @media (max-width:767px) {{
                .calibration-status-card,.calibration-result-card {{ min-height:86px; padding:9px; }}
                .calibration-status-label,.calibration-result-label {{ font-size:9px; }}
                .calibration-status-value,.calibration-result-value {{ font-size:16px; }}
              }}
            </style>
            <div class="calibration-status-grid">{status_html}</div>
            """, unsafe_allow_html=True)
            if refresh_calibration:
                st.success("Cedolini e turni riletti da Google Sheets.")
            if missing_salary_months:
                missing_rows = pd.DataFrame([
                    {
                        "Mese cedolino": month,
                        "Netto reale": salaries[month],
                        "Servono i turni di": add_payroll_months(month, -delay_months),
                    }
                    for month in missing_salary_months
                ])
                with st.expander(
                    f"Cedolini letti ma non calibrabili ({len(missing_salary_months)})",
                    expanded=False,
                ):
                    st.dataframe(missing_rows, hide_index=True, use_container_width=True)
                    st.caption(
                        "Questi cedolini sono stati letti correttamente. Per usarli nella calibrazione "
                        "devi aggiungere o importare i turni del mese indicato in TurniGuadagni."
                    )
            automatic = calibrate_payroll(
                salaries,
                variables_by_month,
                delay=delay_months,
                adjustments=calibration_adjustments,
                recency_months=int(round(rules.get("finestra_calibrazione_mesi", 12))),
            )
            calibration_df = pd.DataFrame([{
                "Mese cedolino": row.month,
                "Mese variabili": row.variables_month,
                "Netto reale": row.actual_net,
                "Rettifica": row.adjustment,
                "Variabili lorde": row.variables_gross,
                "Netto stimato": row.estimated_net,
                "Scarto reale − stima": row.actual_net - row.estimated_net,
                "Errore assoluto": row.absolute_error,
                "Errore %": row.percentage_error,
                "Includi": row.included,
                "Motivo esclusione": row.exclusion_reason,
            } for row in automatic.rows])
            edited = st.data_editor(
                calibration_df,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Netto reale": st.column_config.NumberColumn(format="€ %.0f"),
                    "Rettifica": st.column_config.NumberColumn(format="€ %.0f"),
                    "Variabili lorde": st.column_config.NumberColumn(format="€ %.1f"),
                    "Netto stimato": st.column_config.NumberColumn(format="€ %.1f"),
                    "Scarto reale − stima": st.column_config.NumberColumn(format="€ %.1f"),
                    "Errore assoluto": st.column_config.NumberColumn(format="€ %.1f"),
                    "Errore %": st.column_config.NumberColumn(format="%.1f%%"),
                },
                disabled=[col for col in calibration_df.columns if col != "Includi"],
                key="payroll_calibration_editor",
            )
            manual = {
                str(row["Mese cedolino"]): bool(row["Includi"])
                for _, row in edited.iterrows()
            }
            calibrated = calibrate_payroll(
                salaries,
                variables_by_month,
                delay=delay_months,
                manual_included=manual,
                adjustments=calibration_adjustments,
                recency_months=int(round(rules.get("finestra_calibrazione_mesi", 12))),
            )
            confidence_margin = max(
                0.0,
                (float(calibrated.confidence_high) - float(calibrated.confidence_low)) / 2,
            )
            result_cards = [
                ("Netto fisso ottimale", _money_turni(calibrated.fixed_net), "#34d399", "16,185,129"),
                ("Coeff. variabili ottimale", f"{calibrated.variable_coefficient:.3f}", "#60a5fa", "59,130,246"),
                ("Errore medio tipico", _money_turni(calibrated.mean_absolute_error), "#facc15", "234,179,8"),
                ("Fascia prudenziale 95%", f"± {_money_turni(confidence_margin)}", "#f472b6", "219,39,119"),
            ]
            result_html = "".join(
                f'<div class="calibration-result-card" style="--cal-color:{color};--cal-rgb:{rgb};">'
                f'<div class="calibration-result-label">{html.escape(label)}</div>'
                f'<div class="calibration-result-value">{html.escape(value)}</div>'
                '</div>'
                for label, value, color, rgb in result_cards
            )
            st.markdown(
                f'<div class="calibration-result-grid">{result_html}</div>',
                unsafe_allow_html=True,
            )
            st.info(
                "Come leggerla: l’errore medio tipico descrive lo scarto che il modello "
                "ha avuto normalmente sullo storico. La fascia 95% è più larga perché "
                "copre anche mesi poco favorevoli. Se è enorme, di solito ci sono pochi "
                "mesi ordinari, rettifiche non registrate oppure variabili poco coerenti "
                "con i cedolini; controlla la colonna “Includi” prima di applicare."
            )
            if st.button("✅ Applica e salva calibrazione", key="apply_payroll_calibration", use_container_width=True):
                rules["netto_fisso_mensile"] = float(calibrated.fixed_net)
                rules["coefficiente_netto_variabili"] = float(calibrated.variable_coefficient)
                rules["errore_medio_calibrazione"] = float(calibrated.mean_absolute_error)
                st.session_state.turni_rules = rules
                st.session_state.payroll_calibration_mae = float(calibrated.mean_absolute_error)
                if save_turni_rules(rules):
                    # I widget della scheda Regole conservano il valore precedente:
                    # rimuoverli forza il ricaricamento dei parametri calibrati.
                    st.session_state.pop("turni_netto_fisso", None)
                    st.session_state.pop("turni_coeff_variabili", None)
                    st.success("Calibrazione applicata e salvata su Google Sheets.")
                    st.rerun()
                else:
                    st.error("Calibrazione applicata alla sessione, ma il salvataggio Google non è riuscito.")
        except ValueError as exc:
            st.info(str(exc))
        except Exception as exc:
            st.error(f"Impossibile calibrare lo storico: {exc}")
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

        def _sync_mobile_salary_from_widget(value_key, widget_key, default, max_value=None):
            if widget_key in st.session_state:
                st.session_state[value_key] = _parse_mobile_amount(
                    st.session_state[widget_key],
                    default,
                    max_value=max_value
                )
            else:
                st.session_state.setdefault(value_key, float(default))
            if max_value is not None:
                st.session_state[value_key] = min(
                    float(st.session_state[value_key]),
                    float(max_value)
                )

        def _store_mobile_salary_widget(value_key, widget_key, default, max_value=None):
            st.session_state[value_key] = _parse_mobile_amount(
                st.session_state.get(widget_key, st.session_state.get(value_key, default)),
                default,
                max_value=max_value
            )

        def _prime_mobile_salary_widget(widget_key, value_key):
            if widget_key not in st.session_state:
                st.session_state[widget_key] = float(st.session_state[value_key])

        if _mobile_show("Panoramica"):
            _sync_mobile_salary_from_widget(
                "mobile_salary_stipendio_percepito_value",
                "mobile_salary_stipendio_percepito_num",
                input_stipendio_percepito
            )
            _sync_mobile_salary_from_widget(
                "mobile_salary_budget_da_stipendio_value",
                "mobile_salary_budget_da_stipendio_num",
                input_budget_da_stipendio,
                max_value=st.session_state["mobile_salary_stipendio_percepito_value"]
            )
            _sync_mobile_salary_from_widget(
                "mobile_salary_risparmi_mese_precedente_value",
                "mobile_salary_risparmi_mese_precedente_num",
                input_risparmi_mese_precedente
            )
            _prime_mobile_salary_widget(
                "mobile_salary_stipendio_percepito_num",
                "mobile_salary_stipendio_percepito_value"
            )
            _prime_mobile_salary_widget(
                "mobile_salary_budget_da_stipendio_num",
                "mobile_salary_budget_da_stipendio_value"
            )
            _prime_mobile_salary_widget(
                "mobile_salary_risparmi_mese_precedente_num",
                "mobile_salary_risparmi_mese_precedente_value"
            )
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
                    format="%.0f",
                    on_change=_store_mobile_salary_widget,
                    args=(
                        "mobile_salary_stipendio_percepito_value",
                        "mobile_salary_stipendio_percepito_num",
                        input_stipendio_percepito
                    )
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
                    format="%.0f",
                    on_change=_store_mobile_salary_widget,
                    args=(
                        "mobile_salary_budget_da_stipendio_value",
                        "mobile_salary_budget_da_stipendio_num",
                        input_budget_da_stipendio,
                        float(stipendio_percepito)
                    )
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
                    format="%.0f",
                    on_change=_store_mobile_salary_widget,
                    args=(
                        "mobile_salary_risparmi_mese_precedente_value",
                        "mobile_salary_risparmi_mese_precedente_num",
                        input_risparmi_mese_precedente
                    )
                )
            st.session_state["mobile_salary_stipendio_percepito_value"] = stipendio_percepito
            st.session_state["mobile_salary_budget_da_stipendio_value"] = budget_da_stipendio
            st.session_state["mobile_salary_risparmi_mese_precedente_value"] = risparmi_mese_precedente
            st.markdown(
                '<div class="mobile-salary-note-grid">'
                '<span class="mobile-compact-input-note">Stipendio mese precedente</span>'
                '<span class="mobile-compact-input-note">Quota scelta mese precedente</span>'
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
            stipendio_percepito = st.number_input("Inserisci lo stipendio effettivamente percepito:", min_value=float(input_stipendio_percepito), value=float(input_stipendio_percepito), step=50.0, label_visibility="collapsed")
            st.markdown('<div style="height:10px;"></div><div class="salary-input-label">Risparmio mese prec.</div>', unsafe_allow_html=True)
            risparmi_mese_precedente = st.number_input("Inserisci quanto hai risparmiato nel mese precedente:", min_value=float(input_risparmi_mese_precedente), value=float(input_risparmi_mese_precedente), step=50.0, label_visibility="collapsed")
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

    if MOBILE_VIEW:
        salva_stipendio_home = st.button(
            "✓ Salva riepilogo del mese",
            key="salva_stipendio_home",
            use_container_width=True,
        ) if _mobile_show("Panoramica") else False
    else:
        with col_stip_inserimento2:
            salva_stipendio_home = st.button(
                "✓ Salva riepilogo del mese",
                key="salva_stipendio_home",
                use_container_width=True,
            )
    home_salary_save_feedback = st.empty()

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
                            <div class="budget-memory-label">Entrate mensili totali desiderate<br><span style="color:rgba(255,255,255,.42);">target €{entrate_totali_target:,.0f} · per risparmiare €{risparmio_desiderato_corrente:,.0f}</span></div>
                            <div class="budget-memory-value" style="color:#77dd77;">{entrate_status}</div>
                        </div>
                        <div class="budget-memory-row">
                            <div class="budget-memory-label">Budget mensile desiderato<br><span style="color:rgba(255,255,255,.42);">target €{budget_disponibile_target:,.0f} per coprire spese fisse + variabili</span></div>
                            <div class="budget-memory-value" style="color:#60a5fa;">{budget_status}</div>
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
    emergenze_senza_limite = emergenze_compleanni * risparmiabili
    SPESE["Variabili"]["Emergenze/Compleanni"] = min(emergenze_senza_limite, limite_emergenze_compleanni)
    SPESE["Variabili"]["Viaggi"] = viaggi * risparmiabili

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
    if emergenze_senza_limite > limite_emergenze_compleanni:
        risparmi_mensili += emergenze_senza_limite - limite_emergenze_compleanni

    risparmio_stipendi = stipendio_originale - stipendio_scelto
    risparmio_emergenze_compleanni = emergenze_senza_limite - SPESE["Variabili"]["Emergenze/Compleanni"] if emergenze_senza_limite > limite_emergenze_compleanni else 0
    risparmio_da_spendere = da_spendere_senza_limite - da_spendere if da_spendere_senza_limite > limite_da_spendere else 0
    risparmio_spese_quotidiane = spese_quotidiane_senza_limite - spese_quotidiane if spese_quotidiane_senza_limite > max_spese_quotidiane else 0

    revolut_expenses = sum(
        SPESE["Fisse"].get(voce, 0) + SPESE["Variabili"].get(voce, 0)
        for voce in SPESE["Revolut"]
    )
    revolut_expenses -= risparmi_mese_precedente
    risparmi_mensili += risparmi_mese_precedente

    if salva_stipendio_home:
        if salva_stipendio_corrente(
            stipendio_percepito,
            budget_da_stipendio,
            risparmi_mese_precedente,
            risparmi_mensili,
        ):
            home_salary_save_feedback.success("Riepilogo del mese salvato nello Storico stipendi.")
        else:
            home_salary_save_feedback.error("Non sono riuscito a salvare il riepilogo del mese.")

    if MOBILE_VIEW and _mobile_show("Panoramica"):
        spese_variabili_totali_home = sum(
            SPESE["Variabili"].get(voce, 0)
            for voce in ["Emergenze/Compleanni", "Viaggi", "Da spendere", "Spese quotidiane"]
        )

        def _recap_card(label, value, color, sub="", wide=False):
            wide_class = " wide" if wide else ""
            sub_html = (
                f'<div class="mobile-home-recap-sub">{html.escape(str(sub))}</div>'
                if str(sub)
                else ""
            )
            return (
                f'<div class="mobile-home-recap-card{wide_class}" style="--recap-color:{color};">'
                f'<div class="mobile-home-recap-label">{html.escape(str(label))}</div>'
                f'<div class="mobile-home-recap-value">{html.escape(str(value))}</div>'
                f'{sub_html}'
                "</div>"
            )

        def _donut_or_empty(title, labels, values, colors):
            donut_html = _mobile_donut_html(title, labels, values, colors)
            if donut_html:
                return donut_html
            return (
                '<div class="mobile-donut-card">'
                f'<div class="mobile-donut-title">{html.escape(title)}</div>'
                '<div class="mobile-home-donut-empty">tutto a zero</div>'
                '</div>'
            )

        def _recap_pair(label, value, color, sub, donut_html):
            return (
                '<div class="mobile-home-recap-pair">'
                f'{_recap_card(label, value, color, sub)}'
                f'{donut_html}'
                '</div>'
            )

        def _time_until_label(iso_value):
            if not iso_value:
                return ""
            try:
                target = datetime.fromisoformat(str(iso_value))
                minutes = max(0, int((target - _now_italy()).total_seconds() // 60))
            except Exception:
                return ""
            days, rem = divmod(minutes, 60 * 24)
            hours, mins = divmod(rem, 60)
            if days:
                return f"{days}g {hours}h"
            if hours:
                return f"{hours}h {mins:02d}m"
            return f"{mins}m"

        spese_meta_home = st.session_state.get("spese_fisse_metadata", {})
        fisse_categoria_totali_home = {}
        for voce, importo in SPESE["Fisse"].items():
            categoria = spese_meta_home.get(voce, {}).get("Categoria") or _infer_spesa_fissa_categoria(voce)
            fisse_categoria_totali_home[categoria] = fisse_categoria_totali_home.get(categoria, 0.0) + float(importo or 0)
        fisse_donut_home = _donut_or_empty(
            "Distribuzione",
            list(fisse_categoria_totali_home.keys()),
            list(fisse_categoria_totali_home.values()),
            [SPESA_FISSA_CATEGORIA_COLORI.get(categoria, "#94a3b8") for categoria in fisse_categoria_totali_home],
        )

        variabili_labels_home = ["Emergenze/Compleanni", "Viaggi", "Da spendere", "Spese quotidiane"]
        variabili_donut_home = _donut_or_empty(
            "Distribuzione",
            variabili_labels_home,
            [SPESE["Variabili"].get(voce, 0) for voce in variabili_labels_home],
            ["#4ade80", "#15803d", "#facc15", "#fb923c"],
        )
        altre_colors_base_home = ["#e6c48c", "#c4b5fd", "#7dd3fc", "#34d399", "#facc15"]
        altre_colors_home = [
            altre_colors_base_home[i % len(altre_colors_base_home)]
            for i, _ in enumerate(ALTRE_ENTRATE)
        ]
        altre_donut_home = _donut_or_empty(
            "Distribuzione",
            list(ALTRE_ENTRATE.keys()),
            list(ALTRE_ENTRATE.values()),
            altre_colors_home,
        )
        risparmi_donut_home = _donut_or_empty(
            "Distribuzione",
            ["Dal budget non usato", "Dal Mese Precedente", "Da Emergenze/Compleanni", "Dai 'Da Spendere'", "Dalle 'Spese Quotidiane'"],
            [risparmio_stipendi, risparmi_mese_precedente, risparmio_emergenze_compleanni, risparmio_da_spendere, risparmio_spese_quotidiane],
            ["#9ca3af", "#60a5fa", "#4ade80", "#fde047", "#FB923C"],
        )

        ing_total_home = sum(
            SPESE["Fisse"].get(voce, 0) + SPESE["Variabili"].get(voce, 0)
            for voce in SPESE["ING"]
        )
        revolut_total_home = revolut_expenses + risparmi_mese_precedente
        bnl_total_home = sum(
            SPESE["Fisse"].get(voce, 0) + SPESE["Variabili"].get(voce, 0)
            for voce in SPESE["BNL"]
        )
        carte_donut_home = _donut_or_empty(
            "Distribuzione",
            ["ING", "Revolut", "BNL", "Risparmi BNL"],
            [ing_total_home, revolut_total_home, bnl_total_home, risparmi_mensili],
            ["#d2691e", "#89cff0", "#2f8f46", "#77dd77"],
        )

        def _carte_item(label, value, color):
            return (
                f'<div class="mobile-home-carte-item" style="--carte-color:{color};">'
                f'<span>{html.escape(str(label))}</span>'
                f'<strong>{html.escape(_money_turni(value))}</strong>'
                '</div>'
            )

        carte_list_home = (
            '<div class="mobile-home-carte-list">'
            '<div class="mobile-home-carte-title">Carte</div>'
            + _carte_item("ING", ing_total_home, SPESA_FISSA_CARTA_COLORI.get("ING", "#D2691E"))
            + _carte_item("Revolut", revolut_total_home, SPESA_FISSA_CARTA_COLORI.get("Revolut", "#89CFF0"))
            + _carte_item("BNL", bnl_total_home, SPESA_FISSA_CARTA_COLORI.get("BNL", "#228B22"))
            + _carte_item("Risparmio BNL", risparmi_mensili, "#77DD77")
            + '</div>'
        )

        turni_stats_home = None
        try:
            turni_df_home = st.session_state.get("turni_df_draft")
            if turni_df_home is None or getattr(turni_df_home, "empty", True):
                turni_df_home = load_turni_data()
            current_turni_month = _now_italy().date().replace(day=1)
            turni_df_home, _home_calendar_errors = ensure_turni_month_synced(
                current_turni_month,
                turni_df_home,
            )
            if turni_df_home is not None and not turni_df_home.empty:
                turni_stats_home = compute_turni_dashboard(turni_df_home.copy(), get_turni_rules())
        except Exception:
            turni_stats_home = None

        turni_cards_home = (
            _recap_card("Mese corrente — netto maturato / cedolino", "Dati non caricati", "#34d399", "apri la sezione turni")
            + _recap_card("Turno — netto live / totale netto", "—", "#60a5fa", "nessun dato")
            + _recap_card("Stato turno", "—", "#fef3c7", "nessun dato")
        )
        if turni_stats_home:
            work_days_done = int(turni_stats_home.get("work_days_done", 0))
            work_days_total = int(turni_stats_home.get("work_days_total", 0))
            ferie_days_total = int(turni_stats_home.get("ferie_days_total", 0))
            month_days_total = work_days_total + ferie_days_total
            ferie_suffix = f" + {ferie_days_total} ferie = {month_days_total}" if ferie_days_total else ""
            month_value_home = (
                f"{_money_turni(turni_stats_home.get('live_month', 0))} / "
                f"{_money_turni(turni_stats_home.get('payslip_estimate', 0))}"
            )
            month_sub_home = f"Giorni lavorati: {work_days_done} / {work_days_total}{ferie_suffix}"

            turno_label_home = turni_stats_home.get("turno_kpi_label", "Turno — netto live / totale netto")
            turno_value_home = (
                f"{_money_turni(turni_stats_home.get('live_today', 0))} / "
                f"{_money_turni(turni_stats_home.get('expected_today', 0))}"
            )
            shift_type_home = str(turni_stats_home.get("current_shift_type", ""))
            if turni_stats_home.get("is_on_shift") or turni_stats_home.get("is_on_leave"):
                remaining_label = _time_until_label(turni_stats_home.get("current_shift_end", ""))
                turno_sub_home = f"Ore mancanti: {remaining_label}" if remaining_label else shift_type_home
                if shift_type_home and remaining_label:
                    turno_sub_home = f"{turno_sub_home} · {shift_type_home}"
            else:
                wait_label = _time_until_label(turni_stats_home.get("next_shift_start", ""))
                next_total = turni_stats_home.get("next_shift_total", 0)
                if wait_label and next_total:
                    turno_sub_home = f"Prossimo tra {wait_label} · {_money_turni(next_total)}"
                elif wait_label:
                    turno_sub_home = f"Prossimo tra {wait_label}"
                else:
                    turno_sub_home = turni_stats_home.get("next_shift_label", "nessun turno futuro")

            rate_min_home = float(turni_stats_home.get("rate_min", 0) or 0)
            rate_hour_home = rate_min_home * 60
            current_turno_home = turni_stats_home.get("current_turno") or ""
            current_date_home = turni_stats_home.get("current_shift_date") or ""
            if turni_stats_home.get("is_on_shift"):
                status_value_home = f"In turno · {current_turno_home}"
            elif turni_stats_home.get("is_on_leave"):
                status_value_home = "Fuori turno · in ferie"
            else:
                status_value_home = "Fuori turno"
            if current_date_home and status_value_home != "Fuori turno":
                status_value_home = f"{status_value_home} · {current_date_home}"
            status_sub_home = f"{rate_min_home:.2f} €/min · {rate_hour_home:.2f} €/h"

            turni_cards_home = (
                _recap_card("Mese corrente — netto maturato / cedolino", month_value_home, "#34d399", month_sub_home)
                + _recap_card(turno_label_home, turno_value_home, "#60a5fa", turno_sub_home)
                + _recap_card("Stato turno", status_value_home, "#fef3c7", status_sub_home)
            )

        home_recap_html = (
            '<div class="mobile-home-recap">'
            '<div class="mobile-home-recap-row">'
            + _recap_pair("Spese fisse", _money_turni(spese_fisse_totali), "#f87171", "", fisse_donut_home)
            + _recap_pair("Spese variabili", _money_turni(spese_variabili_totali_home), "#f59e0b", "", variabili_donut_home)
            + _recap_pair("Altre entrate", _money_turni(altre_entrate_totali), "#34d399", "", altre_donut_home)
            + _recap_pair("Risparmi", _money_turni(risparmi_mensili), "#facc15", "", risparmi_donut_home)
            + '</div>'
            '</div>'
        )
        st.markdown(
            '<div style="height:1px;background:rgba(148,163,184,.22);margin:18px 0 14px;"></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            home_recap_html,
            unsafe_allow_html=True,
        )
        home_carte_col, home_spacer_col, home_turni_col = st.columns([1.22, 0.52, 2.26], gap="small")
        with home_carte_col:
            st.markdown('<div class="mobile-home-carte-live-left-marker"></div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="mobile-home-recap">'
                '<div class="mobile-home-carte-stack">'
                    + carte_list_home
                    + carte_donut_home
                    + '</div>'
                    '</div>',
                unsafe_allow_html=True,
            )
        with home_spacer_col:
            st.markdown('<div class="mobile-home-carte-live-spacer-marker"></div>', unsafe_allow_html=True)
        with home_turni_col:
            st.markdown('<div class="mobile-home-carte-live-right-marker"></div>', unsafe_allow_html=True)
            if turni_stats_home:
                render_live_turni_kpis(turni_stats_home)
            else:
                st.markdown(
                    '<div class="mobile-home-recap">'
                    '<div class="mobile-home-turni-row">'
                    + turni_cards_home
                    + '</div>'
                    '</div>',
                    unsafe_allow_html=True,
                )

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

                editor_cols = st.columns(2)
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
                    "Distribuzione",
                    df_fisse["Categoria"].tolist(),
                    df_fisse["Importo"].tolist(),
                    df_fisse["Categoria"].map(lambda c: color_map.get(str(c), "#999999")).tolist()
                )
                entrate_donut_html = _mobile_donut_html(
                    "Entrate mensili totali",
                    df_totale_clean["Component"].tolist(),
                    df_totale_clean["Value"].tolist(),
                    df_totale_clean["Component"].map({
                        "Spese Fisse": "#FF6464",
                        "Budget dopo spese fisse": "#fef3c7",
                        "Risparmio Stipendi": "#888888",
                    }).fillna("#94a3b8").tolist()
                )
                budget_donut_html = _mobile_donut_html(
                    "Budget mensile disponibile",
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
                        + _spesa_variabile_row_html("Emergenze/Compleanni", SPESE["Variabili"]["Emergenze/Compleanni"], "#4ADE80", f"{percentuale_emergenze:.2f}% del budget dopo spese fisse, limite €{limite_emergenze_compleanni:.2f}")
                        + f'<div style="font-size:12px;color:rgba(255,255,255,.36);margin:-4px 0 7px 10px;">reale €{emergenze_senza_limite:.2f} · risparmiati €{risparmio_emergenze_compleanni:.2f}</div>'
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
                                f"{percentuale_emergenze:.2f}% del budget dopo spese fisse, limite €{limite_emergenze_compleanni:.2f}"
                            ),
                            unsafe_allow_html=True
                        )
                        st.markdown(
                            f'<div style="font-size:12px;color:rgba(255,255,255,.36);margin:-4px 0 7px 10px;">reale €{emergenze_senza_limite:.2f} · risparmiati €{risparmio_emergenze_compleanni:.2f}</div>',
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
            emergenze_senza_limite_calc = emergenze_compleanni * risparmiabili
            SPESE["Variabili"]["Emergenze/Compleanni"] = min(emergenze_senza_limite_calc, limite_emergenze_compleanni)
            SPESE["Variabili"]["Viaggi"] = viaggi * risparmiabili
            da_spendere_senza_limite_calc = percentuale_limite_da_spendere * (risparmiabili - sum(percentuali_variabili_calc.values()) * risparmiabili)
            SPESE["Variabili"]["Da spendere"] = min(da_spendere_senza_limite_calc, limite_da_spendere)
            spese_quotidiane_senza_limite_calc = risparmiabili - sum(percentuali_variabili_calc.values()) * risparmiabili - da_spendere_senza_limite_calc
            SPESE["Variabili"]["Spese quotidiane"] = min(spese_quotidiane_senza_limite_calc, max_spese_quotidiane)
            if emergenze_senza_limite_calc > limite_emergenze_compleanni:
                risparmi_mensili_calc += emergenze_senza_limite_calc - limite_emergenze_compleanni
            if spese_quotidiane_senza_limite_calc > max_spese_quotidiane:
                risparmi_mensili_calc += spese_quotidiane_senza_limite_calc - max_spese_quotidiane
            if da_spendere_senza_limite_calc > limite_da_spendere:
                risparmi_mensili_calc += da_spendere_senza_limite_calc - limite_da_spendere
            risparmi_mensili_calc += risparmi_mese_precedente
            risparmio_stipendi_calc = stipendio_originale - stipendio_scelto
            risparmio_emergenze_calc = emergenze_senza_limite_calc - min(emergenze_senza_limite_calc, limite_emergenze_compleanni) if emergenze_senza_limite_calc > limite_emergenze_compleanni else 0
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
                v3 = risparmio_emergenze_calc
                v4 = risparmio_da_spendere_calc
                v5 = risparmio_spese_quotidiane_calc
            
                html_risparmi = ""
                html_risparmi += _money_row_html("Dal budget non usato", v1, "#9ca3af", triangolino_verde_BNL, "differenza tra stipendio percepito e quota stipendio scelta")
                html_risparmi += _money_row_html("Dal Mese Precedente", v2, "#60a5fa", triangolino_verde_BNL, "risparmio riportato nel mese corrente")
                html_risparmi += '<div style="height:1px;background:rgba(148,163,184,0.24);margin:10px 0 8px;"></div>'
                html_risparmi += _money_row_html("Da Emergenze/Compleanni", v3, "#4ade80", triangolino_verde_BNL, f"eccedenza oltre il limite €{limite_emergenze_compleanni:.2f}")
                html_risparmi += _money_row_html("Dai 'Da Spendere'", v4, "#fde047", triangolino_verde_BNL, "differenza non usata sul budget da spendere")
                html_risparmi += _money_row_html("Dalle 'Spese Quotidiane'", v5, "#FB923C", triangolino_verde_BNL, "differenza non usata sulle spese quotidiane")
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
                    savings_vals = [risparmio_stipendi_calc, risparmi_mese_precedente, risparmio_emergenze_calc, risparmio_da_spendere_calc, risparmio_spese_quotidiane_calc]
                    non_saved_calc = max(0, (stipendio_originale + sum(ALTRE_ENTRATE.values())) - sum(savings_vals))
                    df_savings_raw = pd.DataFrame({
                        'Component': ["Dal budget non usato", "Dal Mese Precedente", "Da Emergenze/Compleanni", "Dai 'Da Spendere'", "Dalle 'Spese Quotidiane'"],
                        'Value': [risparmio_stipendi_calc, risparmi_mese_precedente, risparmio_emergenze_calc, risparmio_da_spendere_calc, risparmio_spese_quotidiane_calc]
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
                                    "Dal budget non usato": "#9ca3af",
                                    "Dal Mese Precedente": "#60a5fa",
                                    "Da Emergenze/Compleanni": "#4ade80",
                                    "Dai 'Da Spendere'": "#fde047",
                                    "Dalle 'Spese Quotidiane'": "#FB923C",
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
                                        domain=["Dal budget non usato", "Dal Mese Precedente", "Da Emergenze/Compleanni", "Dai 'Da Spendere'", "Dalle 'Spese Quotidiane'"],
                                        range=['#9ca3af', '#60a5fa', '#4ade80', '#fde047', '#FB923C']
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
                tab_carte_trasferimenti, tab_carte_riepilogo = st.tabs(["💳 Trasferimenti", "📋 Riepilogo carte"])

                with tab_carte_trasferimenti:
                    st.subheader("Trasferimenti sulle Carte:")
                    mobile_transfer_rows = []
                    for carta in ["ING", "Revolut", "BNL"]:
                        spese_carta = {
                            voce: SPESE["Fisse"].get(voce, 0) + SPESE["Variabili"].get(voce, 0)
                            for voce in SPESE[carta]
                        }
                        totale_carta = sum(spese_carta.values())
                        if carta == "Revolut":
                            totale_carta = revolut_expenses
                            colore = "#89CFF0"
                            testo = "trasferire"
                            spese_fisse_revolut = sum(
                                SPESE["Fisse"].get(voce, 0.0)
                                for voce in SPESE["Revolut"]
                            )
                            accantonamenti_revolut = sum(
                                SPESE["Variabili"].get(voce, 0.0)
                                for voce in ["Emergenze/Compleanni", "Viaggi", "Da spendere"]
                            )
                            anticipo_rimborsabile_revolut = 21.50
                            saldo_prima_accantonamenti = (
                                risparmi_mese_precedente + totale_carta
                                - spese_fisse_revolut - anticipo_rimborsabile_revolut
                            )
                            saldo_dopo_accantonamenti = saldo_prima_accantonamenti - accantonamenti_revolut
                            saldo_dopo_rimborso = saldo_dopo_accantonamenti + anticipo_rimborsabile_revolut
                            didascalia = (
                                f"Vedrai €{saldo_prima_accantonamenti:.2f}<br>"
                                f"Di cui €{accantonamenti_revolut:.2f} da destinare a emergenze, viaggi e ‘Da spendere’<br>"
                                f"Dopo i trasferimenti: €{saldo_dopo_accantonamenti:.2f}<br>"
                                f"Dopo il rimborso di €{anticipo_rimborsabile_revolut:.2f}: €{saldo_dopo_rimborso:.2f} per le spese quotidiane"
                            )
                        elif carta == "ING":
                            colore = "#D2691E"
                            testo = "trasferire"
                            didascalia = "totale delle spese previste su questa carta"
                        else:
                            colore = "#2E7D32"
                            testo = "mantenere"
                            didascalia = "totale delle spese previste su questa carta"
                        transfer_row_html = _money_row_html(
                            f"Da {testo} su {carta}", totale_carta, colore,
                            _triangle_for_card(carta), didascalia,
                        )
                        if MOBILE_VIEW:
                            mobile_transfer_rows.append(transfer_row_html)
                        else:
                            st.markdown(transfer_row_html, unsafe_allow_html=True)
                    risparmi_bnl_row_html = _money_row_html(
                        "Totale risparmiato su BNL", risparmi_mensili, "#77DD77",
                        _triangle_for_card("BNL"), "quota da lasciare come risparmio",
                    )
                    if MOBILE_VIEW:
                        mobile_transfer_rows.append(risparmi_bnl_row_html)
                    else:
                        st.markdown(risparmi_bnl_row_html, unsafe_allow_html=True)
                    ing_total = sum(SPESE["Fisse"].get(v, 0) + SPESE["Variabili"].get(v, 0) for v in SPESE["ING"])
                    revolut_total = revolut_expenses + risparmi_mese_precedente
                    bnl_total = sum(SPESE["Fisse"].get(v, 0) + SPESE["Variabili"].get(v, 0) for v in SPESE["BNL"])
                    if MOBILE_VIEW:
                        carte_donut_html = _mobile_donut_html(
                            "Distribuzione carte",
                            ["ING", "Revolut", "BNL", "Risparmi BNL"],
                            [ing_total, revolut_total, bnl_total, risparmi_mensili],
                            ["#D2691E", "#89CFF0", "#2E7D32", "#77DD77"],
                        )
                        st.markdown(
                            '<div style="display:grid;grid-template-columns:minmax(0,1.25fr) minmax(0,.75fr);gap:8px;align-items:start;">'
                            f'<div>{"".join(mobile_transfer_rows)}</div><div>{carte_donut_html}</div></div>',
                            unsafe_allow_html=True,
                        )
                    else:
                        df_carte = pd.DataFrame({
                            "Carta": ["ING", "Revolut", "BNL", "Risparmi BNL"],
                            "Totale": [ing_total, revolut_total, bnl_total, risparmi_mensili],
                        })
                        chart_carte = alt.Chart(df_carte).mark_arc(innerRadius=42, outerRadius=68).encode(
                            theta=alt.Theta("Totale:Q"),
                            color=alt.Color(
                                "Carta:N",
                                scale=alt.Scale(
                                    domain=["ING", "Revolut", "BNL", "Risparmi BNL"],
                                    range=["#D2691E", "#89CFF0", "#2E7D32", "#77DD77"],
                                ),
                                legend=alt.Legend(title=None, orient="right"),
                            ),
                            tooltip=[alt.Tooltip("Carta:N"), alt.Tooltip("Totale:Q", format=".2f")],
                        ).properties(title="Distribuzione carte", height=210)
                        st.altair_chart(chart_carte, use_container_width=True)

                with tab_carte_riepilogo:
                    st.subheader("Spese di riferimento per carta")
                    def render_riepilogo_carta(carta, colore, risparmi_bnl=False):
                        titolo_carta = "Risparmi BNL" if risparmi_bnl else carta
                        colori_variabili = {
                            "Emergenze/Compleanni": "#4ADE80",
                            "Viaggi": "#166534",
                            "Da spendere": "#FACC15",
                            "Spese quotidiane": "#FB923C",
                        }
                        righe = []
                        totale_carta = 0.0
                        if risparmi_bnl:
                            totale_carta = float(risparmi_mensili)
                            righe.append(
                                '<div style="display:flex;justify-content:space-between;gap:10px;padding:5px 0;border-top:1px solid rgba(255,255,255,.07);">'
                                '<span style="color:rgba(255,255,255,.72);">Quota da lasciare</span>'
                                f'<strong style="color:{colore};white-space:nowrap;">€{totale_carta:,.2f}</strong></div>'
                            )
                        else:
                            for voce in SPESE[carta]:
                                importo = float(SPESE["Fisse"].get(voce, 0) + SPESE["Variabili"].get(voce, 0))
                                if abs(importo) < 0.001:
                                    continue
                                totale_carta += importo
                                colore_voce = colori_variabili.get(voce, "rgba(255,255,255,.72)")
                                righe.append(
                                    '<div style="display:flex;justify-content:space-between;gap:10px;padding:5px 0;border-top:1px solid rgba(255,255,255,.07);">'
                                    f'<span style="color:{colore_voce};">{html.escape(str(voce))}</span>'
                                    f'<strong style="color:{colore};white-space:nowrap;">€{importo:,.2f}</strong>'
                                    '</div>'
                                )
                        dettaglio = "".join(righe) or '<div style="color:rgba(255,255,255,.45);">Nessuna spesa assegnata</div>'
                        st.markdown(
                            f'<div class="kpi-card" style="margin:0 0 10px;border-color:{colore}55;">'
                            f'<div class="kpi-label" style="color:{colore};">{html.escape(titolo_carta)}</div>'
                            f'{dettaglio}'
                            f'<div style="display:flex;justify-content:space-between;border-top:1px solid {colore}55;margin-top:6px;padding-top:7px;">'
                            f'<strong>Totale</strong><strong style="color:{colore};">€{totale_carta:,.2f}</strong></div></div>',
                            unsafe_allow_html=True,
                        )
                    riepilogo_col1, riepilogo_col2 = st.columns(2, gap="small")
                    with riepilogo_col1:
                        st.markdown('<span class="carte-summary-mobile-marker"></span>', unsafe_allow_html=True)
                        render_riepilogo_carta("ING", "#D2691E")
                        render_riepilogo_carta("BNL", "#2E7D32")
                        render_riepilogo_carta("BNL", "#77DD77", risparmi_bnl=True)
                    with riepilogo_col2:
                        st.markdown('<span class="carte-summary-mobile-marker"></span>', unsafe_allow_html=True)
                        render_riepilogo_carta("Revolut", "#89CFF0")

            if False and _mobile_show("Carte"):
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
                        spese_variabili_accantonate = sum(
                            SPESE["Variabili"].get(voce, 0.0)
                            for voce in ["Emergenze/Compleanni", "Viaggi", "Da spendere"]
                        )
                        anticipo_rimborsabile_revolut = 21.50
                        saldo_revolut_prima_accantonamenti = (
                            risparmi_mese_precedente
                            + totale_carta
                            - somma_spese_programmate_immediate
                            - anticipo_rimborsabile_revolut
                        )
                        saldo_revolut_dopo_accantonamenti = (
                            saldo_revolut_prima_accantonamenti - spese_variabili_accantonate
                        )
                        saldo_revolut_dopo_rimborso = saldo_revolut_dopo_accantonamenti + anticipo_rimborsabile_revolut
                        row_html = _money_row_html(
                            f"Da {testo} su {carta}",
                            totale_carta,
                            colore,
                            _triangle_for_card(carta),
                            f"Vedrai €{saldo_revolut_prima_accantonamenti:.2f}<br>"
                            f"Di cui €{spese_variabili_accantonate:.2f} da destinare a emergenze, viaggi e ‘Da spendere’<br>"
                            f"Dopo i trasferimenti: €{saldo_revolut_dopo_accantonamenti:.2f}<br>"
                            f"Dopo il rimborso di €{anticipo_rimborsabile_revolut:.2f}: €{saldo_revolut_dopo_rimborso:.2f} per le spese quotidiane"
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
                                "Distribuzione",
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
                            title="Distribuzione",
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
    # I primi tre record dello storico sono mesi di avvio con presenza parziale:
    # non devono abbassare le medie degli stipendi, ma restano visibili nei dati.
    salary_excluded_indexes = set(
        data.loc[data["Mese"].notna()]
        .sort_values("Mese")
        .head(3)
        .index
    )
    for col in colonne:
        if col in data.columns:
            data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0)
            if col == "Stipendio":
                stipendio_per_media = data[col].where(~data.index.isin(salary_excluded_indexes))
                data[f"Media {col}"] = stipendio_per_media.expanding().mean().round(2)
                data[f"Media {col} NO 13°/PDR"] = stipendio_per_media.where(
                    ~data["Mese"].dt.month.isin([7, 12])
                ).expanding().mean().round(2)
            else:
                data[f"Media {col}"] = data[col].expanding().mean().round(2)
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

    data_stipendi = load_data_gsheets("Stipendi", STIPENDI_HEADERS)
    if data_stipendi.empty:
        data_stipendi = pd.DataFrame(columns=STIPENDI_HEADERS)
    else:
        for col in STIPENDI_HEADERS:
            if col not in data_stipendi.columns:
                data_stipendi[col] = 0.0
        data_stipendi["Mese"] = pd.to_datetime(data_stipendi["Mese"], errors="coerce")
        data_stipendi = data_stipendi.dropna(subset=["Mese"])
        data_stipendi["Mese"] = data_stipendi["Mese"].dt.to_period("M").dt.to_timestamp()
        for col in ["Stipendio", "Quota stipendio scelta", "Risparmi", "Messi da parte Totali"]:
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
        quota_stipendio_val = float(record_esistente["Quota stipendio scelta"].iloc[0]) if not record_esistente.empty else 0.0
        risparmi_val = float(record_esistente["Risparmi"].iloc[0]) if not record_esistente.empty else 0.0
        messi_da_parte_mese_corrente_val = float(record_esistente["Messi da parte Totali"].iloc[0]) if not record_esistente.empty else 0.0
        if MOBILE_VIEW:
            st.caption("Valori salvati per il mese selezionato; se il mese non esiste viene creato al salvataggio.")
            col_input1, col_input2, col_input3, col_input4 = st.columns(4)
            with col_input1:
                stipendio = st.number_input("Stipendio (€)", min_value=0.0, step=100.0, value=stipendio_val, key=f"stipendio_input_{selected_mese}")
            with col_input2:
                quota_stipendio = st.number_input("Quota scelta (€)", min_value=0.0, max_value=stipendio, step=100.0, value=min(quota_stipendio_val, stipendio), key=f"quota_stipendio_input_{selected_mese}")
            with col_input3:
                risparmi = st.number_input("Risparmi mese prec. (€)", min_value=0.0, step=100.0, value=risparmi_val, key=f"risparmi_input_{selected_mese}")
            with col_input4:
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
                quota_stipendio = st.number_input("Quota stipendio scelta (€)", min_value=0.0, max_value=stipendio, step=100.0, value=min(quota_stipendio_val, stipendio), key=f"quota_stipendio_input_{selected_mese}")
                aggiungi_button = st.button("Aggiungi/Modifica Dati", key="aggiorna_stipendi")
            with col_input2:
                risparmi = st.number_input("Risparmi mese prec. (€)", min_value=0.0, step=100.0, value=risparmi_val, key=f"risparmi_input_{selected_mese}")
                messi_da_parte_mese_corrente = st.number_input("Messi da parte Totali (Risp. su BNL) (€)", min_value=0.0, step=100.0, value=messi_da_parte_mese_corrente_val, key=f"messi_da_parte_input_{selected_mese}")
                elimina_button = st.button(f"Elimina Record per {selected_mese}", key="elimina_stipendi")

        if aggiungi_button:
            if stipendio > 0 or quota_stipendio > 0 or risparmi > 0 or messi_da_parte_mese_corrente > 0:
                if not record_esistente.empty:
                    data_stipendi.loc[data_stipendi["Mese"] == mese_dt, "Stipendio"] = stipendio
                    data_stipendi.loc[data_stipendi["Mese"] == mese_dt, "Quota stipendio scelta"] = quota_stipendio
                    data_stipendi.loc[data_stipendi["Mese"] == mese_dt, "Risparmi"] = risparmi
                    data_stipendi.loc[data_stipendi["Mese"] == mese_dt, "Messi da parte Totali"] = messi_da_parte_mese_corrente
                    placeholder = st.empty()
                    placeholder.success(f"Record per {selected_mese} aggiornato!")
                    time.sleep(3)
                    placeholder.empty()
                else:
                    nuovo_record = {"Mese": mese_dt, "Stipendio": stipendio, "Quota stipendio scelta": quota_stipendio, "Risparmi": risparmi, "Messi da parte Totali": messi_da_parte_mese_corrente}
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
    BOLLETTE_VALUE_COLUMNS = ["Elettricità", "Gas", "Acqua", "Internet", "Tari"]

    def _parse_bolletta_amount(value):
        try:
            if pd.isna(value):
                return 0.0
        except Exception:
            pass
        if isinstance(value, str):
            text = value.strip().replace("€", "").replace(" ", "")
            if "," in text and "." in text:
                if text.rfind(",") > text.rfind("."):
                    text = text.replace(".", "").replace(",", ".")
                else:
                    text = text.replace(",", "")
            elif "," in text:
                text = text.replace(".", "").replace(",", ".")
            value = text
        parsed = pd.to_numeric(value, errors="coerce")
        return 0.0 if pd.isna(parsed) else float(parsed)

    def normalizza_data_bollette(data):
        if data is None or data.empty:
            return pd.DataFrame(columns=BOLLETTE_HEADERS)
        df = data.copy()
        for col in BOLLETTE_HEADERS:
            if col not in df.columns:
                df[col] = pd.NaT if col == "Mese" else 0.0
        df = df[BOLLETTE_HEADERS].copy()
        df["Mese"] = pd.to_datetime(df["Mese"], errors="coerce")
        df = df.dropna(subset=["Mese"])
        df["Mese"] = df["Mese"].dt.to_period("M").dt.to_timestamp()
        for col in BOLLETTE_VALUE_COLUMNS:
            df[col] = df[col].map(_parse_bolletta_amount).astype(float)
        return df

    data_bollette = normalizza_data_bollette(load_data_gsheets("Bollette", BOLLETTE_HEADERS))

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
                    valori_bollette = {
                        "Elettricità": _parse_bolletta_amount(elettricita),
                        "Gas": _parse_bolletta_amount(gas),
                        "Acqua": _parse_bolletta_amount(acqua),
                        "Internet": _parse_bolletta_amount(internet),
                        "Tari": _parse_bolletta_amount(tari),
                    }
                    data_bollette = normalizza_data_bollette(data_bollette)
                    mask_bol = data_bollette["Mese"] == mese_dt_bol
                    if mask_bol.any():
                        for col, value in valori_bollette.items():
                            data_bollette.loc[mask_bol, col] = value
                        placeholder = st.empty()
                        placeholder.success(f"Record per {selected_mese_bol} aggiornato!")
                        time.sleep(3)
                        placeholder.empty()
                    else:
                        nuovo_record_bol = {"Mese": mese_dt_bol, **valori_bollette}
                        data_bollette = pd.concat([data_bollette, pd.DataFrame([nuovo_record_bol])], ignore_index=True)
                        placeholder = st.empty()
                        placeholder.success(f"Bollette per {selected_mese_bol} aggiunte!")
                        time.sleep(3)
                        placeholder.empty()

                    data_bollette = normalizza_data_bollette(data_bollette).sort_values(by="Mese").reset_index(drop=True)
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
