# python -m streamlit run C:\Users\longh\Desktop\temp.py

import altair as alt
import streamlit as st
import pandas as pd
import json
import os
from datetime import datetime
import time
import io

from googleapiclient.http import MediaIoBaseDownload
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow


st.set_page_config(layout="wide")  # Imposta layout wide per la pagina IMMEDIATAMENTE


# Flag per controllare se la configurazione della pagina è già stata impostata
page_config_set = False

def set_page_config():
    pass # Rimuoviamo il contenuto di questa funzione, non è più necessario

# /////  
# Variabili inizializzate
input_stipendio_originale=2485
input_risparmi_mese_precedente=0
input_stipendio_scelto=2150

percentuale_limite_da_spendere=0.15
limite_da_spendere=80
max_spese_quotidiane=400
decisione_budget_bollette_mensili=100

emergenze_compleanni=0.1
viaggi=0.06
# /////  

# stipendio_originale = input - quanto prendi
# stipendio_scelto = input - quanto decidi che è il tuo stipendio
# altre_entrate = altre entrate
# stipendio = stipendio_scelto + altre_entrate
# stipendio_totale = stipendio_originale + altre_entrate
# risparmi_mese_precedente = input - quanto ti rimane su Revolut/Contanti dal mese precedente
# risparmiabili = stipendio - spese_fisse_totali
# spese_fisse_totali = somma delle spese fisse
# risparmio_stipendi = stipendio_originale - stipendio_scelto
# risparmio_da_spendere = da_spendere_senza_limite - da_spendere if da_spendere_senza_limite > limite_da_spendere else 0
# risparmio_spese_quotidiane = spese_quotidiane_senza_limite - spese_quotidiane if spese_quotidiane_senza_limite > max_spese_quotidiane else 0


# --- CONFIGURAZIONE ---

# Dizionario delle spese (ristrutturato)
SPESE = {
    "Fisse": {
        "Mutuo": 500,
        "Bollette": 250,
        "Condominio": 100,
        "Garages": 30,
        "MoneyFarm - PAC 5": 100,
        "Cometa": 30,
        "Alleanza - PAC": 100,
        "Macchina": 180,
        "Trasporti": 130,
        "Sport": 90,
        "Psicologo": 100,
        "World Food Programme": 30,
        "Beneficienza": 15,
        "Netflix": 8.5,
        "Spotify": 3,
        "Disney+": 3.5,
        "Wind": 10,
        "BNL C.C.": 4.5,
        "ING C.C.": 2
    },
    "Variabili": {
        "Emergenze/Compleanni": emergenze_compleanni,
        "Viaggi": viaggi,
        "Da spendere": percentuale_limite_da_spendere,
        "Spese quotidiane": 0  # Inizializzato a zero
    },
    "Revolut": ["Trasporti", "Sport", "Bollette", "Psicologo", "Beneficienza", "Netflix", "Spotify", "Disney+", "Emergenze/Compleanni", "Viaggi", "Da spendere", "Spese quotidiane"],
    "ING": ["Condominio", "Garages", "MoneyFarm - PAC 5", "Cometa", "Alleanza - PAC", "World Food Programme", "Macchina", "Wind", "ING C.C."],
    "BNL": ["Mutuo", "BNL C.C."],
}

# Dizionario delle altre entrate
ALTRE_ENTRATE = {
    "Macchina (Mamma)": 100,
    "Affitto Garage": 000,
    "Altro": 0
}

@st.cache_data  # Aggiungiamo il decoratore per il caching
def create_charts(stipendio_scelto, risparmiabili, df_altre_entrate):

    # --- 1. Creazione DataFrame ---

    # DataFrame per Spese Fisse (con accorpamento)
    df_fisse = pd.DataFrame.from_dict(SPESE["Fisse"], orient="index", columns=["Importo"]).reset_index().rename(columns={"index": "Categoria"})
    df_fisse.loc[(df_fisse["Categoria"] == "World Food Programme") | (df_fisse["Categoria"] == "Beneficienza"), "Categoria"] = "Donazioni"
    df_fisse.loc[(df_fisse["Categoria"] == "MoneyFarm - PAC 5") | (df_fisse["Categoria"] == "Alleanza - PAC")| (df_fisse["Categoria"] == "Cometa"), "Categoria"] = "Investimenti"
    df_fisse.loc[(df_fisse["Categoria"] == "Netflix") | (df_fisse["Categoria"] == "Disney+") | (df_fisse["Categoria"] == "Spotify") | (df_fisse["Categoria"] == "Wind") | (df_fisse["Categoria"] == "BNL C.C.") | (df_fisse["Categoria"] == "ING C.C."), "Categoria"] = "Abbonamenti"
    df_fisse.loc[(df_fisse["Categoria"] == "Sport") | (df_fisse["Categoria"] == "Psicologo"), "Categoria"] = "Salute"
    df_fisse.loc[(df_fisse["Categoria"] == "Trasporti") | (df_fisse["Categoria"] == "Macchina"), "Categoria"] = "Macchina"
    df_fisse.loc[(df_fisse["Categoria"] == "Bollette") | (df_fisse["Categoria"] == "Mutuo") | (df_fisse["Categoria"] == "Condominio") | (df_fisse["Categoria"] == "Garages"), "Categoria"] = "Casa"
    df_fisse = df_fisse.groupby("Categoria").sum().reset_index()  # Aggrega per categoria

    # DataFrame per Spese Variabili
    df_variabili = pd.DataFrame.from_dict(SPESE["Variabili"], orient="index", columns=["Importo"]).reset_index().rename(columns={"index": "Categoria"})

    # DataFrame per Altre Entrate
    df_altre_entrate = pd.DataFrame.from_dict(ALTRE_ENTRATE, orient="index", columns=["Importo"]).reset_index().rename(columns={"index": "Categoria"})

    # Calcolo percentuali direttamente nel DataFrame e formattazione (spostato qui)
    df_variabili['Percentuale'] = (df_variabili['Importo'] / risparmiabili).map('{:.2%}'.format)


    # --- 2. Creazione DataFrame dei Totali --- (Modificata)
    totali = [df_fisse["Importo"].sum(), df_variabili["Importo"].sum(), df_altre_entrate["Importo"].sum(), stipendio_scelto] # Rimuovi risparmi_mensili
    categorie = ["Spese Fisse", "Spese Variabili", "Altre Entrate", "Stipendio Scelto"] # Rimuovi "Risparmi"
    df_totali = pd.DataFrame({"Totale": totali, "Categoria": categorie})

    # --- 3. Creazione Grafici con colori personalizzati ---
    # Mappa dei colori per le categorie
    color_map = {
        "Mutuo": "#CD5C5C",
        "Bollette": "#CD5C5C",
        "Condominio": "#CD5C5C",
        "Garages": "#CD5C5C",
        "MoneyFarm - PAC 5": "#6495ED",
        "Cometa": "#6495ED",
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
        "Wind": "#D2691E",
        "BNL C.C.": "#D2691E",
        "ING C.C.": "#D2691E",
        "Emergenze/Compleanni": "#50C878",
        "Viaggi": "#50C878",
        "Da spendere": "#FFFF99",
        "Spese quotidiane": "#FFFF99",
        "Altro": "#6495ED",
        "Macchina (Mamma)": "#D2B48C",
        "Affitto Garage": "#D8BFD8",
        "Stipendio Originale": "#5792E8",
        "Stipendio Utilizzato": "#6CBCD0",
        "Altre Entrate": "#77DD77",
        "Spese Fisse": "#FF6961",
        "Spese Variabili": "#FFFF99",
        "Risparmi": "#A2E88A",
    }

    # Grafico a torta per Spese Fisse (con nuovi colori)
    color_map["Donazioni"] = "#B57EDC"
    color_map["Investimenti"] = "#6495ED"
    color_map["Abbonamenti"] = "#D2691E"
    color_map["Salute"] = "#40E0D0"
    color_map["Macchina"] = "#D2B48C"
    color_map["Casa"] = "#CD5C5C"

    # Calcolo percentuali direttamente nel DataFrame e formattazione
    df_fisse['Percentuale'] = (df_fisse['Importo'] / stipendio_scelto).map('{:.2%}'.format)

    # Grafico a torta per Spese Fisse (senza etichette di testo)
    chart_fisse = alt.Chart(df_fisse, title='Distribuzione Spese Fisse').mark_arc().encode(
        theta=alt.Theta(field="Importo", type="quantitative"),
        color=alt.Color(field="Categoria", type="nominal", scale=alt.Scale(domain=list(color_map.keys()), range=list(color_map.values())), legend=None),
        tooltip=["Categoria", "Importo", alt.Tooltip(field="Percentuale", title="Percentuale")]
    ).interactive()

    # Grafico a torta per Spese Variabili
    chart_variabili = alt.Chart(df_variabili, title='Distribuzione Spese Variabili').mark_arc().encode(
        theta=alt.Theta(field="Importo", type="quantitative"),
        color=alt.Color(field="Categoria", type="nominal", scale=alt.Scale(domain=list(color_map.keys()), range=list(color_map.values())), legend=None),
        tooltip=["Categoria", "Importo", alt.Tooltip(field="Percentuale", title="Percentuale")]
    ).interactive()

    # Calcolo percentuali direttamente nel DataFrame e formattazione
    df_altre_entrate['Percentuale'] = (df_altre_entrate['Importo'] / stipendio_scelto).map('{:.2%}'.format)

    # Grafico a torta per Altre Entrate (con raggio ridotto)
    chart_altre_entrate = alt.Chart(df_altre_entrate, title='Distribuzione Altre Entrate').mark_arc(outerRadius=80).encode(
        theta=alt.Theta(field="Importo", type="quantitative"),
        color=alt.Color(field="Categoria", type="nominal", scale=alt.Scale(domain=list(color_map.keys()), range=list(color_map.values())), legend=None),
        tooltip=["Categoria", "Importo"]
    ).interactive()

    return chart_fisse, chart_variabili, chart_altre_entrate, df_fisse, df_variabili, df_altre_entrate, color_map



# --- FUNZIONI ---
def color_text(text, color):
    return f'<span style="color:{color}">{text}</span>'







def main():

    st.title("Calcolatore di Spese Personali")

    # Input stipendio
    col1, col2, col3 = st.columns([1.2, 1.2, 1])  # Crea tre colonne con larghezze decise

    with col1:
        stipendio_originale = st.number_input("Inserisci il tuo stipendio mensile:", min_value=input_stipendio_originale, step=50)
        risparmi_mese_precedente = st.number_input("Inserisci quanto hai risparmiato nel mese precedente:", min_value=input_risparmi_mese_precedente, step=50)
    with col2:
        # Spazio vuoto personalizzabile
        st.markdown(
            '<div style="height: 40px;"></div>',  # Imposta l'altezza desiderata in pixel
            unsafe_allow_html=True,
        )
        stipendio_scelto = st.number_input("Inserisci il tuo stipendio mensile che scegli di usare:", min_value=input_stipendio_scelto, step=50)
        # Spazio vuoto personalizzabile
        st.markdown(
            '<div style="height: 45px;"></div>',  # Imposta l'altezza desiderata in pixel
            unsafe_allow_html=True,
        )
    with col3:
        # Spazio vuoto personalizzabile
        st.markdown(
            '<div style="height: 60px;"></div>',  # Imposta l'altezza desiderata in pixel
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div style="text-align: right;"><b>Stipendio Totale: </b><span style="color:#808080;">Stipendio Originale + Altre Entrate: <span style="color:#77DD77;">€{stipendio_originale + sum(ALTRE_ENTRATE.values()):.2f}</span></span></div>',
            unsafe_allow_html=True
        )
        st.markdown(
            f'<div style="text-align: right;"><b>Stipendio da Utilizzare: </b><span style="color:#808080;">Stipendio Scelto + Altre Entrate: <span style="color:#77DD77;">€{stipendio_scelto + sum(ALTRE_ENTRATE.values()):.2f}</span></span></div>',
            unsafe_allow_html=True
        )


        # Spazio vuoto personalizzabile
        st.markdown(
            '<div style="height: 60px;"></div>',  # Imposta l'altezza desiderata in pixel
            unsafe_allow_html=True,
        )





    # Calcolo entrate e spese (Ottimizzato)
    stipendio = stipendio_scelto + sum(ALTRE_ENTRATE.values())
    spese_fisse_totali = sum(SPESE["Fisse"].values())
    risparmiabili = stipendio - spese_fisse_totali

    # Calcolo spese variabili (Ottimizzato con list comprehension)
    percentuali_variabili = {"Emergenze/Compleanni": emergenze_compleanni, "Viaggi": viaggi}
    for voce, percentuale in percentuali_variabili.items():
        SPESE["Variabili"][voce] = percentuale * risparmiabili

    da_spendere_senza_limite = percentuale_limite_da_spendere * (risparmiabili - sum(percentuali_variabili.values()) * risparmiabili)
    SPESE["Variabili"]["Da spendere"] = min(da_spendere_senza_limite, limite_da_spendere)

    spese_quotidiane_senza_limite = risparmiabili - sum(percentuali_variabili.values()) * risparmiabili - da_spendere_senza_limite
    SPESE["Variabili"]["Spese quotidiane"] = min(spese_quotidiane_senza_limite, max_spese_quotidiane)
    
    # Calcolo risparmi mensili considerando il limite delle spese quotidiane
    risparmi_mensili = stipendio_originale - stipendio_scelto
    da_spendere = SPESE["Variabili"]["Da spendere"]
    spese_quotidiane = SPESE["Variabili"]["Spese quotidiane"]

    if spese_quotidiane_senza_limite > max_spese_quotidiane:
        eccesso_spese_quotidiane = spese_quotidiane_senza_limite - max_spese_quotidiane
        risparmi_mensili += eccesso_spese_quotidiane
    if da_spendere_senza_limite > limite_da_spendere:
        eccesso_da_spendere = da_spendere_senza_limite - limite_da_spendere
        risparmi_mensili += eccesso_da_spendere

    # Calcolo risparmi individuali
    risparmio_stipendi = stipendio_originale - stipendio_scelto
    risparmio_da_spendere = da_spendere_senza_limite - da_spendere if da_spendere_senza_limite > limite_da_spendere else 0
    risparmio_spese_quotidiane = spese_quotidiane_senza_limite - spese_quotidiane if spese_quotidiane_senza_limite > max_spese_quotidiane else 0



    # Sottrazione dei risparmi del mese precedente da Revolut e aggiunta ai risparmi
    revolut_expenses = sum(
        SPESE["Fisse"].get(voce, 0) + SPESE["Variabili"].get(voce, 0)
        for voce in SPESE["Revolut"]
    )
    revolut_expenses -= risparmi_mese_precedente
    risparmi_mensili += risparmi_mese_precedente




    # DataFrame per Altre Entrate
    df_altre_entrate = pd.DataFrame.from_dict(ALTRE_ENTRATE, orient="index", columns=["Importo"]).reset_index().rename(columns={"index": "Categoria"})

    # Creazione dinamica dei grafici (passa risparmiabili e df_altre_entrate)
    with st.spinner("Creazione dei grafici..."):
        chart_fisse, chart_variabili, chart_altre_entrate, df_fisse, df_variabili, df_altre_entrate, color_map = create_charts(stipendio, risparmiabili, df_altre_entrate)






        # --- DataFrame per il grafico a barre impilate ---
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
        # Ordine delle categorie
        ordine_categorie = ["Stipendio Totale", "Stipendio Utilizzato", "Spese Fisse", "Spese Variabili", "Risparmi"]

        # Calcola il massimo valore presente in 'Totale' e aggiungi un margine del 30%
        valore_massimo = df_totali_impilati['Totale'].max()
        margine = valore_massimo * 0.3
        limite_superiore = valore_massimo + margine

        # --- Creazione del grafico impilato ---
        # Esempio di creazione del grafico impilato con etichette centrate
        base = alt.Chart(df_totali_impilati, title='Confronto Totali per Categoria').transform_stack(
            stack='Totale',
            groupby=['Categoria'],      # impila i valori per ogni "Categoria"
            sort=[{'field': 'Tipo', 'order': 'descending'}],   # ordina le tipologie se necessario
            as_=['lower', 'upper']      # i campi generati dallo stack
        )

        bars = base.mark_bar().encode(
            x=alt.X('Categoria:N',
                    sort=ordine_categorie,
                    title="Categoria",
                    axis=alt.Axis(labelAngle=0)),
            # Usa i campi generati da transform_stack:
            y=alt.Y('lower:Q',
                    title="Totale",
                    scale=alt.Scale(domain=[0, limite_superiore])),
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
                            legend=alt.Legend(title="Componenti")),
            tooltip=['Categoria', 'Tipo', 'Totale']
        )

        # Creiamo un layer di etichette centrate all’interno di ogni segmento
        labels = base.transform_filter(
            'datum.Totale > 0'  # opzionale: mostra etichette solo se > 0
        ).transform_calculate(
            # calcolo del punto medio in verticale
            mid="(datum.lower + datum.upper) / 2"
        ).mark_text(
            align='center',
            baseline='middle',
            color='black',
            # dy=-1  # se vuoi aggiustare verticalmente le etichette
        ).encode(
            x=alt.X('Categoria:N', sort=ordine_categorie),
            y=alt.Y('mid:Q'),
            text=alt.Text('Totale:Q', format='.2f')
        )

        chart_barre = (bars + labels).properties(
            title='Confronto Totali per Categoria'
        )





    
    # Creazione di df_fisse_percentuali
    df_fisse_percentuali = df_fisse.rename(columns={'Importo': 'Valore €'})
    # Formattiamo la colonna 'Valore €' una sola volta, prima di rinominarla
    df_fisse['Valore €'] = df_fisse['Importo'].apply(lambda x: f"€ {x:.2f}")  





    # --- COLONNA 1: SPESE FISSE (con grafico e tabella) --- divisa in due colonne
    with col1:
        st.markdown("---")
        st.subheader("Spese Fisse:")

        # Creare due colonne
        col_left, col_right = st.columns(2)

        with col_left:
            for voce, importo in SPESE["Fisse"].items():
                if voce in ["Mutuo"]:
                    st.markdown(
                        f'<span style="color: #F08080;">- {voce}: €{importo:.2f}</span>'
                        f'<span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid green; margin-left: 10px;"></span>',
                        unsafe_allow_html=True
                    )
                elif voce in ["Bollette"]:
                    st.markdown(
                        f'<span style="color: #F08080;">- {voce}: €{importo:.2f}</span>'
                        f'<span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #89CFF0; margin-left: 10px;"></span>',
                        unsafe_allow_html=True
                    )
                elif voce in ["Condominio"]:
                    st.markdown(
                        f'<span style="color: #F08080;">- {voce}: €{importo:.2f}</span>'
                        f'<span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #D2691E; margin-left: 10px;"></span>',
                        unsafe_allow_html=True
                    )
                elif voce in ["Beneficienza"]:
                    st.markdown(
                        f'<span style="color: #D8BFD8;">- {voce}: €{importo:.2f}</span>'
                        f'<span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #89CFF0; margin-left: 10px;"></span>',
                        unsafe_allow_html=True
                    )
                elif voce in ["World Food Programme"]:
                    st.markdown(
                        f'<span style="color: #D8BFD8;">- {voce}: €{importo:.2f}</span>'
                        f'<span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #D2691E; margin-left: 10px;"></span>',
                        unsafe_allow_html=True
                    )
                elif voce in ["Sport", "Psicologo"]:
                    st.markdown(
                        f'<span style="color: #80E6E6;">- {voce}: €{importo:.2f}</span>'
                        f'<span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #89CFF0; margin-left: 10px;"></span>',
                        unsafe_allow_html=True
                    )
                elif voce in ["Garages"]:
                    st.markdown(
                        f'<span style="color: #F08080;">- {voce}: €{importo:.2f}</span>'
                        f'<span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #D2691E; margin-left: 10px;"></span>',
                        unsafe_allow_html=True
                    )
                    st.markdown('<hr style="width:50%; margin-left:0;">', unsafe_allow_html=True)
                elif voce in ["Trasporti"]:
                    st.markdown(
                        f'<span style="color: #E6C48C;">- {voce}: €{importo:.2f}</span>'
                        f'<span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #89CFF0; margin-left: 10px;"></span>',
                        unsafe_allow_html=True
                    )

        with col_right:
            for voce, importo in SPESE["Fisse"].items():
                if voce in ["Wind"]:
                    st.markdown(
                        f'<span style="color: #CC7722;">- {voce}: €{importo:.2f}</span>'
                        f'<span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #D2691E; margin-left: 10px;"></span>',
                        unsafe_allow_html=True
                    )
                elif voce in ["Disney+", "Netflix", "Spotify"]:
                    st.markdown(
                        f'<span style="color: #CC7722;">- {voce}: €{importo:.2f}</span>'
                        f'<span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #89CFF0; margin-left: 10px;"></span>',
                        unsafe_allow_html=True
                    )
                elif voce in ["BNL C.C."]:
                    st.markdown(
                        f'<span style="color: #CC7722;">- {voce}: €{importo:.2f}</span>'
                        f'<span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid green; margin-left: 10px;"></span>',
                        unsafe_allow_html=True
                    )
                elif voce in ["ING C.C."]:
                    st.markdown(
                        f'<span style="color: #CC7722;">- {voce}: €{importo:.2f}</span>'
                        f'<span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #D2691E; margin-left: 10px;"></span>',
                        unsafe_allow_html=True
                    )
                elif voce in ["MoneyFarm - PAC 5","Cometa", "Alleanza - PAC"]:
                    st.markdown(
                        f'<span style="color: #89CFF0;">- {voce}: €{importo:.2f}</span>'
                        f'<span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #D2691E; margin-left: 10px;"></span>',
                        unsafe_allow_html=True
                    )
                elif voce in ["Macchina"]:
                    st.markdown(
                        f'<span style="color: #E6C48C;">- {voce}: €{importo:.2f}</span>'
                        f'<span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #D2691E; margin-left: 10px;"></span>',
                        unsafe_allow_html=True
                    )
                    st.markdown('<hr style="width:50%; margin-left:0;">', unsafe_allow_html=True)


        st.markdown("---")
        st.markdown(f'**Totale Spese Fisse:** <span style="color:#F08080;">€{spese_fisse_totali:.2f}</span><span style="color:#77DD77; float:right;"> - Risparmiabili: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp; <span style="color:#808080;"> Stipendio da Utilizzare - Spese fisse = </span>€{risparmiabili:.2f}</span>', unsafe_allow_html=True)
        st.markdown(f' <small span style="color:#F08080;"> {(spese_fisse_totali) / stipendio * 100:.2f} % dello Stipendio da Utilizzare</span> <small span style="color:#FFFF99; float:right;"> {(risparmiabili) / stipendio * 100:.2f} % dello Stipendio da Utilizzare </span>', unsafe_allow_html=True)
        st.markdown(f' <small span style="color:#F08080;"> {(spese_fisse_totali) / (stipendio_originale + sum(ALTRE_ENTRATE.values())) * 100:.2f} % dello Stipendio Totale</span> <small span style="color:#FFFF99; float:right;"> {(risparmiabili) / (stipendio_originale + sum(ALTRE_ENTRATE.values())) * 100:.2f} % dello Stipendio Totale </span>', unsafe_allow_html=True)





# --- COLONNA 2: SPESE VARIABILI E RIMANENTE ---
    with col2:
        st.markdown("---")
        st.subheader("Spese Variabili Rimanenti:")

        # Calcola e visualizza spese variabili (semplificato)
        da_spendere = 0  # Inizializzazione di da_spendere
        spese_quotidiane = 0  # Inizializzazione di spese_quotidiane
        spese_variabili_totali = sum(SPESE["Variabili"].values())

        # Calcolo risparmi individuali
        risparmio_stipendi = stipendio_originale - stipendio_scelto
        risparmio_da_spendere = 0
        risparmio_spese_quotidiane = 0

        for voce, importo in SPESE["Variabili"].items():
            if voce in ["Emergenze/Compleanni"]:
                percentuale_emergenze = percentuali_variabili.get("Emergenze/Compleanni", 0) * 100
                st.markdown(color_text(f"- {voce}: €{importo:.2f}&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;", "#66D1A3") + f'<span style="margin-right: 20px; color:#808080;">- {percentuale_emergenze:.2f}% dei Risparmiabili</span>', unsafe_allow_html=True)
            elif voce in ["Viaggi"]:
                percentuale_viaggi = percentuali_variabili.get("Viaggi", 0) * 100
                st.markdown(color_text(f"- {voce}: €{importo:.2f}&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;", "#66D1A3") + f'<span style="margin-right: 20px; color:#808080;">- {percentuale_viaggi:.2f}% dei Risparmiabili</span>', unsafe_allow_html=True)
            elif voce in ["Spese quotidiane"]:
                percentuale_da_spendere = SPESE["Variabili"]["Da spendere"] / risparmiabili * 100
                st.markdown(color_text(f"- {voce}: €{importo:.2f}&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;", "#F0E68C") + f'<span style="margin-right: 20px; color:#808080;">- il rimanente &nbsp;&nbsp;(con un limite a {max_spese_quotidiane})</span>', unsafe_allow_html=True)
            elif voce in ["Da spendere"]:
                spese_emergenze_viaggi = SPESE["Variabili"]["Emergenze/Compleanni"] + SPESE["Variabili"]["Viaggi"]
                risparmiabili_dopo_emergenze_viaggi = risparmiabili - spese_emergenze_viaggi
                st.markdown(color_text(f"- {voce}: €{importo:.2f}&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;", "#F0E68C") + f'<span style="margin-right: 20px; color:#808080;">- {da_spendere_senza_limite*100/risparmiabili_dopo_emergenze_viaggi:.2f}% &nbsp;&nbsp; del rimanente €{risparmiabili_dopo_emergenze_viaggi:.2f} &nbsp;&nbsp; (con un limite a {limite_da_spendere}€)</span>', unsafe_allow_html=True)
            else:
                st.write(f"- {voce}: €{importo:.2f}")
            if voce == "Da spendere":  # Aggiunta per visualizzare da_spendere_senza_limite
                da_spendere = min(da_spendere_senza_limite, limite_da_spendere)  # Calcolo di da_spendere
                risparmio_da_spendere = da_spendere_senza_limite - da_spendere  # Calcolo dei risparmi (spostato qui)
                st.markdown(color_text(f'<small>- {voce} (reali): €{da_spendere_senza_limite:.2f} -> Risparmiati: €{risparmio_da_spendere:.2f}</small>', "#808080"), unsafe_allow_html=True)
            if voce == "Spese quotidiane":  # Aggiunta per visualizzare spese_quotidiane_senza_limite
                spese_quotidiane = min(spese_quotidiane_senza_limite, max_spese_quotidiane)  # Calcolo di spese_quotidiane
                risparmio_spese_quotidiane = spese_quotidiane_senza_limite - spese_quotidiane  # Calcolo dei risparmi (spostato qui)
                st.markdown(color_text(f'<small>- {voce} (reali): €{spese_quotidiane_senza_limite:.2f} -> Risparmiati: €{risparmio_spese_quotidiane:.2f}</small>', "#808080"), unsafe_allow_html=True)
        

        st.markdown("---")
        st.markdown(
            f'**Totale Spese Variabili utilizzate:** <span style="color:#FFFF99;">€{spese_variabili_totali:.2f}</span>'
            f'<span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid #89CFF0; margin-left: 10px;"></span>',
            unsafe_allow_html=True)
            
        # Spazio vuoto personalizzabile
        st.markdown(
            '<div style="height: 75px;"></div>',  # Imposta l'altezza desiderata in pixel
            unsafe_allow_html=True,
        )



        # --- CALCOLO E VISUALIZZAZIONE RISPARMIATI DEL MESE ---
        _, right_col = st.columns([1, 2]) # Crea due colonne, il titolo va nella seconda
        with right_col:
            st.markdown('<hr style="width: 100%; height:5px;border-width:0;color:gray;background-color:gray">', unsafe_allow_html=True)
            st.subheader("Risparmiati del mese:")

            # Calcolo risparmi mensili considerando il limite delle spese quotidiane
            risparmi_mensili = stipendio_originale - stipendio_scelto
            
            # Calcolo spese variabili (Ottimizzato con list comprehension)
            percentuali_variabili = {"Emergenze/Compleanni": emergenze_compleanni, "Viaggi": viaggi}
            for voce, percentuale in percentuali_variabili.items():
                SPESE["Variabili"][voce] = percentuale * risparmiabili

            da_spendere_senza_limite = percentuale_limite_da_spendere * (risparmiabili - sum(percentuali_variabili.values()) * risparmiabili)
            SPESE["Variabili"]["Da spendere"] = min(da_spendere_senza_limite, limite_da_spendere)

            spese_quotidiane_senza_limite = risparmiabili - sum(percentuali_variabili.values()) * risparmiabili - da_spendere_senza_limite
            SPESE["Variabili"]["Spese quotidiane"] = min(spese_quotidiane_senza_limite, max_spese_quotidiane)
            
            if spese_quotidiane_senza_limite > max_spese_quotidiane:
                eccesso_spese_quotidiane = spese_quotidiane_senza_limite - max_spese_quotidiane
                risparmi_mensili += eccesso_spese_quotidiane
            if da_spendere_senza_limite > limite_da_spendere:
                eccesso_da_spendere = da_spendere_senza_limite - limite_da_spendere
                risparmi_mensili += eccesso_da_spendere
            risparmi_mensili += risparmi_mese_precedente

            # Calcolo risparmi individuali
            risparmio_stipendi = stipendio_originale - stipendio_scelto
            risparmio_da_spendere = da_spendere_senza_limite - da_spendere if da_spendere_senza_limite > limite_da_spendere else 0
            risparmio_spese_quotidiane = spese_quotidiane_senza_limite - spese_quotidiane if spese_quotidiane_senza_limite > max_spese_quotidiane else 0

            # Visualizzazione con formattazione
            st.markdown(
                f'**Tot. Risparmiato:**</span> + <span style="color:#808080;">€{risparmio_stipendi:.2f}</span> + <span style="color:#808080;">€{risparmi_mese_precedente:.2f}</span> + <span style="color:#F0E68C;">€{risparmio_da_spendere:.2f}</span> + <span style="color:#F0E68C;">€{risparmio_spese_quotidiane:.2f}</span> = <span style="color:#77DD77;">€{risparmi_mensili:.2f}</span>'
                f'<span style="display: inline-block; width: 0; height: 0; border-top: 5px solid transparent; border-bottom: 5px solid transparent; border-right: 5px solid green; margin-left: 10px;"></span>',
                unsafe_allow_html=True
            )
            st.markdown(
                f'<div style="text-align:center;"><small style="color:#808080;">Risparmi da Stipendi</small> + <small style="color:#808080;">Risparmi da Mese Prec</small> + <small style="color:#FFFF99;">Risparmi Da Spendere</small> +  <small style="color:#FFFF99;">Risparmi Da Spese Quotidiane</small></div>'
                f'<div style="text-align:center;"><small style="color:#FFFF99;">{(risparmi_mensili) / (stipendio_originale + sum(ALTRE_ENTRATE.values())) * 100:.2f} % dello Stipendio Totale</small></div>',
                unsafe_allow_html=True,
            )





# --- COLONNA 3: ALTRE ENTRATE ---
    with col3:
        st.markdown("---")
        st.subheader("Altre Entrate:")
        for voce, importo in ALTRE_ENTRATE.items():
            if voce in ["Macchina (Mamma)"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#E6C48C"), unsafe_allow_html=True)
            elif voce in ["Altro"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#89CFF0"), unsafe_allow_html=True)
            elif voce in ["Affitto Garage"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#D8BFD8"), unsafe_allow_html=True)
            else:
                st.write(f"- {voce}: €{importo:.2f}")

        st.markdown("---")
        st.markdown(
            f'**Totale Altre Entrate:** <span style="color:#77DD77;">€{sum(ALTRE_ENTRATE.values()):.2f}</span>'
            f'<span style="display: inline-block; position: relative; width: 10px; height: 10px; margin-left: 10px;">'
            f'  <span style="display: inline-block; position: absolute; width: 10px; height: 2px; background-color: green; top: 50%; left: 0; transform: translateY(-50%);"></span>'
            f'  <span style="display: inline-block; position: absolute; width: 2px; height: 10px; background-color: green; top: 0; left: 50%; transform: translateX(-50%);"></span>'
            f'</span>',
            unsafe_allow_html=True
        )




        # Spazio vuoto personalizzabile
        st.markdown(
            '<div style="height: 195px;"></div>',  # Imposta l'altezza desiderata in pixel
            unsafe_allow_html=True,
        )


        # --- CALCOLO E VISUALIZZAZIONE TRASFERIMENTI E SPESE --- (Ottimizzato con dict comprehension)
        st.markdown('<hr style="width: 100%; height:5px;border-width:0;color:gray;background-color:gray">', unsafe_allow_html=True)
        st.subheader("Trasferimenti sulle Carte:")

        for carta in ["ING", "Revolut", "BNL"]:
            spese_carta = {voce: SPESE["Fisse"].get(voce, 0) + SPESE["Variabili"].get(voce, 0) 
                           for voce in SPESE[carta]}
            spese_carta = {voce: importo for voce, importo in spese_carta.items() if importo != 0}
            if carta == "Revolut":
                totale_carta = revolut_expenses  # Usa il valore modificato per Revolut
                colore = "#89CFF0"  # Azzurro
                testo = "trasferire"
                somma_valori = risparmi_mese_precedente + 25.50 + totale_carta
                st.markdown(f'Totale da &nbsp; **<em style="color: #A0A0A0;">{testo}</em> &nbsp; su <span style="color:{colore}; text-decoration: underline;">{carta}</span>:** <span style="color:{colore}">€{totale_carta:.2f}</span> <span style="font-size: 14px; color: gray;"> &nbsp;&nbsp;( + <span style="color:{colore}; font-size: 14px;">{risparmi_mese_precedente:.2f}</span> dai Risparmi + <span style="color:{colore}; font-size: 14px;">€25.50</span> da Netf/Spoti -> Vedrai: <span style="color:{colore}; font-size: 14px;">€{somma_valori:.2f}</span> )</span>', unsafe_allow_html=True)
            else:
                totale_carta = sum(spese_carta.values())
                if carta == "ING":
                    colore = "#D2691E"  # Arancione
                    testo = "trasferire"
                elif carta == "BNL":
                    colore = "green"  # Verde
                    colore2 = "#77DD77"  # Verde chiaro
                    testo = "mantenere"
                    testo2 = "risparmiato"
                st.markdown(f'Totale da &nbsp; **<em style="color: #A0A0A0;">{testo}</em> &nbsp; su <span style="color:{colore}; text-decoration: underline;">{carta}</span>:** <span style="color:{colore}">€{totale_carta:.2f}</span>', unsafe_allow_html=True)
        st.markdown(f'Totale &nbsp; **<em style="color: #A0A0A0;">{testo2}</em> &nbsp; su <span style="color:{colore}; text-decoration: underline;">{carta}</span>:** <span style="color:{colore2}">€{risparmi_mensili:.2f}</span>', unsafe_allow_html=True)



    # Visualizzazione dei grafici e della tabella delle percentuali
    with st.container():
        # --- GRAFICI ---
        st.markdown("---")

        # Grafici a torta per le spese (con container per ogni riga)
        with st.container():
            col1, col2 = st.columns(2)

            # --- Spese Fisse ---
            with col1:
                col1_1, col1_2 = st.columns([1, 1])  # Larghezze uguali per grafico e tabella
                with col1_1:  # Grafico a torta nella prima sub-colonna
                    st.altair_chart(chart_fisse, use_container_width=True)
                with col1_2:  # Table of fixed expenses in the second sub-column
                    st.subheader("Dettaglio Spese Fisse:")
                    df_fisse_percentuali = df_fisse_percentuali.rename(columns={'Importo': 'Valore €'})
                    df_fisse_percentuali["Valore €"] = df_fisse_percentuali["Valore €"].apply(lambda x: f"€ {x:.2f}")

                    # Applica lo stile direttamente al DataFrame (senza reset_index)
                    styled_df_fisse = (
                        df_fisse_percentuali[["Categoria", "Valore €", "Percentuale"]].style
                        .apply(lambda x: [f"background-color: {color_map.get(x.name, '')}" for i in x], axis=1)
                        .applymap(lambda x: f"color: {color_map.get(x, '')}" if x in df_fisse_percentuali["Categoria"].unique() else "", subset=["Categoria"])
                        .set_properties(**{'text-align': 'center'})
                    )
                    st.dataframe(styled_df_fisse)  # Visualizza il DataFrame stilizzato
                    st.markdown('<small style="color:#808080;">Percentuali sullo Sipendio da Utilizzare</small>', unsafe_allow_html=True)


           

            # --- Spese Variabili ---
            with col2:
                col2_1, col2_2 = st.columns([1, 1])  # Larghezze uguali per grafico e tabella
                with col2_1:  # Grafico a torta nella prima sub-colonna
                    st.altair_chart(chart_variabili, use_container_width=True)
                with col2_2:  # Table of variable expenses in the second sub-column
                    st.markdown("<br>", unsafe_allow_html=True)  # Add space above the table
                    st.subheader("Dettaglio Spese Variabili:")
                    formatted_df_variabili = df_variabili.rename(columns={'Importo': 'Valore €'})
                    formatted_df_variabili["Valore €"] = formatted_df_variabili["Valore €"].apply(lambda x: f"€ {x:.2f}")

                    # Applica lo stile direttamente al DataFrame (senza set_index)
                    styled_df_variabili = (
                        formatted_df_variabili[["Categoria", "Valore €", "Percentuale"]].style
                        .apply(lambda x: [f"background-color: {color_map.get(x.name, '')}" for i in x], axis=1)
                        .applymap(lambda x: f"color: {color_map.get(x, '')}" if x in formatted_df_variabili["Categoria"].unique() else "", subset=["Categoria"])
                        .set_properties(**{'text-align': 'center'})
                    )
                    st.dataframe(styled_df_variabili)  # Visualizza il DataFrame stilizzato
                    st.markdown('<small style="color:#808080;">Percentuali sui Risparmiabili</small>', unsafe_allow_html=True)

            
        with st.container():
            col1, col2 = st.columns([1.5, 2])
            
            # --- Altre Entrate ---
            with col1:
                col1_1, col1_2 = st.columns([1, 1])  # Larghezze uguali per grafico e tabella
                with col1_1:  # Grafico a torta nella prima sub-colonna
                    st.altair_chart(chart_altre_entrate, use_container_width=True)
                with col1_2:  # Table of other expenses in the second sub-column
                    st.markdown("<br><br><br>", unsafe_allow_html=True)  # Add space above the table
                    st.subheader("Dettaglio Altre Entrate:")
                    df_altre_entrate = df_altre_entrate.rename(columns={'Importo': 'Valore €'})
                    df_altre_entrate["Valore €"] = df_altre_entrate["Valore €"].apply(lambda x: f"€ {x:.2f}")
                   
                    # Applica lo stile per colorare le righe della tabella (modificato)
                    
                    styled_df_altre_entrate = (
                        df_altre_entrate[["Categoria", "Valore €", "Percentuale"]].style
                        .apply(lambda x: [f"background-color: {color_map.get(x.name, '')}" for i in x], axis=1)
                        .applymap(lambda x: f"color: {color_map.get(x, '')}" if x in df_altre_entrate["Categoria"].unique() else "", subset=["Categoria"])
                        .set_properties(**{'text-align': 'center'}) # Centra il testo nelle celle
                    )
                    st.dataframe(styled_df_altre_entrate)  # Visualizza il DataFrame stilizzato
                    st.markdown('<small style="color:#808080;">Percentuali sullo Sipendio da Utilizzare</small>', unsafe_allow_html=True)



            # --- Grafico Categorie ---
            with col2:
                st.altair_chart(chart_barre, use_container_width=True)

if __name__ == "__main__":
    main()

st.markdown('<hr style="width: 100%; height:5px;border-width:0;color:gray;background-color:gray">', unsafe_allow_html=True)




















import streamlit as st
import pandas as pd
import json
import os
import time
from datetime import datetime
import altair as alt

###############################
# Funzioni per gestione locale
###############################

def load_data_local(percorso_file):
    """
    Carica i dati da un file JSON locale e restituisce un DataFrame.
    Se il file non esiste, restituisce un DataFrame vuoto.
    """
    if os.path.exists(percorso_file):
        try:
            with open(percorso_file, 'r') as file:
                data = json.load(file)
            df = pd.DataFrame(data)
            if not df.empty and "Mese" in df.columns:
                df["Mese"] = pd.to_datetime(df["Mese"], errors="coerce")
                df = df.sort_values(by="Mese").reset_index(drop=True)
            return df
        except Exception as e:
            st.error(f"Errore nel caricamento del file {percorso_file}: {e}")
            return pd.DataFrame()
    else:
        # Se il file non esiste, inizializza un DataFrame vuoto
        return pd.DataFrame()

def save_data_local(percorso_file, data):
    """
    Salva il DataFrame in formato JSON sul percorso indicato.
    """
    try:
        data_dict = data.to_dict(orient="records")
        json_content = json.dumps(data_dict, indent=4, default=str)
        with open(percorso_file, "w") as file:
            file.write(json_content)
        st.success(f"Dati salvati correttamente in {percorso_file}.")
    except Exception as e:
        st.error(f"Errore nel salvataggio del file {percorso_file}: {e}")

###############################
# Funzioni per calcoli e grafici
###############################

@st.cache_data
def calcola_statistiche(data, colonne):
    """Calcola somma e media per le colonne indicate."""
    stats = {col: {'somma': data[col].sum(), 'media': round(data[col].mean(), 2)} for col in colonne}
    return stats

def calcola_medie(data, colonne):
    """Calcola le medie cumulative per le colonne e per lo stipendio escludendo il 13°/PDR."""
    for col in colonne:
        data[f"Media {col}"] = data[col].expanding().mean().round(2)
        if col == "Stipendio":
            data[f"Media {col} NO 13°/PDR"] = data[col].where(~data["Mese"].dt.month.isin([7, 12])).expanding().mean().round(2)
    return data

def crea_grafico_stipendi(data):
    """Crea il grafico per Stipendi e Risparmi (linee e punti)."""
    # Prepara i dati: unisce i valori originali e le medie
    data_completa = pd.concat([
        data.melt(id_vars=["Mese"], value_vars=["Stipendio", "Risparmi"], var_name="Categoria", value_name="Valore"),
        data.melt(id_vars=["Mese"], value_vars=["Media Stipendio", "Media Risparmi", "Media Stipendio NO 13°/PDR"],
                  var_name="Categoria", value_name="Valore")
    ])
    dominio_categorie = ["Stipendio", "Risparmi", "Media Stipendio", "Media Risparmi", "Media Stipendio NO 13°/PDR"]
    scala_colori = ["#77DD77", "#FFFF99", "#FF6961", "#84B6F4", "#FFA07A"]

    base = alt.Chart(data_completa).encode(
        x=alt.X("Mese:T", title="Mese", axis=alt.Axis(tickCount="month")),
        y=alt.Y("Valore:Q", title="Valore (€)")
    )

    linee = base.mark_line(strokeWidth=2, strokeDash=[5, 5]).encode(
        color=alt.Color("Categoria:N", scale=alt.Scale(domain=dominio_categorie, range=scala_colori),
                        legend=alt.Legend(title="Categorie")),
        tooltip=["Mese:T", "Categoria:N", "Valore:Q"]
    )
    punti = base.mark_point(shape="diamond", size=100, filled=True, opacity=0.7).encode(
        color=alt.Color("Categoria:N", scale=alt.Scale(domain=dominio_categorie, range=scala_colori)),
        tooltip=["Mese:T", "Categoria:N", "Valore:Q"]
    )
    return linee + punti

def crea_grafico_bollette(data_completa, order):
    """Crea il grafico per le bollette: barre impilate e linea per il saldo."""
    # Separa le bollette dal saldo
    df_bollette = data_completa[data_completa["Categoria"] != "Saldo"]
    df_saldo = data_completa[data_completa["Categoria"] == "Saldo"]

    barre = alt.Chart(df_bollette).mark_bar(opacity=0.8).encode(
        x=alt.X("Mese_str:N", sort=order, title="Mese", axis=alt.Axis(labelAngle=-45)),
        y=alt.Y("Valore:Q", title="Valore (€)"),
        color=alt.Color("Categoria:N", scale=alt.Scale(
            domain=["Elettricità", "Gas", "Acqua", "Internet", "Tari"],
            range=["#84B6F4", "#FF6961", "#96DED1", "#FFF5A1", "#C19A6B"]),
            legend=alt.Legend(title="Categorie")),
        tooltip=["Mese_str:N", "Categoria:N", "Valore:Q"]
    )

    linea_saldo = alt.Chart(df_saldo).mark_line(strokeDash=[5, 5], strokeWidth=2, color="#FF6961").encode(
        x=alt.X("Mese_str:N", sort=order),
        y=alt.Y("Valore:Q", title="Saldo (€)"),
        tooltip=["Mese_str:N", "Valore:Q"]
    )
    punti_saldo = alt.Chart(df_saldo).mark_point(shape="diamond", size=80, filled=True, color="#FF6961").encode(
        x=alt.X("Mese_str:N", sort=order),
        y="Valore:Q",
        tooltip=["Mese_str:N", "Valore:Q"]
    )
    return barre + linea_saldo + punti_saldo

###############################
# SEZIONE: Storico Stipendi e Risparmi
###############################

st.title("Storico Stipendi e Risparmi")

# File locale
stipendi_file = "storico_stipendi.json"
data_stipendi = load_data_local(stipendi_file)
if data_stipendi.empty:
    data_stipendi = pd.DataFrame(columns=["Mese", "Stipendio", "Risparmi"])

st.header("Inserisci Stipendio e Risparmi")
mesi_anni = pd.date_range(start="2024-03-01", end="2030-12-01", freq="MS").strftime("%B %Y")
selected_mese_anno = st.selectbox("Seleziona il mese e l'anno", mesi_anni, key="mese_anno_stipendi")
mese_datetime = datetime.strptime(selected_mese_anno, "%B %Y")

# Verifica se esiste già un record per il mese selezionato
existing_record = data_stipendi[data_stipendi["Mese"] == mese_datetime] if not data_stipendi.empty else pd.DataFrame()
stipendio_value = float(existing_record["Stipendio"].iloc[0]) if not existing_record.empty else 0.0
risparmi_value = float(existing_record["Risparmi"].iloc[0]) if not existing_record.empty else 0.0

col1, col2 = st.columns(2)
with col1:
    stipendio = st.number_input("Stipendio (€)", min_value=0.0, step=100.0, value=stipendio_value, key="stipendio_input")
with col2:
    risparmi = st.number_input("Risparmi (€)", min_value=0.0, step=100.0, value=risparmi_value, key="risparmi_input")

if st.button("Aggiungi/Modifica Stipendio e Risparmi", key="aggiorna_stipendi"):
    if stipendio > 0 or risparmi > 0:
        if not existing_record.empty:
            data_stipendi.loc[data_stipendi["Mese"] == mese_datetime, "Stipendio"] = stipendio
            data_stipendi.loc[data_stipendi["Mese"] == mese_datetime, "Risparmi"] = risparmi
            st.success(f"Record per {selected_mese_anno} aggiornato!")
        else:
            new_row = {"Mese": mese_datetime, "Stipendio": stipendio, "Risparmi": risparmi}
            data_stipendi = pd.concat([data_stipendi, pd.DataFrame([new_row])], ignore_index=True)
            st.success(f"Stipendio e Risparmi per {selected_mese_anno} aggiunti!")
        data_stipendi = data_stipendi.sort_values(by="Mese").reset_index(drop=True)
        save_data_local(stipendi_file, data_stipendi)
    else:
        st.error("Inserisci valori validi per stipendio e/o risparmi!")

if st.button(f"Elimina Record per {selected_mese_anno}", key="elimina_stipendi"):
    if not existing_record.empty:
        data_stipendi = data_stipendi[data_stipendi["Mese"] != mese_datetime]
        save_data_local(stipendi_file, data_stipendi)
        st.success(f"Record per {selected_mese_anno} eliminato!")
    else:
        st.error(f"Il mese {selected_mese_anno} non è presente nello storico!")

st.subheader("Dati Storici")
data_display = data_stipendi.copy()
if not data_display.empty:
    data_display["Mese"] = data_display["Mese"].dt.strftime("%B %Y")
st.dataframe(data_display, use_container_width=True)

# Calcolo medie e statistiche
data_stipendi = calcola_medie(data_stipendi, ["Stipendio", "Risparmi"])
statistiche_stipendi = calcola_statistiche(data_stipendi, ["Stipendio", "Risparmi"])
st.write(f"**Somma Stipendio:** {statistiche_stipendi['Stipendio']['somma']:,.2f} €")
st.write(f"**Media Stipendio:** {statistiche_stipendi['Stipendio']['media']:,.2f} €")
if "Media Stipendio NO 13°/PDR" in data_stipendi.columns and not data_stipendi.empty:
    st.write(f"**Media Stipendio NO 13°/PDR:** {data_stipendi['Media Stipendio NO 13°/PDR'].iloc[-1]:,.2f} €")
st.write(f"**Somma Risparmi:** {statistiche_stipendi['Risparmi']['somma']:,.2f} €")
st.write(f"**Media Risparmi:** {statistiche_stipendi['Risparmi']['media']:,.2f} €")

st.altair_chart(crea_grafico_stipendi(data_stipendi).properties(height=500, width='container'), use_container_width=True)

st.markdown("---")

###############################
# SEZIONE: Storico Bollette
###############################

st.title("Storico Bollette")

# File locale per bollette
bollette_file = "storico_bollette.json"
data_bollette = load_data_local(bollette_file)
if data_bollette.empty:
    data_bollette = pd.DataFrame(columns=["Mese", "Elettricità", "Gas", "Acqua", "Internet", "Tari"])

st.header("Inserisci Bollette")
mesi_anni_bollette = pd.date_range(start="2024-03-01", end="2030-12-01", freq="MS").strftime("%B %Y")
selected_mese_anno_bollette = st.selectbox("Seleziona il mese e l'anno", mesi_anni_bollette, key="mese_anno_bollette")
mese_datetime_bollette = datetime.strptime(selected_mese_anno_bollette, "%B %Y")

existing_record_bollette = data_bollette[data_bollette["Mese"] == mese_datetime_bollette] if not data_bollette.empty else pd.DataFrame()
elettricita_value = float(existing_record_bollette["Elettricità"].iloc[0]) if not existing_record_bollette.empty else 0.0
gas_value = float(existing_record_bollette["Gas"].iloc[0]) if not existing_record_bollette.empty else 0.0
acqua_value = float(existing_record_bollette["Acqua"].iloc[0]) if not existing_record_bollette.empty else 0.0
internet_value = float(existing_record_bollette["Internet"].iloc[0]) if not existing_record_bollette.empty else 0.0
tari_value = float(existing_record_bollette["Tari"].iloc[0]) if not existing_record_bollette.empty else 0.0

col1_bollette, col2_bollette = st.columns(2)
with col1_bollette:
    elettricita = st.number_input("Elettricità (€)", min_value=0.0, step=10.0, value=elettricita_value, key="elettricita_input")
    gas = st.number_input("Gas (€)", min_value=0.0, step=10.0, value=gas_value, key="gas_input")
    acqua = st.number_input("Acqua (€)", min_value=0.0, step=10.0, value=acqua_value, key="acqua_input")
with col2_bollette:
    internet = st.number_input("Internet (€)", min_value=0.0, step=10.0, value=internet_value, key="internet_input")
    tari = st.number_input("Tari (€)", min_value=0.0, step=10.0, value=tari_value, key="tari_input")

if st.button("Aggiungi/Modifica Bollette", key="aggiorna_bollette"):
    if elettricita > 0 or gas > 0 or acqua > 0 or internet > 0 or tari > 0:
        if not existing_record_bollette.empty:
            data_bollette.loc[data_bollette["Mese"] == mese_datetime_bollette, "Elettricità"] = elettricita
            data_bollette.loc[data_bollette["Mese"] == mese_datetime_bollette, "Gas"] = gas
            data_bollette.loc[data_bollette["Mese"] == mese_datetime_bollette, "Acqua"] = acqua
            data_bollette.loc[data_bollette["Mese"] == mese_datetime_bollette, "Internet"] = internet
            data_bollette.loc[data_bollette["Mese"] == mese_datetime_bollette, "Tari"] = tari
            st.success(f"Record per {selected_mese_anno_bollette} aggiornato!")
        else:
            new_row = {"Mese": mese_datetime_bollette, "Elettricità": elettricita, "Gas": gas,
                       "Acqua": acqua, "Internet": internet, "Tari": tari}
            data_bollette = pd.concat([data_bollette, pd.DataFrame([new_row])], ignore_index=True)
            st.success(f"Bollette per {selected_mese_anno_bollette} aggiunte!")
        data_bollette = data_bollette.sort_values(by="Mese").reset_index(drop=True)
        save_data_local(bollette_file, data_bollette)
    else:
        st.error("Inserisci valori validi per le bollette!")

if st.button(f"Elimina Record per {selected_mese_anno_bollette}", key="elimina_bollette"):
    if not existing_record_bollette.empty:
        data_bollette = data_bollette[data_bollette["Mese"] != mese_datetime_bollette]
        save_data_local(bollette_file, data_bollette)
        st.success(f"Record per {selected_mese_anno_bollette} eliminato!")
    else:
        st.error(f"Il mese {selected_mese_anno_bollette} non è presente nello storico!")

# Input per il budget mensile e calcolo del saldo
budget_mensile = st.number_input("Budget Bollette Mensili (€)", min_value=0.0, step=10.0, value=200.0, key="budget_bollette")
def calcola_saldo(data, budget):
    saldo_iniziale = -50  # saldo iniziale
    saldi = []
    # Assicuriamoci che le colonne esistano
    for col in ["Elettricità", "Gas", "Acqua", "Internet", "Tari"]:
        if col not in data.columns:
            data[col] = 0.0
    for _, row in data.iterrows():
        mese_saldo = saldo_iniziale + budget - (row["Elettricità"] + row["Gas"] + row["Acqua"] + row["Internet"] + row["Tari"])
        saldi.append(mese_saldo)
        saldo_iniziale = mese_saldo
    data["Saldo"] = saldi
    return data

data_bollette = calcola_saldo(data_bollette, budget_mensile)

st.subheader("Dati Storici")
data_display_bollette = data_bollette.copy()
if not data_display_bollette.empty:
    data_display_bollette["Mese"] = data_display_bollette["Mese"].dt.strftime("%B %Y")
st.dataframe(data_display_bollette, use_container_width=True)

# Prepara i dati per il grafico
data_melted_bollette = data_bollette.melt(id_vars=["Mese"], value_vars=["Elettricità", "Gas", "Acqua", "Internet", "Tari"],
                                          var_name="Categoria", value_name="Valore")
data_saldo = data_bollette[["Mese", "Saldo"]].copy()
data_saldo["Categoria"] = "Saldo"
data_completa_bollette = pd.concat([data_melted_bollette, data_saldo])
data_completa_bollette["Mese_str"] = data_completa_bollette["Mese"].dt.strftime("%b %Y")
order = data_completa_bollette.sort_values("Mese")["Mese_str"].unique().tolist()

st.altair_chart(crea_grafico_bollette(data_completa_bollette, order).properties(height=500), use_container_width=True)

st.markdown("---")
