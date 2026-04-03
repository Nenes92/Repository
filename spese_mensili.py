
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
input_stipendio_originale=2300
input_risparmi_mese_precedente=0
input_stipendio_scelto=2000

percentuale_limite_da_spendere=0.15
limite_da_spendere=80
max_spese_quotidiane=370
decisione_budget_bollette_mensili=180

emergenze_compleanni=0.15
viaggi=0.07
# /////  

SPESE = {
    "Fisse": {
        "Mutuo": 435,
        "Bollette": decisione_budget_bollette_mensili,
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
        "Altro/C": 150,
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
    "Seconda Entrata": 0,
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

    chart_fisse = chart_fisse.properties(title='🏠 Distribuzione Spese Fisse', width=280, height=280).interactive()
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
    chart_variabili = chart_variabili_arc.properties(title='💸 Distribuzione Spese Variabili', width=280, height=280).interactive()
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
        title='➕ Distribuzione Altre Entrate'
    ).interactive()
    
    return chart_fisse, chart_variabili, chart_altre_entrate, df_fisse, df_variabili, df_altre_entrate, color_map


def color_text(text, color):
    return f'<span style="color:{color}">{text}</span>'


def main():

    st.markdown('<div class="section-pill">💎 Dashboard Finanziaria</div>', unsafe_allow_html=True)
    st.title("Calcolatore di Spese Personali")

    col_stip_inserimento1, col_stip_inserimento2, col_stip_inserimento3, col_stip_inserimento4, col_stip_inserimento5 = st.columns([1, 1, 1, 1, 1])
    col1, col2, col3, col4, col5 = st.columns([1, 1, 1, 1, 1])

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
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">Stipendio Totale</div>
            <div class="kpi-value" style="color:#34d399;">{_ts}</div>
            <div style="font-size:11px;color:rgba(255,255,255,0.3);margin-top:3px;">Originale + Altre Entrate</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Stipendio da Utilizzare</div>
            <div class="kpi-value" style="color:#60a5fa;">{_tu}</div>
            <div style="font-size:11px;color:rgba(255,255,255,0.3);margin-top:3px;">Scelto + Altre Entrate</div>
        </div>
        """, unsafe_allow_html=True)

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
                <div class="kpi-label">Risparmiabili</div>
                <div class="kpi-value" style="color:#a3e635;">{_ri}</div>
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
                    range=['#FF6464', '#B8C070', '#888888']
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
                alt.Tooltip("Percentuale:Q", title="%", format=".1f")
            ]
        ).properties(
            title=alt.TitleParams("Stipendio Totale", color='rgba(255,255,255,0.7)', fontSize=12),
            width=160, height=160
        )

        chart_utilizzare_clean = alt.Chart(df_utilizzare_clean).mark_arc(innerRadius=40, outerRadius=70).encode(
            theta=alt.Theta(field="Value", type="quantitative"),
            color=alt.Color(
                field="Component", type="nominal",
                scale=alt.Scale(domain=['Spese Fisse', 'Risparmiabili'], range=['#FF6961', '#B8C070']),
                legend=alt.Legend(
                    title=None, orient='bottom', direction='vertical',
                    labelColor='rgba(255,255,255,0.65)', labelFontSize=10,
                    symbolSize=60, padding=4
                )
            ),
            tooltip=[
                alt.Tooltip("Component:N", title="Categoria"),
                alt.Tooltip("Value:Q", title="Valore (€)", format=".2f"),
                alt.Tooltip("Percentuale:Q", title="%", format=".1f")
            ]
        ).properties(
            title=alt.TitleParams("Stipendio da Utilizzare", color='rgba(255,255,255,0.7)', fontSize=12),
            width=160, height=160
        )

        chart_donut = (chart_totale_clean | chart_utilizzare_clean).resolve_scale(color='independent')

        st.markdown('<div style="height: 20px;"></div>', unsafe_allow_html=True)
        st.markdown("**💶 Distribuzione Stipendi:**")
        st.altair_chart(chart_donut, use_container_width=True)

    # --- COLONNA 2: SPESE VARIABILI ---
    with col2:
        st.markdown("---")
        st.markdown('<div class="section-pill">💸 Spese Variabili</div>', unsafe_allow_html=True)
        st.subheader("Spese Variabili Rimanenti:")

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
        _sv = f"€{spese_variabili_totali:.2f}"
        st.markdown(f"""
        <div class="kpi-card">
            <div class="kpi-label">Totale Spese Variabili</div>
            <div class="kpi-value" style="color:#fde047;">{_sv}</div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown('<div style="height: 20px;"></div>', unsafe_allow_html=True)

        pass  # Risparmiati section moved below col1,2,3

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
    with col3:
        st.markdown("---")
        st.markdown('<div class="section-pill">➕ Altre Entrate</div>', unsafe_allow_html=True)
        st.subheader("Altre Entrate:")
        for voce, importo in ALTRE_ENTRATE.items():
            if voce in ["Macchina (Mamma)"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#E6C48C"), unsafe_allow_html=True)
            elif voce in ["Altro"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#89CFF0"), unsafe_allow_html=True)
            elif voce in ["Seconda Entrata"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#D8BFD8"), unsafe_allow_html=True)
            else:
                st.write(f"- {voce}: €{importo:.2f}")

        _ae = f"€{sum(ALTRE_ENTRATE.values()):.2f}"
        st.markdown("---")
        st.markdown(f"""
        <div class="kpi-card" style="border-color:rgba(52,211,153,0.2);">
            <div class="kpi-label">Totale Altre Entrate</div>
            <div class="kpi-value" style="color:#34d399;">{_ae}</div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown('<div style="height: 20px;"></div>', unsafe_allow_html=True)


    with col4:
        st.markdown("---")
        st.markdown('<div class="section-pill">💰 Risparmi del Mese</div>', unsafe_allow_html=True)
        st.subheader("Risparmiati del mese:")
    
        kpi_val = f"€{risparmi_mensili_calc:.2f}"
        kpi_pct = f"{(risparmi_mensili_calc)/(stipendio_originale+sum(ALTRE_ENTRATE.values()))*100:.1f}"
    
        # valori già calcolati
        v1 = risparmio_stipendi_calc
        v2 = risparmi_mese_precedente
        v3 = risparmio_da_spendere_calc
        v4 = risparmio_spese_quotidiane_calc
        
        def riga(voce, valore, colore, extra=""):
            return f"""
            <div style="
                display:flex;
                justify-content:space-between;
                align-items:center;
                width:420px;
                color:{colore};
                margin-bottom:4px;
            ">
                <span style="display:flex; align-items:center;">
                    - {voce}
                    <span style="
                        display:inline-block;
                        width:0;
                        height:0;
                        border-top:5px solid transparent;
                        border-bottom:5px solid transparent;
                        border-right:5px solid #89CFF0;
                        margin-left:8px;
                    "></span>
                </span>
                <span>€{valore:.2f}</span>
            </div>
            {extra}
            """

        # Stipendi + Mese precedente + Da spendere + Quotidiane
        html_risparmi = ""
        html_risparmi += riga("Dallo Stipendio Originale", v1, "#9ca3af")
        html_risparmi += riga("Dal Mese Precedente", v2, "#60a5fa")
        html_risparmi += riga("Dai 'Da Spendere'", v3, "#fde047")
        html_risparmi += riga("Dalle 'Spese Quotidiane'", v4, "#FB923C")
        
        st.markdown(html_risparmi, unsafe_allow_html=True)
        st.markdown("---")
        st.markdown(f"""
        <div class="kpi-card" style="border-color:rgba(52,211,153,0.25);">
            <div class="kpi-label">Tot. Risparmiato</div>
            <div class="kpi-value" style="color:#34d399;">{kpi_val}</div>
            <div style="font-size:10px;color:rgba(255,255,255,0.3);margin-top:3px;">{kpi_pct}% dello Stipendio Totale</div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown('<div style="height: 20px;"></div>', unsafe_allow_html=True)
        st.markdown("**💰 Distribuzione Risparmi:**")
        st.markdown('<div style="height: 10px;"></div>', unsafe_allow_html=True)
        savings_vals = [risparmio_stipendi_calc, risparmi_mese_precedente, risparmio_da_spendere_calc, risparmio_spese_quotidiane_calc]
        non_saved_calc = max(0, (stipendio_originale + sum(ALTRE_ENTRATE.values())) - sum(savings_vals))
        df_savings_raw = pd.DataFrame({
            'Component': ['Da Stipendi', 'Da Mese Prec.', 'Da Spendere', 'Quotidiane', 'Spesi'],
            'Value': [risparmio_stipendi_calc, risparmi_mese_precedente, risparmio_da_spendere_calc, risparmio_spese_quotidiane_calc, non_saved_calc]
        })
        df_savings = df_savings_raw[df_savings_raw["Value"] > 0].copy()
        if not df_savings.empty:
            chart_savings_arc = alt.Chart(df_savings).mark_arc(innerRadius=40, outerRadius=70).encode(
                theta=alt.Theta(field="Value", type="quantitative"),
                color=alt.Color(
                    field="Component", type="nominal",
                    scale=alt.Scale(
                        domain=['Da Stipendi', 'Da Mese Prec.', 'Da Spendere', 'Quotidiane', 'Spesi'],
                        range=['#9ca3af', '#60a5fa', '#fde047', '#fbbf24', '#374151']
                    ),
                    legend=alt.Legend(
                        title=None, orient='right', direction='vertical',
                        labelColor='rgba(255,255,255,0.65)', labelFontSize=10,
                        symbolSize=60, padding=4
                    )
                ),
                tooltip=[
                    alt.Tooltip('Component:N', title='Tipo'),
                    alt.Tooltip('Value:Q', title='€', format='.2f')
                ]
            ).properties(
                legend=alt.Legend(title=None)
                width=200, height=270
            #).configure_view(strokeWidth=0, fill='transparent'
            #).configure_title(color='rgba(255,255,255,0.7)'
            )
            chart_donut_Distribuzione_Risparmi = (chart_savings_arc).resolve_scale(color='independent')
            st.altair_chart(chart_donut_Distribuzione_Risparmi, use_container_width=True)


    with col5:
        st.markdown("---")
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
                somma_spese_programmate_immediate = SPESE["Fisse"]["Psicologo"] + SPESE["Fisse"]["Sport"] + SPESE["Fisse"]["Altro/C"] + SPESE["Fisse"]["Trasporti"] + SPESE["Fisse"]["Bollette"] + SPESE["Fisse"]["Beneficienza"] + SPESE["Fisse"]["Pulizia Casa"] + SPESE["Fisse"]["Disney+"] + SPESE["Fisse"]["Netflix"] + SPESE["Fisse"]["Spotify"]
                spese_che_anticipo_per_un_giorno_di_disney_spotify=18
                somma_valori = risparmi_mese_precedente - somma_spese_programmate_immediate - spese_che_anticipo_per_un_giorno_di_disney_spotify + totale_carta
                st.markdown(f'Totale da &nbsp; **<em style="color: #A0A0A0;">{testo}</em> &nbsp; su <span style="color:{colore}; text-decoration: underline;">{carta}</span>:** <span style="color:{colore}">€{totale_carta:.2f}</span> <span style="font-size: 14px; color: gray;"> <br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;( + <span style="color:{colore}; font-size: 14px;">{risparmi_mese_precedente:.2f}</span> dai Risparmi - (<span style="color:{colore}; font-size: 14px;">€{somma_spese_programmate_immediate:.2f} - {spese_che_anticipo_per_un_giorno_di_disney_spotify:.2f}</span>) -> Vedrai: <span style="color:{colore}; font-size: 14px;">€{somma_valori:.2f}</span> )</span>', unsafe_allow_html=True)
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
        st.markdown("**💳 Distribuzione Carte:**")
        st.markdown('<div style="height: 60px;"></div>', unsafe_allow_html=True)

        # Calculate totals per card
        ing_total = sum(SPESE["Fisse"].get(v, 0) + SPESE["Variabili"].get(v, 0) for v in SPESE["ING"])
        revolut_total = revolut_expenses + risparmi_mese_precedente  # original before subtraction
        bnl_total = sum(SPESE["Fisse"].get(v, 0) + SPESE["Variabili"].get(v, 0) for v in SPESE["BNL"])

        df_carte = pd.DataFrame({
            'Carta': ['ING', 'Revolut', 'BNL', 'Risparmiato BNL'],
            'Totale': [ing_total, revolut_total, bnl_total, risparmi_mensili]
                })
        df_carte['Percentuale'] = (df_carte['Totale'] / df_carte['Totale'].sum() * 100).round(1)

        carte_arc = alt.Chart(df_carte).mark_arc(innerRadius=35, outerRadius=60).encode(
        theta=alt.Theta(field="Totale", type="quantitative"),
        color=alt.Color(
            field="Carta", type="nominal",
            scale=alt.Scale(
                domain=['ING', 'Revolut', 'BNL', 'Risparmiato BNL'],
                range=['#D2691E', '#89CFF0', '#2E7D32', '#66BB6A']
            ),
            legend=alt.Legend(title=None)
            
        ),
        tooltip=[
            alt.Tooltip("Carta:N", title="Carta"),
            alt.Tooltip("Totale:Q", title="Totale (€)", format=".2f"),
            alt.Tooltip("Percentuale:Q", title="%", format=".1f")
        ]
        ).properties( width=180, height=200)


        chart_carte = carte_arc
        st.altair_chart(chart_carte, use_container_width=True)


                
            

    # Visualizzazione grafici
    with st.container():
        st.markdown("---")
        with st.container():
            col1, col2 = st.columns(2)
            with col1:
                col1_1, col1_2 = st.columns([1, 1])
                with col1_1:
                    st.altair_chart(chart_fisse, use_container_width=True)
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
                    st.markdown('<small style="color:#808080;">Percentuali sullo Stipendio da Utilizzare</small>', unsafe_allow_html=True)

            with col2:
                col2_1, col2_2 = st.columns([1, 1])
                with col2_1:
                    if not df_variabili.empty and df_variabili['Importo'].sum() > 0:
                        st.altair_chart(chart_variabili, use_container_width=True)
                    else:
                        st.info("Inserisci uno stipendio maggiore delle spese fisse")
                with col2_2:
                    st.markdown("<br>", unsafe_allow_html=True)
                    st.subheader("Dettaglio Spese Variabili:")
                    formatted_df_variabili = df_variabili.rename(columns={'Importo': 'Valore €'})
                    formatted_df_variabili["Valore €"] = formatted_df_variabili["Valore €"].apply(lambda x: f"€ {x:.2f}")
                    styled_df_variabili = (
                        formatted_df_variabili[["Categoria", "Valore €", "Percentuale"]].style
                        .apply(lambda x: [f"background-color: {color_map.get(x.name, '')}" for i in x], axis=1)
                        .map(lambda x: f"color: {color_map.get(x, '')}" if x in formatted_df_variabili["Categoria"].unique() else "", subset=["Categoria"])
                        .set_properties(**{'text-align': 'center'})
                    )
                    st.dataframe(styled_df_variabili, use_container_width=True)
                    st.markdown('<small style="color:#808080;">Percentuali sui Risparmiabili</small>', unsafe_allow_html=True)

        with st.container():
            col1, col2 = st.columns([1.5, 2])
            with col1:
                col1_1, col1_2 = st.columns([1, 1])
                with col1_1:
                    st.altair_chart(chart_altre_entrate, use_container_width=True)
                with col1_2:
                    st.markdown("<br><br><br>", unsafe_allow_html=True)
                    st.subheader("Dettaglio Altre Entrate:")
                    df_altre_entrate = df_altre_entrate.rename(columns={'Importo': 'Valore €'})
                    df_altre_entrate["Valore €"] = df_altre_entrate["Valore €"].apply(lambda x: f"€ {x:.2f}")
                    styled_df_altre_entrate = (
                        df_altre_entrate[["Categoria", "Valore €", "Percentuale"]].style
                        .apply(lambda x: [f"background-color: {color_map.get(x.name, '')}" for i in x], axis=1)
                        .applymap(lambda x: f"color: {color_map.get(x, '')}" if x in df_altre_entrate["Categoria"].unique() else "", subset=["Categoria"])
                        .set_properties(**{'text-align': 'center'})
                    )
                    st.dataframe(styled_df_altre_entrate)
                    st.markdown('<small style="color:#808080;">Percentuali sullo Stipendio da Utilizzare</small>', unsafe_allow_html=True)
            with col2:
                st.altair_chart(chart_barre, use_container_width=True)

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


def download_data_button(data, file_name):
    json_data = json.dumps(data.to_dict(orient="records"), indent=4, default=str)
    st.download_button(
        label=f"⬇️   Download {file_name}",
        data=json_data,
        file_name=file_name,
        mime="application/json"
    )


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

col_sx_stip, col_cx_stip_download, col_dx_stip_chart = st.columns([1, 1, 2])
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

with col_cx_stip_download:
    download_data_button(data_stipendi, "storico_stipendi.json")
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
                color="#f87171", strokeWidth=2, strokeDash=[6,3], point=True
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
st.markdown('<h1 style="font-size:2rem;font-weight:600;background:linear-gradient(90deg,#60a5fa,#a78bfa,#34d399);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;">Storico Bollette</h1>', unsafe_allow_html=True)

BOLLETTE_HEADERS = ["Mese", "Elettricità", "Gas", "Acqua", "Internet", "Tari"]
data_bollette = load_data_gsheets("Bollette", BOLLETTE_HEADERS)

col_sx_bol, col_cx_bol_download, col_dx_bol_chart = st.columns([1, 1, 2])

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

with col_cx_bol_download:
    download_data_button(data_bollette, "storico_bollette.json")
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
