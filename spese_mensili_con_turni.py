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
