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

def get_or_create_worksheet(client, sheet_url, worksheet_name, headers):
    try:
        spreadsheet = client.open_by_url(sheet_url)
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=20)
            worksheet.append_row(headers)
        return worksheet
    except Exception as e:
        st.error(f"Errore connessione Google Sheets: {e}")
        return None

GSHEETS_CACHE_TTL_SECONDS = 300


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
            st.warning(f"Google Sheets non risponde ora ({worksheet_name}). Uso l'ultima copia caricata in memoria.")
            return cached
        st.error(f"Errore caricamento dati: {e}")
        return pd.DataFrame(columns=headers)

def save_data_gsheets(worksheet_name, headers, data):
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
    font-size: 10px;
    color: rgba(255,255,255,0.4);
    text-transform: uppercase;
    letter-spacing: 0.9px;
    margin-bottom: 4px;
}
.kpi-value {
    font-family: 'DM Mono', monospace;
    font-size: 20px;
    font-weight: 500;
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
totale_entrate_target_oltre_lo_stipendio= 0.9

percentuale_limite_da_spendere=0.15
limite_da_spendere=80
max_spese_quotidiane=370
decisione_budget_bollette_mensili=180

emergenze_compleanni=0.15
viaggi=0.07

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
        "Altro/C": 135,
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
    "Revolut": ["Trasporti", "Sport", "Bollette", "Pulizia Casa", "Psicologo", "Altro/C", "Beneficienza", "Netflix", "Spotify", "Disney+", "Emergenze/Compleanni", "Viaggi", "Da spendere", "Spese quotidiane"],
    "ING": ["Condominio", "Altro", "Cucina", "MoneyFarm - PAC 5", "Alleanza - PAC", "World Food Programme", "Macchina", "ING C.C."],
    "BNL": ["Mutuo", "BNL C.C."],
}

ALTRE_ENTRATE = {
    "Macchina (Mamma)": 100,
    "2° Entr. dal mese prec.": 0,
    "Altro": 0
}

@st.cache_data
def create_charts(stipendio_scelto, risparmiabili, df_altre_entrate):

    df_fisse = pd.DataFrame.from_dict(SPESE["Fisse"], orient="index", columns=["Importo"]).reset_index().rename(columns={"index": "Categoria"})
    df_fisse.loc[(df_fisse["Categoria"] == "World Food Programme") | (df_fisse["Categoria"] == "Beneficienza"), "Categoria"] = "Donazioni"
    df_fisse.loc[(df_fisse["Categoria"] == "MoneyFarm - PAC 5") | (df_fisse["Categoria"] == "Alleanza - PAC"), "Categoria"] = "Investimenti"
    df_fisse.loc[(df_fisse["Categoria"] == "Netflix") | (df_fisse["Categoria"] == "Disney+") | (df_fisse["Categoria"] == "Spotify") | (df_fisse["Categoria"] == "BNL C.C.") | (df_fisse["Categoria"] == "ING C.C."), "Categoria"] = "Abbonamenti"
    df_fisse.loc[(df_fisse["Categoria"] == "Sport") | (df_fisse["Categoria"] == "Psicologo") | (df_fisse["Categoria"] == "Altro/C"), "Categoria"] = "Salute"
    df_fisse.loc[(df_fisse["Categoria"] == "Trasporti") | (df_fisse["Categoria"] == "Macchina"), "Categoria"] = "Macchina"
    df_fisse.loc[(df_fisse["Categoria"] == "Bollette") | (df_fisse["Categoria"] == "Mutuo") | (df_fisse["Categoria"] == "Condominio") | (df_fisse["Categoria"] == "Altro") | (df_fisse["Categoria"] == "Cucina") | (df_fisse["Categoria"] == "Pulizia Casa"), "Categoria"] = "Casa"
    df_fisse = df_fisse.groupby("Categoria").sum().reset_index()

    df_variabili = pd.DataFrame.from_dict(SPESE["Variabili"], orient="index", columns=["Importo"]).reset_index().rename(columns={"index": "Categoria"})
    df_altre_entrate = pd.DataFrame.from_dict(ALTRE_ENTRATE, orient="index", columns=["Importo"]).reset_index().rename(columns={"index": "Categoria"})
    df_variabili['Percentuale'] = (df_variabili['Importo'] / risparmiabili).map('{:.2%}'.format)

    totali = [df_fisse["Importo"].sum(), df_variabili["Importo"].sum(), df_altre_entrate["Importo"].sum(), stipendio_scelto]
    categorie = ["Spese Fisse", "Spese Variabili", "Altre Entrate", "Stipendio Scelto"]
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
        "Altro/C": "#40E0D0",
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
        "Stipendio Originale": "#5792E8",
        "Stipendio Utilizzato": "#6CBCD0",
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
    font-size: 13px !important;
    line-height: 1 !important;
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


def _is_festive_at(dt_obj, forced_festivo=False):
    return bool(forced_festivo) or dt_obj.weekday() == 6


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


def compute_turno(data_str, turno, forced_festivo, rules, until=None, only_day=None):
    now = datetime.now() if until is None else until
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
    if start <= datetime.now() <= end:
        rate_min = paga * (1 + _pct_for_turno(turno, datetime.now(), forced_festivo, rules) / 100) / 60

    return {"total": base + extra, "base": base, "extra": extra, "hours": hours, "rate_min": rate_min}


def _turni_current_prev_months():
    now = datetime.now()
    current = now.strftime("%Y-%m")
    prev = (pd.Timestamp(now.replace(day=1)) - pd.DateOffset(months=1)).strftime("%Y-%m")
    return current, prev


def compute_turni_dashboard(df_turni, rules):
    now = datetime.now()
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
    current_shift_end = None

    for _, row in df_turni.iterrows():
        data = row["Data"]
        turno = row["Turno"]
        festivo = bool(row["Festivo"])
        has_turno = turno in TURNI_ORARI and turno != ""

        if has_turno and data[:7] == current_m:
            calc_live = compute_turno(data, turno, festivo, rules, until=now)
            live_month += calc_live["total"]
            hours_live += calc_live["hours"]
            calc_full = compute_turno(data, turno, festivo, rules, until=datetime.max.replace(tzinfo=None))
            current_base_full += calc_full["base"]
            start, end = _shift_bounds(data, turno)
            if turno not in ["Ferie", "Riposo"] and start <= now <= end:
                rate_min = calc_live["rate_min"]
                current_shift = f"{turno} {start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
                current_turno = turno
                current_shift_end = end

        if has_turno and data[:7] == prev_m:
            calc_prev = compute_turno(data, turno, festivo, rules, until=datetime.max.replace(tzinfo=None))
            prev_extras += calc_prev["extra"]

        if not has_turno:
            continue
        start, end = _shift_bounds(data, turno)
        if start.strftime("%Y-%m-%d") <= today <= end.strftime("%Y-%m-%d"):
            live_today += compute_turno(data, turno, festivo, rules, until=now, only_day=today)["total"]
            expected_today += compute_turno(data, turno, festivo, rules, until=datetime.max.replace(tzinfo=None), only_day=today)["total"]

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
        "is_on_shift": bool(current_shift_end),
        "current_shift_end": current_shift_end.isoformat() if current_shift_end else "",
    }




def _turno_color_info(turno):
    mapping = {
        "Mattina": {"emoji": "🔵", "short": "M", "class": "turni-mattina", "color": "#60a5fa"},
        "Pomeriggio": {"emoji": "🟠", "short": "P", "class": "turni-pomeriggio", "color": "#fb923c"},
        "Notte": {"emoji": "⚫", "short": "N", "class": "turni-notte", "color": "#64748b"},
        "Ferie": {"emoji": "🟢", "short": "F", "class": "turni-ferie", "color": "#34d399"},
        "Riposo": {"emoji": "⚪", "short": "R", "class": "turni-riposo", "color": "#cbd5e1"},
    }
    return mapping.get(str(turno), {"emoji": "—", "short": "—", "class": "", "color": "rgba(255,255,255,0.45)"})


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


def render_live_turni_kpis(stats):
    live_month = float(stats["live_month"])
    live_today = float(stats["live_today"])
    rate_min = float(stats["rate_min"])
    rate_sec = rate_min / 60
    payslip_estimate = _money_turni(stats["payslip_estimate"])
    expected_today = _money_turni(stats["expected_today"])
    current_shift = str(stats["current_shift"]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    current_turno = str(stats.get("current_turno", "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    is_on_shift = bool(stats.get("is_on_shift", False))
    status_color = "#22c55e" if is_on_shift else "#64748b"
    status_shadow = "0 0 12px rgba(34,197,94,0.75)" if is_on_shift else "none"
    status_text = f"In turno · {current_turno}" if is_on_shift else "Fuori turno"
    current_shift_end = stats.get("current_shift_end", "")
    components.html(f"""
    <div class="turni-live-grid">
      <div class="kpi-card" style="border-color:rgba(52,211,153,0.25);">
        <div class="kpi-label">Mese corrente — live / stimato cedolino</div>
        <div class="kpi-value" style="color:#34d399;"><span id="turni-live-month"></span> / {payslip_estimate}</div>
      </div>
      <div class="kpi-card" style="border-color:rgba(96,165,250,0.25);">
        <div class="kpi-label">Oggi — live / totale giornata</div>
        <div class="kpi-value" style="color:#60a5fa;"><span id="turni-live-today"></span> / {expected_today}</div>
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
    </style>
    <script>
      const start = Date.now();
      const startMonth = {live_month:.8f};
      const startToday = {live_today:.8f};
      const rateSec = {rate_sec:.10f};
      const shiftEnd = {json.dumps(current_shift_end)};
      const monthEl = document.getElementById("turni-live-month");
      const todayEl = document.getElementById("turni-live-today");
      const dotEl = document.getElementById("turni-status-dot");
      const statusEl = document.getElementById("turni-status-text");
      const rateEl = document.getElementById("turni-rate-min");
      const shiftEl = document.getElementById("turni-shift-label");

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

      function tick() {{
        const ended = shiftEnd && Date.now() >= Date.parse(shiftEnd);
        const extra = elapsedSeconds() * rateSec;
        monthEl.textContent = money(startMonth + extra);
        todayEl.textContent = money(startToday + extra);
        if (ended) {{
          dotEl.style.background = "#64748b";
          dotEl.style.boxShadow = "none";
          statusEl.textContent = "Fuori turno";
          rateEl.textContent = "0.000 €/min";
          shiftEl.textContent = "—";
        }}
      }}

      tick();
      setInterval(tick, 1000);
    </script>
    """, height=126)


def render_turni_guadagni_section():
    st.markdown('<div class="section-pill">⏱️ Guadagni Turni</div>', unsafe_allow_html=True)
    rules = get_turni_rules()
    df_turni = load_turni_data()
    stats = compute_turni_dashboard(df_turni, rules)

    render_live_turni_kpis(stats)

    tab_cal, tab_rules = st.tabs(["📅 Turni", "⚙️ Regole"])

    with tab_cal:
        st.markdown('<div class="turni-compact-row">', unsafe_allow_html=True)
        tool_col, fest_col = st.columns([1.7, 0.8], gap="small")
        with tool_col:
            tool = st.radio(
                "Turno da assegnare",
                ["Mattina", "Pomeriggio", "Notte", "Ferie", "Cancella"],
                horizontal=True,
                key="turni_tool_radio"
            )
        with fest_col:
            st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
            festivo_manual = st.checkbox("Festivo manuale", key="turni_festivo_manual")
        st.markdown('</div>', unsafe_allow_html=True)

        if "turni_calendar_month" not in st.session_state:
            today_month = datetime.now().date()
            st.session_state.turni_calendar_month = datetime(today_month.year, today_month.month, 1).date()

        selected_month = st.session_state.turni_calendar_month
        year, month = selected_month.year, selected_month.month
        month_key = f"{year}-{month:02d}"

        cal_col, summary_col = st.columns([1.55, 0.55], gap="medium")

        with cal_col:
            st.markdown('<div class="turni-calendar-wrap">', unsafe_allow_html=True)
            prev_col, title_col, next_col = st.columns([0.16, 0.68, 0.16], gap="small")
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
                        current_label = f"  {info['emoji']} {info['short']}" if turno_corrente in TURNI_ORARI and turno_corrente else ""
                    day_is_festive = day.weekday() == 6 or (not row.empty and bool(row.iloc[0]["Festivo"]))
                    day_label = f":red[{day.day}]" if day_is_festive else str(day.day)
                    if c.button(f"{day_label}{current_label}", key=f"turno_day_{day_str}", use_container_width=True):
                        df_new = df_turni[df_turni["Data"] != day_str].copy()
                        if festivo_manual:
                            turno_esistente = "" if row.empty else str(row.iloc[0].get("Turno", ""))
                            df_new = pd.concat([df_new, pd.DataFrame([{
                                "Data": day_str,
                                "Turno": turno_esistente if turno_esistente in TURNI_ORARI else "",
                                "Festivo": True
                            }])], ignore_index=True)
                        elif tool != "Cancella":
                            df_new = pd.concat([df_new, pd.DataFrame([{
                                "Data": day_str,
                                "Turno": tool,
                                "Festivo": False
                            }])], ignore_index=True)
                        set_turni_draft(df_new)
                        st.rerun()

            st.markdown("""
            <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; font-size:12px; color:rgba(255,255,255,0.55);">
              <span>🔵 Mattina</span><span>🟠 Pomeriggio</span><span>⚫ Notte</span><span>🟢 Ferie</span><span style="color:#ef4444;">Numero rosso = festivo</span>
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
                cards = ['<div class="turni-grid-scroll">']
                for _, r in month_df.iterrows():
                    turno = r["Turno"]
                    info = _turno_color_info(turno)
                    calc = compute_turno(r["Data"], turno, bool(r["Festivo"]), rules, until=datetime.max.replace(tzinfo=None))
                    seg = _segmenti_turno(r["Data"], turno, bool(r["Festivo"]))
                    festivo_txt = " · festivo manuale" if bool(r["Festivo"]) else ""
                    cards.append(
                        f'<div class="turni-card-small {info["class"]}">'
                        f'<div class="date">{r["Data"]}{festivo_txt}</div>'
                        f'<div class="title" style="color:{info["color"]};">{info["emoji"]} {turno}</div>'
                        f'<div class="meta">{seg} · Totale {_money_turni(calc["total"])}</div>'
                        f'<div class="meta">Base {_money_turni(calc["base"])} · Extra {_money_turni(calc["extra"])}</div>'
                        f'</div>'
                    )
                cards.append("</div>")
                st.markdown("".join(cards), unsafe_allow_html=True)

        st.markdown("---")
        if st.session_state.get("turni_dirty", False):
            st.warning("Modifiche turni non ancora salvate su Google Sheets.")
        save_col, reload_col = st.columns(2)
        with save_col:
            if st.button("💾 Salva modifiche turni su Google Sheets", use_container_width=True, key="turni_save_all"):
                if save_turni_data(st.session_state.turni_df_draft):
                    st.success("Turni salvati su Google Sheets")
                    st.rerun()
                else:
                    st.error("Errore nel salvataggio turni")
        with reload_col:
            if st.button("🔄 Ricarica turni da Google Sheets", use_container_width=True, key="turni_reload_sheet"):
                load_turni_data(force_reload=True)
                st.rerun()

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
        st.caption("Le regole sono salvate nella sessione Streamlit. I turni restano in bozza mentre clicchi i giorni e vengono scritti su Google Sheets solo quando premi 'Salva modifiche turni'.")
# ─────────────────────────────────────────────────────────────────────────────

def main():

    col_left, col_center, col_right = st.columns([1, 2, 1])
    with col_left:
        st.markdown('<div class="section-pill">💎 Dashboard Finanziaria</div>', unsafe_allow_html=True)
    with col_center:
        st.markdown("<h1 style='text-align: center;'>Calcolatore di Spese Personali</h1>", unsafe_allow_html=True)

    col_stip_inserimento1, col_stip_inserimento2, col_stip_inserimento3, col_stip_inserimento4 = st.columns([1, 1, 1, 2])
    col1, col2, col3 = st.columns([1, 2, 2])

    with col_stip_inserimento1:
        stipendio_originale = st.number_input("Inserisci il tuo stipendio mensile:", min_value=input_stipendio_originale, step=50)
        risparmi_mese_precedente = st.number_input("Inserisci quanto hai risparmiato nel mese precedente:", min_value=input_risparmi_mese_precedente, step=50)
    with col_stip_inserimento2:
        st.markdown('<div style="height: 40px;"></div>', unsafe_allow_html=True)
        stipendio_scelto = st.number_input("Inserisci il tuo stipendio mensile che scegli di usare:", min_value=input_stipendio_scelto, step=50)
        st.markdown('<div style="height: 45px;"></div>', unsafe_allow_html=True)
    with col_stip_inserimento3:
        st.markdown('<div style="height: 30px;"></div>', unsafe_allow_html=True)
    
        tot_stipendio = stipendio_originale + sum(ALTRE_ENTRATE.values())
        tot_utilizzare = stipendio_scelto + sum(ALTRE_ENTRATE.values())
    
        _ts = f"€{tot_stipendio:,.2f}"
        _tu = f"€{tot_utilizzare:,.2f}"
    
        # ───────── Divisione in 2 colonne ─────────
        col_stip_inserimento3_1, col_stip_inserimento3_2 = st.columns(2)
    
        # ───────── Prima card ─────────
        with col_stip_inserimento3_1:
            st.markdown(f"""
            <div class="kpi-card">
                <div class="kpi-label">Stipendio Totale</div>
                <div class="kpi-value" style="color:#77DD77;">{_ts}</div>
                <div style="font-size:11px;color:rgba(255,255,255,0.3);margin-top:3px;">
                    Originale + Altre Entrate
                </div>
            </div>
            """, unsafe_allow_html=True)
    
        # ───────── Seconda card ─────────
        with col_stip_inserimento3_2:
            st.markdown(f"""
            <div class="kpi-card">
                <div class="kpi-label">Stipendio da Utilizzare</div>
                <div class="kpi-value" style="color:#60a5fa;">{_tu}</div>
                <div style="font-size:11px;color:rgba(255,255,255,0.3);margin-top:3px;">
                    Scelto + Altre Entrate
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
            }
            </style>
            """, unsafe_allow_html=True)
        
            # ───────── CONFIG ─────────
            NOTE_HEADERS = ["id", "nota1", "nota2", "nota3", "nota4"]
            worksheet_name = "Note"

            if "note_df_draft" not in st.session_state:
                df_note = load_data_gsheets(worksheet_name, NOTE_HEADERS)
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
                        "nota4": ""
                    }])
                st.session_state.note_df_draft = df_note[NOTE_HEADERS].copy()

            df_note = st.session_state.note_df_draft.copy()
            if df_note.empty:
                df_note = pd.DataFrame([{
                    "id": 1,
                    "nota1": "",
                    "nota2": "",
                    "nota3": "",
                    "nota4": ""
                }])
            nota_corrente = df_note.iloc[0]

            def _nota_value(key):
                value = nota_corrente.get(key, "")
                return "" if pd.isna(value) else str(value)
        
            # ───────── UI ─────────
            st.markdown(
                '<div class="section-pill">📝 Promemoria</div>',
                unsafe_allow_html=True
            )
            col1_postit, col2_postit, col3_postit, col4_postit = st.columns(4)
            with col1_postit:
                nota1 = st.text_area("Nota 1", value=_nota_value("nota1"), height=150, label_visibility="collapsed", key="nota1_text")
            with col2_postit:
                nota2 = st.text_area("Nota 2", value=_nota_value("nota2"), height=150, label_visibility="collapsed", key="nota2_text")
            with col3_postit:
                nota3 = st.text_area("Nota 3", value=_nota_value("nota3"), height=150, label_visibility="collapsed", key="nota3_text")
            with col4_postit:
                nota4 = st.text_area("Nota 4", value=_nota_value("nota4"), height=150, label_visibility="collapsed", key="nota4_text")
            # ───────── BOTTONE A DESTRA (SOTTO) ─────────
            col_spazio, col_btn = st.columns([6, 1])
            with col_btn:
                salva = st.button("💾 Salva", use_container_width=True)
            # ───────── SALVATAGGIO ─────────
            if salva:
                df_note = pd.DataFrame([{
                    "id": 1,
                    "nota1": nota1,
                    "nota2": nota2,
                    "nota3": nota3,
                    "nota4": nota4
                }])
                if save_data_gsheets(worksheet_name, NOTE_HEADERS, df_note):
                    st.session_state.note_df_draft = df_note.copy()
                    st.success("Note salvate")
                else:
                    st.error("Errore salvataggio")
            #FINE CREAZIONE NOTA

    stipendio = stipendio_scelto + sum(ALTRE_ENTRATE.values())
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
            "Categoria": ["Spese Fisse", "Spese Variabili", "Stipendio Totale", "Stipendio Totale", 
                        "Risparmi", "Stipendio Utilizzato", "Stipendio Utilizzato"],
            "Tipo": ["Spese Fisse", "Spese Variabili", "Stipendio Originale", "Altre Entrate", 
                    "Risparmi", "Stipendio Scelto", "Altre Entrate"],
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

        ordine_categorie = ["Stipendio Totale", "Stipendio Utilizzato", "Spese Fisse", "Spese Variabili", "Risparmi"]
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
                                "Stipendio Originale", "Altre Entrate", "Stipendio Scelto", 
                                "Spese Fisse", "Spese Variabili", "Risparmi"
                            ],
                            range=[
                                color_map["Stipendio Originale"], 
                                color_map["Altre Entrate"], 
                                color_map["Stipendio Utilizzato"], 
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
        st.markdown("---")
        st.markdown('<div class="section-pill">🏠 Spese Fisse</div>', unsafe_allow_html=True)
        st.subheader("Spese Fisse:")

        col_left, col_right = st.columns(2)

        with col_left:
            for voce, importo in SPESE["Fisse"].items():
                if voce in ["Mutuo"]:
                    st.markdown(f'<span style="color: #F08080;">- {voce}: €{importo:.2f}</span><span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid green; margin-left: 10px;"></span>', unsafe_allow_html=True)
                elif voce in ["Bollette"]:
                    st.markdown(f'<span style="color: #F08080;">- {voce}: €{importo:.2f}</span><span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #89CFF0; margin-left: 10px;"></span>', unsafe_allow_html=True)
                elif voce in ["Pulizia Casa"]:
                    st.markdown(f'<span style="color: #F08080;">- {voce}: €{importo:.2f}</span><span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #89CFF0; margin-left: 10px;"></span>', unsafe_allow_html=True)
                    st.markdown('<hr style="width:50%; margin-left:0;">', unsafe_allow_html=True)
                elif voce in ["Condominio"]:
                    st.markdown(f'<span style="color: #F08080;">- {voce}: €{importo:.2f}</span><span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #D2691E; margin-left: 10px;"></span>', unsafe_allow_html=True)
                elif voce in ["Beneficienza"]:
                    st.markdown(f'<span style="color: #D8BFD8;">- {voce}: €{importo:.2f}</span><span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #89CFF0; margin-left: 10px;"></span>', unsafe_allow_html=True)
                elif voce in ["World Food Programme"]:
                    st.markdown(f'<span style="color: #D8BFD8;">- {voce}: €{importo:.2f}</span><span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #D2691E; margin-left: 10px;"></span>', unsafe_allow_html=True)
                elif voce in ["Sport", "Psicologo", "Altro/C"]:
                    st.markdown(f'<span style="color: #80E6E6;">- {voce}: €{importo:.2f}</span><span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #89CFF0; margin-left: 10px;"></span>', unsafe_allow_html=True)
                elif voce in ["Altro"]:
                    st.markdown(f'<span style="color: #F08080;">- {voce}: €{importo:.2f}</span><span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #D2691E; margin-left: 10px;"></span>', unsafe_allow_html=True)
                elif voce in ["Cucina"]:
                    st.markdown(f'<span style="color: #F08080;">- {voce}: €{importo:.2f}</span><span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #D2691E; margin-left: 10px;"></span>', unsafe_allow_html=True)
                elif voce in ["Trasporti"]:
                    st.markdown(f'<span style="color: #E6C48C;">- {voce}: €{importo:.2f}</span><span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #89CFF0; margin-left: 10px;"></span>', unsafe_allow_html=True)

        with col_right:
            for voce, importo in SPESE["Fisse"].items():
                if voce in ["Disney+", "Netflix", "Spotify"]:
                    st.markdown(f'<span style="color: #CC7722;">- {voce}: €{importo:.2f}</span><span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #89CFF0; margin-left: 10px;"></span>', unsafe_allow_html=True)
                elif voce in ["BNL C.C."]:
                    st.markdown(f'<span style="color: #CC7722;">- {voce}: €{importo:.2f}</span><span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid green; margin-left: 10px;"></span>', unsafe_allow_html=True)
                elif voce in ["ING C.C."]:
                    st.markdown(f'<span style="color: #CC7722;">- {voce}: €{importo:.2f}</span><span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #D2691E; margin-left: 10px;"></span>', unsafe_allow_html=True)
                elif voce in ["MoneyFarm - PAC 5","Cometa", "Alleanza - PAC"]:
                    st.markdown(f'<span style="color: #89CFF0;">- {voce}: €{importo:.2f}</span><span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #D2691E; margin-left: 10px;"></span>', unsafe_allow_html=True)
                elif voce in ["Macchina"]:
                    st.markdown(f'<span style="color: #E6C48C;">- {voce}: €{importo:.2f}</span><span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #D2691E; margin-left: 10px;"></span>', unsafe_allow_html=True)
                    st.markdown('<hr style="width:50%; margin-left:0;">', unsafe_allow_html=True)

        st.markdown("---")
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
                <div style="font-size:10px;color:rgba(255,255,255,0.3);margin-top:3px;">{_sfp}% dello stipendio da utilizzare</div>
                <div style="font-size:10px;color:rgba(255,255,255,0.3);margin-top:3px;">{_sfpo}% dello stipendio totale</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Risparmiabili ≥ Spese Variabili</div>
                <div class="kpi-value" style="color:#fef3c7;">{_ri}</div>
                <div style="font-size:10px;color:rgba(255,255,255,0.3);margin-top:3px;">{_rip}% dello stipendio da utilizzare</div>
                <div style="font-size:10px;color:rgba(255,255,255,0.3);margin-top:3px;">{_ripo}% dello stipendio totale</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        stipendio_totale = stipendio_originale + sum(ALTRE_ENTRATE.values())
        stipendio_utilizzare = stipendio_scelto + sum(ALTRE_ENTRATE.values())

        df_totale = pd.DataFrame({
            'Component': ['Spese Fisse', 'Risparmiabili', 'Risparmio Stipendi'],
            'Value': [spese_fisse_totali, risparmiabili, risparmio_stipendi]
        })
        df_utilizzare = pd.DataFrame({
            'Component': ['Spese Fisse', 'Risparmiabili'],
            'Value': [spese_fisse_totali, stipendio_utilizzare - spese_fisse_totali]
        })

        df_totale["Percentuale"] = (df_totale["Value"] / df_totale["Value"].sum()) * 100
        df_utilizzare["Percentuale"] = (df_utilizzare["Value"] / df_utilizzare["Value"].sum()) * 100

        # FIX 3: Stipendio Totale donut - labels outside
        chart_totale = alt.Chart(df_totale).mark_arc(innerRadius=35, outerRadius=60).encode(
            theta=alt.Theta(field="Value", type="quantitative"),
            color=alt.Color(
                field="Component", type="nominal", 
                scale=alt.Scale(
                    domain=['Spese Fisse', 'Risparmiabili', 'Risparmio Stipendi'], 
                    range=['rgba(255, 100, 100, 0.3)', 'rgba(184, 192, 112, 0.3)', 'rgba(128, 128, 128, 0.3)']
                ),
                legend=None
            ),
            tooltip=[
                alt.Tooltip("Component:N", title="Categoria"),
                alt.Tooltip("Value:Q", title="Valore (€)", format=".2f"),
                alt.Tooltip("Percentuale:Q", title="Percentuale", format=".2f")
            ]
        ).properties(title="Stipendio Totale", width=150, height=150)

        # Filter zero/negative values to avoid broken donuts
        df_totale_clean = df_totale[df_totale["Value"] > 0].copy()
        df_utilizzare_clean = df_utilizzare[df_utilizzare["Value"] > 0].copy()

        chart_totale_clean = alt.Chart(df_totale_clean).mark_arc(innerRadius=40, outerRadius=70).encode(
            theta=alt.Theta(field="Value", type="quantitative"),
            color=alt.Color(
                field="Component", type="nominal",
                scale=alt.Scale(
                    domain=['Spese Fisse', 'Risparmiabili', 'Risparmio Stipendi'],
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
                alt.Tooltip("Percentuale:Q", title="% sul Totale", format=".1f")
            ]
        ).properties(
            title=alt.TitleParams(
                "Stipendio Totale",
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
                scale=alt.Scale(domain=['Spese Fisse', 'Risparmiabili'], range=['#FF6961', '#fef3c7']),
                legend=alt.Legend(
                    title=None, orient='bottom', direction='vertical',
                    labelColor='rgba(255,255,255,0.65)', labelFontSize=10,
                    symbolSize=60, padding=4
                )
            ),
            tooltip=[
                alt.Tooltip("Component:N", title="Categoria"),
                alt.Tooltip("Value:Q", title="Valore (€)", format=".2f"),
                alt.Tooltip("Percentuale:Q", title="% su Scelto", format=".1f")
            ]
        ).properties(
            title=alt.TitleParams(
                "Stipendio da Utilizzare",
                anchor='middle',   # <-- centra il titolo
                color='rgba(255,255,255,0.7)',
                fontSize=12
            ),
            width=160,
            height=160
        )


        chart_donut = (chart_totale_clean | chart_utilizzare_clean).resolve_scale(color='independent')

        st.markdown('<div style="height: 20px;"></div>', unsafe_allow_html=True)
        st.markdown("**💶 Distribuzione Stipendi:**")
        st.altair_chart(chart_donut, use_container_width=True)

    # --- COLONNA 2: SPESE VARIABILI ---
    with col2:
        col2_left, col2_right = st.columns([1, 1])
        with col2_left:
            st.markdown("---")
            st.markdown('<div class="section-pill">💸 Spese Variabili</div>', unsafe_allow_html=True)
            st.subheader("Spese Variabili:")
    
            da_spendere = 0
            spese_quotidiane = 0
            spese_variabili_totali = SPESE["Variabili"]["Emergenze/Compleanni"] + SPESE["Variabili"]["Viaggi"] + SPESE["Variabili"]["Da spendere"] + SPESE["Variabili"]["Spese quotidiane"]
    
            risparmio_stipendi = stipendio_originale - stipendio_scelto
            risparmio_da_spendere = 0
            risparmio_spese_quotidiane = 0
    
            for voce, importo in SPESE["Variabili"].items():
                if voce in ["Emergenze/Compleanni"]:
                    percentuale_emergenze = percentuali_variabili.get("Emergenze/Compleanni", 0) * 100
                    st.markdown(color_text(f"- {voce}: €{importo:.2f}<span style=\"display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #89CFF0; margin-left: 10px;\"></span>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;", "#4ADE80") + f'<span style="margin-right: 20px; color:#808080;">- {percentuale_emergenze:.2f}% dei Risparmiabili</span>', unsafe_allow_html=True)
                elif voce in ["Viaggi"]:
                    percentuale_viaggi = percentuali_variabili.get("Viaggi", 0) * 100
                    st.markdown(color_text(f"- {voce}: €{importo:.2f}<span style=\"display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #89CFF0; margin-left: 10px;\"></span>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;", "#166534") + f'<span style="margin-right: 20px; color:#808080;">- {percentuale_viaggi:.2f}% dei Risparmiabili</span>', unsafe_allow_html=True)
                elif voce in ["Spese quotidiane"]:
                    percentuale_da_spendere = (SPESE["Variabili"]["Da spendere"] / risparmiabili * 100) if risparmiabili != 0 else 0
                    st.markdown(color_text(f"- {voce}: €{importo:.2f}<span style=\"display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #89CFF0; margin-left: 10px;\"></span>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;", "#FB923C") + f'<span style="margin-right: 20px; color:#808080;">- il rimanente &nbsp;&nbsp;(con un limite a {max_spese_quotidiane})</span>', unsafe_allow_html=True)
                elif voce in ["Da spendere"]:
                    spese_emergenze_viaggi = SPESE["Variabili"]["Emergenze/Compleanni"] + SPESE["Variabili"]["Viaggi"]
                    risparmiabili_dopo_emergenze_viaggi = risparmiabili - spese_emergenze_viaggi
                    st.markdown(color_text(f"- {voce}: €{importo:.2f}<span style=\"display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #89CFF0; margin-left: 10px;\"></span>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;", "#FACC15") + f'<span style="margin-right: 20px; color:#808080;">- {(da_spendere_senza_limite*100/risparmiabili_dopo_emergenze_viaggi if risparmiabili_dopo_emergenze_viaggi != 0 else 0):.2f}% &nbsp;&nbsp; del rimanente €{risparmiabili_dopo_emergenze_viaggi:.2f} &nbsp;&nbsp; (con un limite a {limite_da_spendere}€)</span>', unsafe_allow_html=True)
                else:
                    st.write(f"- {voce}: €{importo:.2f}")
                if voce == "Da spendere":
                    da_spendere = min(da_spendere_senza_limite, limite_da_spendere)
                    risparmio_da_spendere = da_spendere_senza_limite - da_spendere
                    st.markdown(color_text(f'<small>- {voce} (reali): €{da_spendere_senza_limite:.2f} -> Risparmiati: €{risparmio_da_spendere:.2f}</small>', "#808080"), unsafe_allow_html=True)
                if voce == "Spese quotidiane":
                    spese_quotidiane = min(spese_quotidiane_senza_limite, max_spese_quotidiane)
                    risparmio_spese_quotidiane = spese_quotidiane_senza_limite - spese_quotidiane
                    st.markdown(color_text(f'<small>- {voce} (reali): €{spese_quotidiane_senza_limite:.2f} -> Risparmiati: €{risparmio_spese_quotidiane:.2f}</small>', "#808080"), unsafe_allow_html=True)
    
    
            st.markdown("---")
            col_spese_variabili_1, col_spese_variabili_2 = st.columns([1.2, 2])
            with col_spese_variabili_1:
                _sv = f"€{spese_variabili_totali:.2f}"
                _sv_st_risp = f"€{spese_variabili_totali/risparmiabili*100:.1f}"
                _sv_st_util = f"€{spese_variabili_totali/stipendio_utilizzare*100:.1f}"
                _sv_st_tot = f"€{spese_variabili_totali/stipendio_totale*100:.2f}"
                st.markdown(f"""
                <div class="kpi-card">
                    <div class="kpi-label">Totale Spese Variabili</div>
                    <div class="kpi-value" style="color:#fde047;">{_sv}</div>
                    <div style="font-size:10px;color:rgba(255,255,255,0.3);margin-top:3px;">{_sv_st_risp}% dei Risparmiabili</div>
                    <div style="font-size:10px;color:rgba(255,255,255,0.3);margin-top:3px;">{_sv_st_util}% dello Stipendio da Utilizzare</div>
                    <div style="font-size:10px;color:rgba(255,255,255,0.3);margin-top:3px;">{_sv_st_tot}% dello Stipendio Totale</div>
                </div>
                """, unsafe_allow_html=True)

                st.markdown('<div style="height: 20px;"></div>', unsafe_allow_html=True)

                progresso_altre_entrate = spese_variabili_totali / risparmiabili if risparmiabili > 0 else 0
                progresso_altre_entrate = min(progresso_altre_entrate, 1.0)
                st.progress(progresso_altre_entrate)
                st.markdown(f"""
                <div style="font-size:11px; color:rgba(255,255,255,0.4); margin-top:5px;">
                Spese Variabili rispetto ai Risparmiabili: €{spese_variabili_totali:,.2f} / €{risparmiabili:,.2f}
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
        st.markdown('<hr style="width: 100%; height:1px;border-width:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.18),transparent);">', unsafe_allow_html=True)
    
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
            st.markdown("---")
            col_altre_entrate_sx, col_altre_entrate_dx, col_altre_entrate_vuoto = st.columns([1, 1, 0.1])
            totale_altre = sum(ALTRE_ENTRATE.values())
            _ae = f"€{totale_altre:.2f}" 
            with col_altre_entrate_sx:
                st.markdown('<div class="section-pill">➕ Altre Entrate</div>', unsafe_allow_html=True)
                st.subheader("Altre Entrate:")
                for voce, importo in ALTRE_ENTRATE.items():
                    if voce in ["Macchina (Mamma)"]:
                        st.markdown(color_text(f"- {voce}: €{importo:.2f} {triangolino_verde_BNL}", "#E6C48C"), unsafe_allow_html=True)
                    elif voce in ["Altro"]:
                        st.markdown(color_text(f"- {voce}: €{importo:.2f} {triangolino_verde_BNL}", "#89CFF0"), unsafe_allow_html=True)
                    elif voce in ["2° Entr. dal mese prec."]:
                        st.markdown(color_text(f"- {voce}: €{importo:.2f} {triangolino_verde_BNL}", "#D8BFD8"), unsafe_allow_html=True)
                    else:
                        st.write(f"- {voce}: €{importo:.2f}")
            with col_altre_entrate_dx:
                totale_entrate_target = stipendio_originale / totale_entrate_target_oltre_lo_stipendio
                altre_entrate_target = totale_entrate_target - stipendio_originale

                progresso = totale_altre / altre_entrate_target if altre_entrate_target > 0 else 0
                progresso = min(progresso, 1.0)
            
                st.markdown("### 🎯 Obiettivo Entrate")

                percentuale_stip = stipendio_originale / totale_entrate_target * 100
                st.markdown(f"""
                <div style="font-size:13px; color:rgba(255,255,255,0.6);">
                Entrate totali desiderate<br>
                <b style="color:white; font-size:18px;">
                €{totale_entrate_target:,.2f}
                <span style="font-size:11px; color:rgba(255,255,255,0.4);">
                &nbsp;&nbsp;Stipendio = {percentuale_stip:.0f}% delle entrate totali
                </span>
                </b>
                </div>
                """, unsafe_allow_html=True)  
                
                st.markdown(f"""
                <div style="font-size:13px; color:rgba(255,255,255,0.6); margin-top:10px;">
                Altre entrate target<br>
                <b style="color:#8fe28f; font-size:18px;">€{altre_entrate_target:,.2f}</b>
                </div>
                """, unsafe_allow_html=True)
            
                st.markdown("<div style='margin-top:15px'></div>", unsafe_allow_html=True)
            
                st.progress(progresso)
            
                st.markdown(f"""
                <div style="font-size:11px; color:rgba(255,255,255,0.4); margin-top:5px;">
                Attuale: €{totale_altre:,.2f} / €{altre_entrate_target:,.2f}
                </div>
                """, unsafe_allow_html=True)
                
            st.markdown("---")
            col_altre_entrate_1, col_altre_entrate_2 = st.columns([1, 2])
            percentuale_altre_su_totale_altre = totale_altre/altre_entrate_target
            _ae_ipot = f"{percentuale_altre_su_totale_altre *100:.2f}"                    
            with col_altre_entrate_1:
                st.markdown(f"""
                <div class="kpi-card" style="border-color:rgba(52,211,153,0.2);">
                    <div class="kpi-label">Totale Altre Entrate</div>
                    <div class="kpi-value" style="color:#77DD77;">{_ae}</div>
                    <div style="font-size:10px;color:rgba(255,255,255,0.3);margin-top:3px;">{_ae_ipot}% di Obiettivo Entrate</div>
                </div>
                """, unsafe_allow_html=True)
                st.markdown('<div style="height: 20px;"></div>', unsafe_allow_html=True)
            
            with col_altre_entrate_2:
                # --- Grafico ---
                # Creo il DataFrame per il grafico delle altre entrate
                df_altre_entrate = pd.DataFrame({
                    'Voce': list(ALTRE_ENTRATE.keys()),
                    'Value': list(ALTRE_ENTRATE.values())
                })
            
                # Solo voci con importo > 0
                df_altre_entrate = df_altre_entrate[df_altre_entrate["Value"] > 0].copy()
            
                # Calcolo le percentuali relative alle altre entrate
                totale_entrate = df_altre_entrate["Value"].sum()
                df_altre_entrate["Percentuale"] = (df_altre_entrate["Value"] / totale_entrate * 100).round(1) if totale_entrate != 0 else 0
            
                if not df_altre_entrate.empty:
                    chart_altre_entrate = alt.Chart(df_altre_entrate).mark_arc(
                        innerRadius=40, outerRadius=70
                    ).encode(
                        theta=alt.Theta(field="Value", type="quantitative"),
                        color=alt.Color(
                            field="Voce", type="nominal",
                            scale=alt.Scale(
                                domain=list(ALTRE_ENTRATE.keys()),
                                range=['#E6C48C', '#D8BFD8', '#89CFF0', '#A78BFA'][:len(ALTRE_ENTRATE)]  # colori personalizzabili
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
                        title="➕ Distribuzione Altre Entrate",
                        width=200,
                        height=220
                    ).configure_title(
                        anchor='middle'
                    ).configure_view(
                        strokeWidth=0,
                        fill='transparent'
                    )
            
                    st.altair_chart(chart_altre_entrate, use_container_width=True)
    
        # Visualizzazione grafici
        col_center_pill = st.columns([1, 2, 1])[1]
        with col_center_pill:
            st.markdown('<div class="section-pill">🏠 Spese Fisse</div>',unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)
            
        col_vuoto_a, col1_1, col1_2, col_vuoto_b= st.columns([0.07, 0.5, 1, 0.1])
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
            df_fisse_percentuali = df_fisse_percentuali.rename(columns={'Importo': 'Valore €'})
            df_fisse_percentuali["Valore €"] = df_fisse_percentuali["Valore €"].apply(lambda x: f"€ {x:.2f}")
            styled_df_fisse = (
                df_fisse_percentuali[["Categoria", "Valore €", "Percentuale"]].style
                .apply(lambda x: [f"background-color: {color_map.get(x.name, '')}" for i in x], axis=1)
                .map(lambda x: f"color: {color_map.get(x, '')}" if x in df_fisse_percentuali["Categoria"].unique() else "", subset=["Categoria"])
                .set_properties(**{'text-align': 'center'})
            )
            st.dataframe(styled_df_fisse, use_container_width=True)
    
                

    with col3:
        col3_left, col3_right = st.columns([1, 1])
        with col3_left:
            st.markdown("---")
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
            
            def riga(voce, valore, colore, triangolino, extra=""):
                return f"""
                <div style="
                    color:{colore};
                    margin-bottom:4px;
                ">
                    - {voce}: €{valore:.2f} {triangolino}
                </div>
                {extra}
                """
    
            # Stipendi + Mese precedente + Da spendere + Quotidiane
            html_risparmi = ""
            html_risparmi += riga("Dallo Stipendio Originale", v1, "#9ca3af", triangolino_verde_BNL)
            html_risparmi += riga("Dal Mese Precedente", v2, "#60a5fa", triangolino_verde_BNL)
            html_risparmi += riga("Dai 'Da Spendere'", v3, "#fde047", triangolino_verde_BNL)
            html_risparmi += riga("Dalle 'Spese Quotidiane'", v4, "#FB923C", triangolino_verde_BNL)            
            st.markdown(html_risparmi, unsafe_allow_html=True)
            st.markdown("---")
            
            col_risparmi_1, col_risparmi_2 = st.columns([1, 2])
            with col_risparmi_1:
                st.markdown(f"""
                <div class="kpi-card" style="border-color:rgba(52,211,153,0.25);">
                    <div class="kpi-label">Tot. Risparmiato</div>
                    <div class="kpi-value" style="color:#34d399;">{kpi_val}</div>
                    <div style="font-size:10px;color:rgba(255,255,255,0.3);margin-top:3px;">{kpi_pct}% dello Stipendio da Utilizzare</div>
                    <div style="font-size:10px;color:rgba(255,255,255,0.3);margin-top:3px;">{kpi_pctot}% dello Stipendio Totale</div>
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
                    chart_savings_arc = alt.Chart(df_savings).mark_arc(innerRadius=40, outerRadius=70).encode(
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
                        width=200,
                        height=220
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
            st.markdown("---")
            st.markdown('<div class="section-pill">💳 Trasferimenti Carte</div>', unsafe_allow_html=True)
            col_Distribuzione_Carte_1, col_Distribuzione_Carte_2 = st.columns([1, 0.8])
            with col_Distribuzione_Carte_1:
                st.subheader("Trasferimenti sulle Carte:")
        
                for carta in ["ING", "Revolut", "BNL"]:
                    spese_carta = {voce: SPESE["Fisse"].get(voce, 0) + SPESE["Variabili"].get(voce, 0) 
                                   for voce in SPESE[carta]}
                    spese_carta = {voce: importo for voce, importo in spese_carta.items() if importo != 0}
                    if carta == "Revolut":
                        totale_carta = revolut_expenses  # Usa il valore modificato per Revolut
                        colore = "#89CFF0"  # Azzurro
                        testo = "trasferire"
                        somma_spese_programmate_immediate = SPESE["Fisse"]["Psicologo"] + SPESE["Fisse"]["Sport"] + SPESE["Fisse"]["Altro/C"] + SPESE["Fisse"]["Trasporti"] + SPESE["Fisse"]["Bollette"] + SPESE["Fisse"]["Beneficienza"] + SPESE["Fisse"]["Pulizia Casa"] + SPESE["Fisse"]["Disney+"] + SPESE["Fisse"]["Netflix"] + SPESE["Fisse"]["Spotify"]
                        spese_che_anticipo_per_un_giorno_di_disney_spotify=18
                        somma_valori = risparmi_mese_precedente - somma_spese_programmate_immediate - spese_che_anticipo_per_un_giorno_di_disney_spotify + totale_carta
                        st.markdown(f'Totale da &nbsp; **<em style="color: #A0A0A0;">{testo}</em> &nbsp; su <span style="color:{colore}; text-decoration: underline;">{carta}</span>:** <span style="color:{colore}">€{totale_carta:.2f}</span> <span style="font-size: 11px; color: gray;"> <br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;( + <span style="color:{colore}; font-size: 11px;">{risparmi_mese_precedente:.2f}</span> dai Risparmi - (<span style="color:{colore}; font-size: 11px;">€{somma_spese_programmate_immediate:.2f} - {spese_che_anticipo_per_un_giorno_di_disney_spotify:.2f}</span>) -> Vedrai: <span style="color:{colore}; font-size: 11px;">€{somma_valori:.2f}</span> )</span>', unsafe_allow_html=True)
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
                        st.markdown(f'Totale da &nbsp; **<em style="color: #A0A0A0;">{testo}</em> &nbsp; su <span style="color:{colore}; text-decoration: underline;">{carta}</span>:** <span style="color:{colore}">€{totale_carta:.2f}</span>', unsafe_allow_html=True)
                st.markdown(f'Totale &nbsp; **<em style="color: #A0A0A0;">{testo2}</em> &nbsp; su <span style="color:{colore}; text-decoration: underline;">{carta}</span>:** <span style="color:{colore2}">€{risparmi_mensili:.2f}</span>', unsafe_allow_html=True)
    
            # FIX 4: NEW "Carte" donut chart
            with col_Distribuzione_Carte_2:  
                # Calculate totals per card
                ing_total = sum(SPESE["Fisse"].get(v, 0) + SPESE["Variabili"].get(v, 0) for v in SPESE["ING"])
                revolut_total = revolut_expenses + risparmi_mese_precedente  # original before subtraction
                bnl_total = sum(SPESE["Fisse"].get(v, 0) + SPESE["Variabili"].get(v, 0) for v in SPESE["BNL"])
        
                df_carte = pd.DataFrame({
                    'Carta': ['ING', 'Revolut', 'BNL', 'Risparmiato BNL'],
                    'Totale': [ing_total, revolut_total, bnl_total, risparmi_mensili]
                        })
                df_carte['Percentuale'] = (df_carte['Totale'] / df_carte['Totale'].sum() * 100).round(1)
        
                carte_arc = alt.Chart(df_carte).mark_arc(innerRadius=40, outerRadius=70).encode(
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
                    width=200,
                    height=220,
                ).configure_title(
                    anchor='middle'
                ).configure_view(
                    strokeWidth=0,
                    fill='transparent',
                )    
        
                chart_carte = carte_arc.resolve_scale(color='independent')
                st.altair_chart(chart_carte, use_container_width=True)
            st.markdown("---")  
        st.markdown("---")
        render_turni_guadagni_section()

if __name__ == "__main__":
    main()

st.markdown('<hr style="width: 100%; height:1px;border-width:0;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.18),transparent);">', unsafe_allow_html=True)


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
        tooltip=["Mese_str:N", "Categoria:N", "Valore:Q"]
    )

    # FIX 1: Risparmi overlaid ON TOP of Messi da parte (same x position, smaller/different color)
    base_bar_risparmi = alt.Chart(df_risparmi).mark_bar(size=40, color="rgba(255,165,0,0.6)", opacity=0.9).encode(
        x=alt.X("Mese_str:N", sort=ordine_mesi),
        y=alt.Y("Valore:Q"),
        tooltip=["Mese_str:N", "Categoria:N", "Valore:Q"]
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
    
    barre = base_stack.mark_bar(opacity=0.8).encode(
        x=alt.X("Mese_str:N", sort=order, title="Mese", axis=alt.Axis(labelAngle=-45)),
        y=alt.Y("lower:Q", title="Valore (€)"),
        y2="upper:Q",
        color=alt.Color("Categoria:N", scale=alt.Scale(
            domain=["Elettricità", "Gas", "Acqua", "Internet", "Tari"],
            range=["#84B6F4", "#FF6961", "#96DED1", "#FFF5A1", "#C19A6B"]),
            legend=alt.Legend(title="Bollette")),
        tooltip=["Mese_str:N", "Categoria:N", "Valore:Q"]
    )
    
    labels = base_stack.transform_filter("datum.Valore > 0").transform_calculate(
        mid="(datum.lower + datum.upper) / 2"
    ).mark_text(color="black", align="center", baseline="middle").encode(
        x=alt.X("Mese_str:N", sort=order),
        y=alt.Y("mid:Q"),
        text=alt.Text("Valore:Q", format=".2f")
    )
    
    df_saldo = data_completa[data_completa["Categoria"] == "Saldo"]
    linea_saldo_unica = alt.Chart(df_saldo).mark_line(strokeWidth=2, strokeDash=[5,5], color="#F0F0F0", opacity=0.25).encode(
        x=alt.X("Mese_str:N", sort=order),
        y=alt.Y("Valore:Q"),
        tooltip=["Mese_str:N", "Valore:Q"]
    )

    punti_saldo_color = alt.Chart(df_saldo).mark_point(shape="diamond", size=80, filled=True).encode(
        x=alt.X("Mese_str:N", sort=order),
        y=alt.Y("Valore:Q"),
        color=alt.condition("datum.Valore < 0", alt.value("#FF6961"), alt.value("#77DD77")),
        tooltip=["Mese_str:N", "Valore:Q"]
    )

    df_totali = data_completa[data_completa["Categoria"].isin(["Elettricità", "Gas", "Acqua", "Internet", "Tari"])].groupby(
        ["Mese", "Mese_str"], as_index=False
    )["Valore"].sum()
    
    testo_totale = alt.Chart(df_totali).mark_text(
        align="center", baseline="bottom", dy=-5, fontSize=12, color="white"
    ).encode(
        x=alt.X("Mese_str:N", sort=order),
        y=alt.Y("Valore:Q"),
        text=alt.Text("Valore:Q", format=".2f")
    )
    
    linea_saldo = linea_saldo_unica + punti_saldo_color
    grafico_finale = alt.layer(barre, labels, linea_saldo, testo_totale)
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

col_sx_stip, col_cx_stip_vuoto, col_dx_stip_chart = st.columns([1, 1, 2])
with col_sx_stip:
    st.subheader("Inserisci Dati")
    mesi_anni = pd.date_range(start="2024-03-01", end="2030-12-01", freq="MS").strftime("%B %Y")
    selected_mese = st.selectbox("Seleziona il mese e l'anno", mesi_anni, key="mese_stipendi")
    mese_dt = datetime.strptime(selected_mese, "%B %Y")

    if data_stipendi.empty:
        data_stipendi = pd.DataFrame(columns=["Mese", "Stipendio", "Risparmi", "Messi da parte Totali"])

    record_esistente = data_stipendi[data_stipendi["Mese"] == mese_dt] if not data_stipendi.empty else pd.DataFrame()
    stipendio_val = float(record_esistente["Stipendio"].iloc[0]) if not record_esistente.empty else 0.0
    risparmi_val = float(record_esistente["Risparmi"].iloc[0]) if not record_esistente.empty else 0.0
    messi_da_parte_mese_corrente_val = float(record_esistente["Messi da parte Totali"].iloc[0]) if not record_esistente.empty else 0.0

    col_input1, col_input2 = st.columns(2)
    with col_input1:
        stipendio = st.number_input("Stipendio (€)", min_value=0.0, step=100.0, value=stipendio_val, key="stipendio_input")
        aggiungi_button = st.button("Aggiungi/Modifica Dati", key="aggiorna_stipendi")
    with col_input2:
        risparmi = st.number_input("Risparmi mese prec. (€)", min_value=0.0, step=100.0, value=risparmi_val, key="risparmi_input")
        messi_da_parte_mese_corrente = st.number_input("Messi da parte Totali (Risp. su BNL) (€)", min_value=0.0, step=100.0, value=messi_da_parte_mese_corrente_val, key="messi_da_parte_input")
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

col_table, col_chart = st.columns([1.3, 3])
with col_table:
    df_stip = data_stipendi.copy()
    if not df_stip.empty:
        df_stip["Mese"] = df_stip["Mese"].dt.strftime("%B %Y")
    st.dataframe(
        df_stip,
        use_container_width=True,
        column_config={
            "Messi da parte Totali": st.column_config.NumberColumn(
                "Messi da parte",
                width="medium"
            ),
            "Mese": st.column_config.TextColumn("Mese", width="medium"),
            "Stipendio": st.column_config.NumberColumn("Stipendio", width="small"),
            "Risparmi": st.column_config.NumberColumn("Risparmi", width="small"),
        }
    )
    
    data_stipendi = calcola_medie(data_stipendi, ["Stipendio", "Risparmi", "Messi da parte Totali"])
    stats_stip = calcola_statistiche(data_stipendi, ["Stipendio", "Risparmi", "Messi da parte Totali"])
    
    col_somme1, col_somme2, col_somme3 = st.columns([1.3, 1, 1])
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
            chart_data["Mese_str"] = chart_data["Mese"].dt.strftime("%b %Y")
            ordine_mesi = chart_data.sort_values("Mese")["Mese_str"].unique().tolist()

            # Bar: Messi da parte (badi - background)
            bars_messi = alt.Chart(chart_data).mark_bar(
                color="#1D9E75", opacity=0.6, size=30
            ).encode(
                x=alt.X("Mese_str:N", sort=ordine_mesi, title="Mese",
                        axis=alt.Axis(labelAngle=-45)),
                y=alt.Y("Messi da parte Totali:Q", title="Valore (€)"),
                tooltip=["Mese_str:N", "Messi da parte Totali:Q"]
            )

            # Bar: Risparmi (sovrapposta - overlay)
            bars_risparmi = alt.Chart(chart_data).mark_bar(
                color="#EF9F27", opacity=0.85, size=30
            ).encode(
                x=alt.X("Mese_str:N", sort=ordine_mesi),
                y=alt.Y("Risparmi:Q"),
                tooltip=["Mese_str:N", "Risparmi:Q"]
            )

            # Line: Stipendi
            line_stipendi = alt.Chart(chart_data).mark_line(
                color="#5792E8", strokeWidth=2, point=True
            ).encode(
                x=alt.X("Mese_str:N", sort=ordine_mesi),
                y=alt.Y("Stipendio:Q"),
                tooltip=["Mese_str:N", "Stipendio:Q"]
            )

            # Line: Media Stipendi
            line_media_stip = alt.Chart(chart_data).mark_line(
                color="#f87171", strokeWidth=2, strokeDash=[6,3], point=True, opacity=0.4
            ).encode(
                x=alt.X("Mese_str:N", sort=ordine_mesi),
                y=alt.Y("Media Stipendio:Q"),
                tooltip=["Mese_str:N", "Media Stipendio:Q"]
            )

            # Line: Media NO 13/PDR
            line_media_no13 = alt.Chart(chart_data).mark_line(
                color="#fb923c", strokeWidth=2, strokeDash=[3,3], point=True
            ).encode(
                x=alt.X("Mese_str:N", sort=ordine_mesi),
                y=alt.Y("Media Stipendio NO 13°/PDR:Q"),
                tooltip=["Mese_str:N", "Media Stipendio NO 13°/PDR:Q"]
            )

            # Line: Media Risparmi
            line_media_risp = alt.Chart(chart_data).mark_line(
                color="#FFA040", strokeWidth=2, strokeDash=[4,4], point=True
            ).encode(
                x=alt.X("Mese_str:N", sort=ordine_mesi),
                y=alt.Y("Media Risparmi:Q"),
                tooltip=["Mese_str:N", "Media Risparmi:Q"]
            )

            # Line: Media Messi da parte
            line_media_messi = alt.Chart(chart_data).mark_line(
                color="#90EE90", strokeWidth=2, strokeDash=[5,5], point=True
            ).encode(
                x=alt.X("Mese_str:N", sort=ordine_mesi),
                y=alt.Y("Media Messi da parte Totali:Q"),
                tooltip=["Mese_str:N", "Media Messi da parte Totali:Q"]
            )

            grafico_finale = alt.layer(
                bars_messi, bars_risparmi,
                line_stipendi, line_media_stip, line_media_no13,
                line_media_risp, line_media_messi
            ).properties(
                title="Storico Stipendi e Risparmi",
                height=400
            ).resolve_scale(y="shared")

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

col_sx_bol, col_cx_bol_vuoto, col_dx_bol_chart = st.columns([1, 1, 2])

with col_sx_bol:
    with st.container():
        st.subheader("Inserisci Bollette")
        mesi_anni_bol = pd.date_range(start="2024-03-01", end="2030-12-01", freq="MS").strftime("%B %Y")
        selected_mese_bol = st.selectbox("Seleziona il mese e l'anno", mesi_anni_bol, key="mese_bollette")
        mese_dt_bol = datetime.strptime(selected_mese_bol, "%B %Y")
        
        if data_bollette.empty:
            data_bollette = pd.DataFrame(columns=["Mese", "Elettricità", "Gas", "Acqua", "Internet", "Tari"])
        
        record_bol = data_bollette[data_bollette["Mese"] == mese_dt_bol] if not data_bollette.empty else pd.DataFrame()
        elettricita_val = float(record_bol["Elettricità"].iloc[0]) if not record_bol.empty else 0.0
        gas_val = float(record_bol["Gas"].iloc[0]) if not record_bol.empty else 0.0
        acqua_val = float(record_bol["Acqua"].iloc[0]) if not record_bol.empty else 0.0
        internet_val = float(record_bol["Internet"].iloc[0]) if not record_bol.empty else 0.0
        tari_val = float(record_bol["Tari"].iloc[0]) if not record_bol.empty else 0.0
        
        col_bol_input1, col_bol_input2 = st.columns(2)
        with col_bol_input1:
            elettricita = st.number_input("Elettricità (€)", min_value=0.0, step=10.0, value=elettricita_val, key="elettricita_input")
            gas = st.number_input("Gas (€)", min_value=0.0, step=10.0, value=gas_val, key="gas_input")
            aggiungi_bollette = st.button("Aggiungi/Modifica Bollette", key="aggiorna_bollette")
        with col_bol_input2:
            acqua = st.number_input("Acqua (€)", min_value=0.0, step=10.0, value=acqua_val, key="acqua_input")
            internet = st.number_input("Internet (€)", min_value=0.0, step=10.0, value=internet_val, key="internet_input")
            tari = st.number_input("Tari (€)", min_value=0.0, step=10.0, value=tari_val, key="tari_input")
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
col_bol_table, col_bol_chart = st.columns([1, 3])
with col_bol_table:
    df_bol = data_bollette.copy()
    if not df_bol.empty:
        df_bol["Mese"] = pd.to_datetime(df_bol["Mese"], errors="coerce")
        df_bol["Mese"] = df_bol["Mese"].dt.strftime("%B %Y")
    st.dataframe(df_bol, use_container_width=True)
    
    stats_bollette = calcola_statistiche(data_bollette, ["Elettricità", "Gas", "Acqua", "Internet", "Tari"])
    
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
