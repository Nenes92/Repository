
# python -m streamlit run C:\Users\longh\Desktop\temp.py

import altair as alt
import streamlit as st
import pandas as pd
import json
import os
from datetime import datetime
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

def load_data_gsheets(worksheet_name, headers):
    client = get_gsheet_client()
    if not client:
        return pd.DataFrame()
    try:
        worksheet = get_or_create_worksheet(client, SHEET_URL, worksheet_name, headers)
        if not worksheet:
            return pd.DataFrame()
        records = worksheet.get_all_records()
        if not records:
            return pd.DataFrame(columns=headers)
        df = pd.DataFrame(records)
        if "Mese" in df.columns:
            df["Mese"] = pd.to_datetime(df["Mese"], errors="coerce")
            df = df.dropna(subset=["Mese"])
            df = df.sort_values(by="Mese").reset_index(drop=True)
        return df
    except Exception as e:
        st.error(f"Errore caricamento dati: {e}")
        return pd.DataFrame()

def save_data_gsheets(worksheet_name, headers, data):
    client = get_gsheet_client()
    if not client:
        return False
    try:
        worksheet = get_or_create_worksheet(client, SHEET_URL, worksheet_name, headers)
        if not worksheet:
            return False
        worksheet.clear()
        worksheet.append_row(headers)
        for _, row in data.iterrows():
            row_data = []
            for h in headers:
                val = row.get(h, "")
                if pd.isna(val):
                    row_data.append("")
                elif h == "Mese" and hasattr(val, 'strftime'):
                    row_data.append(val.strftime("%Y-%m-%d"))
                else:
                    row_data.append(float(val) if isinstance(val, (int, float)) else str(val))
            worksheet.append_row(row_data)
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
input_stipendio_originale=1000
input_risparmi_mese_precedente=0
input_stipendio_scelto=1000

percentuale_limite_da_spendere=0.15
limite_da_spendere=80
max_spese_quotidiane=370
decisione_budget_bollette_mensili=165

emergenze_compleanni=0.15
viaggi=0.07
# /////  

SPESE = {
    "Fisse": {
        "Mutuo": 435,
        "Bollette": 165,
        "Condominio": 45,
        "Altro": 0,
        "Cucina": 315,
        "Pulizia Casa": 40,
        "MoneyFarm - PAC 5": 100,
        "Alleanza - PAC": 100,
        "Macchina": 180,
        "Trasporti": 165,
        "Sport": 90,
        "Psicologo": 100,
        "World Food Programme": 30,
        "Beneficienza": 10,
        "Netflix": 8.5,
        "Spotify": 3.5,
        "Disney+": 4,
        "Fastweb (Casa+Cel)": 35,
        "BNL C.C.": 7.4,
        "ING C.C.": 2
    },
    "Variabili": {
        "Emergenze/Compleanni": emergenze_compleanni,
        "Viaggi": viaggi,
        "Da spendere": percentuale_limite_da_spendere,
        "Spese quotidiane": 0
    },
    "Revolut": ["Trasporti", "Sport", "Bollette", "Pulizia Casa", "Psicologo", "Beneficienza", "Netflix", "Spotify", "Disney+", "Emergenze/Compleanni", "Viaggi", "Da spendere", "Spese quotidiane"],
    "ING": ["Condominio", "Altro", "Cucina", "MoneyFarm - PAC 5", "Alleanza - PAC", "World Food Programme", "Macchina", "Fastweb (Casa+Cel)", "ING C.C."],
    "BNL": ["Mutuo", "BNL C.C."],
}

ALTRE_ENTRATE = {
    "Macchina (Mamma)": 100,
    "Seconda Entrata": 0,
    "Altro": 0
}

@st.cache_data
def create_charts(stipendio_scelto, risparmiabili, df_altre_entrate):

    df_fisse = pd.DataFrame.from_dict(SPESE["Fisse"], orient="index", columns=["Importo"]).reset_index().rename(columns={"index": "Categoria"})
    df_fisse.loc[(df_fisse["Categoria"] == "World Food Programme") | (df_fisse["Categoria"] == "Beneficienza"), "Categoria"] = "Donazioni"
    df_fisse.loc[(df_fisse["Categoria"] == "MoneyFarm - PAC 5") | (df_fisse["Categoria"] == "Alleanza - PAC"), "Categoria"] = "Investimenti"
    df_fisse.loc[(df_fisse["Categoria"] == "Netflix") | (df_fisse["Categoria"] == "Disney+") | (df_fisse["Categoria"] == "Spotify") | (df_fisse["Categoria"] == "Fastweb (Casa+Cel)") | (df_fisse["Categoria"] == "BNL C.C.") | (df_fisse["Categoria"] == "ING C.C."), "Categoria"] = "Abbonamenti"
    df_fisse.loc[(df_fisse["Categoria"] == "Sport") | (df_fisse["Categoria"] == "Psicologo"), "Categoria"] = "Salute"
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
        "World Food Programme": "#B57EDC",
        "Beneficienza": "#B57EDC",
        "Netflix": "#D2691E",
        "Spotify": "#D2691E",
        "Disney+": "#D2691E",
        "Fastweb (Casa+Cel)": "#D2691E",
        "BNL C.C.": "#D2691E",
        "ING C.C.": "#D2691E",
        "Emergenze/Compleanni": "#4ADE80",
        "Viaggi": "#166534", 
        "Da spendere": "#FACC15", 
        "Spese quotidiane": "#FB923C",
        "Macchina (Mamma)": "#D2B48C",
        "Seconda Entrata": "#D8BFD8",
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
    chart_fisse = alt.Chart(df_fisse, title='Distribuzione Spese Fisse').mark_arc(
        outerRadius=100, innerRadius=40
    ).encode(
        theta=alt.Theta(field="Importo", type="quantitative"),
        color=alt.Color(field="Categoria", type="nominal", scale=alt.Scale(domain=list(color_map.keys()), range=list(color_map.values())), legend=alt.Legend(
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

    chart_fisse = chart_fisse.properties(title='Distribuzione Spese Fisse', width=280, height=280).interactive()
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
    chart_variabili = chart_variabili_arc.properties(title='Distribuzione Spese Variabili', width=280, height=280).interactive()
    df_altre_entrate['Percentuale'] = (df_altre_entrate['Importo'] / stipendio_scelto).map('{:.2%}'.format)

    # Altre Entrate donut — no legend, tooltip only
    df_altre_entrate_chart = df_altre_entrate[df_altre_entrate["Importo"] > 0].copy()
    if df_altre_entrate_chart.empty:
        df_altre_entrate_chart = df_altre_entrate.copy()

    ae_cats = df_altre_entrate_chart["Categoria"].tolist()
    ae_colors_map = {
        "Macchina (Mamma)": "#D2B48C",
        "Seconda Entrata": "#D8BFD8",
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
        title='Distribuzione Altre Entrate'
    ).interactive()
    
    return chart_fisse, chart_variabili, chart_altre_entrate, df_fisse, df_variabili, df_altre_entrate, color_map


def color_text(text, color):
    return f'<span style="color:{color}">{text}</span>'


def main():

    st.markdown('<div class="section-pill">💎 Dashboard Finanziaria</div>', unsafe_allow_html=True)
    st.title("Calcolatore di Spese Personali")

    col1, col2, col3 = st.columns([1, 1, 1.4])

    with col1:
        stipendio_originale = st.number_input("Inserisci il tuo stipendio mensile:", min_value=input_stipendio_originale, step=50)
        risparmi_mese_precedente = st.number_input("Inserisci quanto hai risparmiato nel mese precedente:", min_value=input_risparmi_mese_precedente, step=50)
    with col2:
        st.markdown('<div style="height: 40px;"></div>', unsafe_allow_html=True)
        stipendio_scelto = st.number_input("Inserisci il tuo stipendio mensile che scegli di usare:", min_value=input_stipendio_scelto, step=50)
        st.markdown('<div style
