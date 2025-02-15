# python -m streamlit run C:\Users\longh\Desktop\temp.py

import altair as alt
import streamlit as st
import pandas as pd
import json
import os
from datetime import datetime

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
        chart_barre = alt.Chart(df_totali_impilati, title='Confronto Totali per Categoria').mark_bar().encode(
            x=alt.X('Categoria:N', sort=ordine_categorie, title="Categoria", axis=alt.Axis(labelAngle=0)),
            y=alt.Y('Totale:Q', 
                    stack="zero", 
                    title="Totale", 
                    scale=alt.Scale(domain=[0, limite_superiore])  # Aggiunge il margine del 30% al massimo valore
                ),
            color=alt.Color('Tipo:N', 
                            scale=alt.Scale(domain=["Stipendio Originale", "Altre Entrate", "Stipendio Scelto", 
                                                    "Spese Fisse", "Spese Variabili", "Risparmi"], 
                                            range=[color_map["Stipendio Originale"], 
                                                color_map["Altre Entrate"], 
                                                color_map["Stipendio Utilizzato"], 
                                                color_map["Spese Fisse"], 
                                                color_map["Spese Variabili"], 
                                                color_map["Risparmi"]]),
                            legend=alt.Legend(title="Componenti")),
            tooltip=['Categoria', 'Tipo', 'Totale']
        ) + alt.Chart(df_totali_impilati).mark_text(
            align='center',
            baseline='middle',
            color= 'white',
            dy=-6  # Sposta il testo sopra la barra
        ).encode(
            x=alt.X('Categoria:N', sort=ordine_categorie),
            y=alt.Y('Totale:Q', stack="zero"),
            text=alt.Text('Totale:Q', format='.2f')
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























###########################################################################
##############          Storico Stipendi e Risparmi          ##############
###########################################################################





###############################
########## DRIVE ##############
###############################






# Funzione per autenticare l'accesso a Google Drive
SCOPES = ['https://www.googleapis.com/auth/drive']

def authenticate_drive():
    # Carica il token dai secrets
    if "google" not in st.secrets or "token" not in st.secrets["google"]:
        st.error("Token non presente nei secrets. Aggiorna il token in st.secrets.")
        return None

    try:
        token_info = json.loads(st.secrets["google"]["token"])
        creds = Credentials.from_authorized_user_info(token_info, scopes=SCOPES)
    except Exception as e:
        st.error(f"Errore nel caricamento del token dai secrets: {e}")
        return None

    # Se le credenziali non sono valide, mostra l'errore (su Cloud devi aggiornare manualmente il token)
    if not creds or not creds.valid:
        st.error("Le credenziali non sono valide. Aggiorna manualmente il token nei secrets.")
        return None

    try:
        service = build('drive', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"Errore nella creazione del service: {e}")
        return None

    return service

# Funzione per ottenere la lista di file su Google Drive
def get_drive_files():
    drive_service = authenticate_drive()
    if not drive_service:
        return []
    
    results = drive_service.files().list(fields="files(id, name)").execute()
    return results.get('files', [])

# Funzione per selezionare un file da Drive o crearlo se non esiste
def select_or_create_file():
    files = get_drive_files()
    if not files:
        st.error("Nessun file trovato su Google Drive.")
        return None, None
    
    file_names = [file['name'] for file in files]
    # Passa un key univoco per il selectbox
    selected_file_name = st.selectbox("Seleziona un file da Google Drive:", file_names + ["Crea Nuovo File"], key="file_selectbox_unique")

    if selected_file_name == "Crea Nuovo File":
        file_metadata = {'name': 'data.json', 'mimeType': 'application/json'}
        drive_service = authenticate_drive()
        file = drive_service.files().create(body=file_metadata).execute()
        return file['id'], file['name']
    
    selected_file = next(file for file in files if file['name'] == selected_file_name)
    return selected_file['id'], selected_file['name']

# Funzione per caricare dati JSON da Drive
import io
from googleapiclient.http import MediaIoBaseDownload

def load_data(file_id, drive_service):
    try:
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        fh.seek(0)
        file_content = fh.read()
        data = json.loads(file_content)
        data = pd.DataFrame(data)

        # Assicurati che la colonna 'Mese' sia in formato datetime
        data['Mese'] = pd.to_datetime(data['Mese'], errors='coerce')

        # Ordinamento dei dati
        data = data.sort_values(by="Mese").reset_index(drop=True)

        return data
    except Exception as e:
        st.error(f"Errore nel caricamento del file: {e}")
        return pd.DataFrame()

# Funzione per salvare dati su Google Drive
def save_data(data, file_id, drive_service):
    try:
        data_dict = data.to_dict(orient="records")
        json_content = json.dumps(data_dict, indent=4, default=str)
        
        temp_file = "temp_data.json"
        with open(temp_file, "w") as file:
            file.write(json_content)
        
        media = MediaFileUpload(temp_file, mimetype='application/json')
        drive_service.files().update(fileId=file_id, media_body=media).execute()
        os.remove(temp_file)
        st.success("Dati salvati correttamente su Google Drive.")
    except Exception as e:
        st.error(f"Errore nel salvataggio del file: {e}")





###############################
########## MAIN ###############
###############################






# Titolo dell'app
st.title("Storico Stipendi e Risparmi")

col_1, col_empty, col_2 = st.columns([3, 1, 6])
with col_2:
    st.write("### Seleziona o Crea File")
    file_id, file_name = select_or_create_file()
    if file_id:
        drive_service = authenticate_drive()
        data = load_data(file_id, drive_service)
        # Verifica che il file contenga le colonne attese
        if not ("Stipendio" in data.columns and "Risparmi" in data.columns):
            st.info("Il file selezionato non contiene i dati richiesti (colonne 'Stipendio' e 'Risparmi'). Seleziona il file corretto e riprova.")
            st.stop()
        # Imposta i dati nello stato della sessione
        st.session_state.data = data
    else:
        st.error("Nessun file selezionato.")
        st.stop()

# Verifica che st.session_state.data esista e sia non vuoto
if "data" not in st.session_state or st.session_state.data.empty:
    st.error("Nessun dato disponibile. Seleziona o crea un file su Google Drive e carica i dati.")
    st.stop()

# Ora puoi assegnare in sicurezza la variabile data
data = st.session_state.data

# --- Sezione per l'inserimento/modifica dati (col_1) ---
with col_1:
    st.write("### Inserisci Stipendio e Risparmi")
    mesi_anni = pd.date_range(start="2024-03-01", end="2030-12-01", freq="MS").strftime("%B %Y")
    selected_mese_anno = st.selectbox("Seleziona il mese e l'anno", mesi_anni, key="mese_anno_selectbox")
    
    mese_datetime = datetime.strptime(selected_mese_anno, "%B %Y")
    
    # Cerca un record esistente
    if "Mese" in data.columns:
        existing_record = data.loc[data["Mese"] == mese_datetime]
    else:
        existing_record = pd.DataFrame()
    
    stipendio_value = existing_record["Stipendio"].values[0] if not existing_record.empty else 0.0
    risparmi_value = existing_record["Risparmi"].values[0] if not existing_record.empty else 0.0
    
    col_sx, col_dx = st.columns(2)
    with col_dx:
        risparmi = st.number_input("Risparmi (€)", min_value=0.0, step=100.0, key="risparmi_input", value=risparmi_value)
    if st.button(f"Elimina Record per {selected_mese_anno}", key=f"elimina_{selected_mese_anno}"):
        if not existing_record.empty:
            data = data[data["Mese"] != mese_datetime]
            save_data(data, file_id, drive_service)
            st.success(f"Record per {selected_mese_anno} eliminato!")
            # st.experimental_rerun()
        else:
            st.error(f"Il mese {selected_mese_anno} non è presente nello storico!")
    with col_sx:
        stipendio = st.number_input("Stipendio (€)", min_value=0.0, step=100.0, key="stipendio_input", value=stipendio_value)
        if st.button("Aggiungi/Modifica Stipendio e Risparmi"):
            if stipendio > 0 or risparmi > 0:
                if not existing_record.empty:
                    data.loc[data["Mese"] == mese_datetime, "Stipendio"] = stipendio
                    data.loc[data["Mese"] == mese_datetime, "Risparmi"] = risparmi
                    st.success(f"Record per {selected_mese_anno} aggiornato!")
                else:
                    new_row = {"Mese": mese_datetime, "Stipendio": stipendio, "Risparmi": risparmi}
                    data = pd.concat([data, pd.DataFrame([new_row])], ignore_index=True)
                    st.success(f"Stipendio e Risparmi per {selected_mese_anno} aggiunti!")
                data = data.sort_values(by="Mese").reset_index(drop=True)
                save_data(data, file_id, drive_service)
                # st.experimental_rerun()
            else:
                st.error("Inserisci valori validi per stipendio e/o risparmi!")

st.markdown("---")




# Funzione per calcolare somma e media
@st.cache_data
def calcola_statistiche(data, colonne):
    stats = {col: {'somma': data[col].sum(), 'media': round(data[col].mean(), 2)} for col in colonne}
    return stats

# Funzione per calcolare medie mobili e medie no 13°/PDR
def calcola_medie(data, colonne):
    for col in colonne:
        data[f"Media {col}"] = data[col].expanding().mean().round(2)
        if col == "Stipendio":  # Solo per gli stipendi calcola la media no 13°/PDR
            data[f"Media {col} NO 13°/PDR"] = data[col].where(~data["Mese"].dt.month.isin([7, 12])).expanding().mean().round(2)
    return data

# Funzione per creare i grafici
def crea_grafico(data, categorie, dominio, colori, line_style=None):
    base = alt.Chart(data.query(f"Categoria in {categorie}"))

    # Configura il tratteggio solo se specificato
    linee = base.mark_line(
        strokeDash=(5, 5) if line_style == "dashed" else alt.Undefined,
        strokeWidth=2
    ).encode(
        x=alt.X("Mese:T", title="Mese", axis=alt.Axis(tickCount="month")),
        y=alt.Y("Valore:Q", title="Valore (€)"),
        color=alt.Color(
            "Categoria:N",
            scale=alt.Scale(domain=dominio, range=colori),
            legend=alt.Legend(title="Categorie")
        ),
        tooltip=["Mese:T", "Categoria:N", "Valore:Q"]
    )

    punti = base.mark_point(shape="diamond", size=100, filled=True, opacity=0.7).encode(
        x="Mese:T",
        y="Valore:Q",
        color=alt.Color(
            "Categoria:N",
            scale=alt.Scale(domain=dominio, range=colori)
        ),
        tooltip=["Mese:T", "Categoria:N", "Valore:Q"]
    )

    return linee + punti

# Calcoli principali
statistiche = calcola_statistiche(data, ["Stipendio", "Risparmi"])
data = calcola_medie(data, ["Stipendio", "Risparmi"])

# Prepara i dati per i grafici
data_completa = pd.concat([
    data.melt(id_vars=["Mese"], value_vars=["Stipendio", "Risparmi"], var_name="Categoria", value_name="Valore"),
    data.melt(id_vars=["Mese"], value_vars=["Media Stipendio", "Media Risparmi", "Media Stipendio NO 13°/PDR"], var_name="Categoria", value_name="Valore")
])

# Layout Streamlit
if not data.empty:
    col_tabella, col_grafico = st.columns([2, 3.7])

    # Tabella
    data_display = data.copy()
    data_display["Mese"] = data_display["Mese"].dt.strftime('%B %Y')
    with col_tabella:
        st.dataframe(data_display, use_container_width=True)
        col_left, col_right = st.columns(2)
        with col_left:
            st.write(f"**Somma Stipendio:** <span style='color:#77DD77;'>{statistiche['Stipendio']['somma']:,.2f} €</span>", unsafe_allow_html=True)
            st.write(f"**Media Stipendio:** <span style='color:#FF6961;'>{statistiche['Stipendio']['media']:,.2f} €</span>", unsafe_allow_html=True)
            st.write(f"**Media no 13°/PDR:** <span style='color:#FFA07A;'>{data['Media Stipendio NO 13°/PDR'].iloc[-1]:,.2f} €</span>", unsafe_allow_html=True)
        with col_right:
            st.write(f"**Somma Risparmi:** <span style='color:#FFFF99;'>{statistiche['Risparmi']['somma']:,.2f} €</span>", unsafe_allow_html=True)
            st.write(f"**Media Risparmi:** <span style='color:#84B6F4;'>{statistiche['Risparmi']['media']:,.2f} €</span>", unsafe_allow_html=True)

    # Grafico
    dominio_categorie = ["Stipendio", "Risparmi", "Media Stipendio", "Media Risparmi", "Media Stipendio NO 13°/PDR"]
    scala_colori = ["#77DD77", "#FFFF99", "#FF6961", "#84B6F4", "#FFA07A"]

    grafico_principale = crea_grafico(data_completa, ["Stipendio", "Risparmi"], dominio_categorie, scala_colori)
    grafico_medie = crea_grafico(data_completa, ["Media Stipendio", "Media Risparmi", "Media Stipendio NO 13°/PDR"], dominio_categorie, scala_colori, line_style="dashed")

    with col_grafico:
        st.altair_chart((grafico_principale + grafico_medie).properties(height=500, width='container'), use_container_width=True)

st.markdown('<hr style="width: 100%; height:5px;border-width:0;color:gray;background-color:gray">', unsafe_allow_html=True)
















################################################################
##############          Storico Bollette          ##############
################################################################





###############################
########## DRIVE ##############
###############################






SCOPES = ['https://www.googleapis.com/auth/drive']

def authenticate_drive():
    # Carica il token dai secrets (non usa flusso interattivo, solo token dai secrets)
    if "google" not in st.secrets or "token" not in st.secrets["google"]:
        st.error("Token non presente nei secrets. Aggiorna il token in st.secrets.")
        return None
    try:
        token_info = json.loads(st.secrets["google"]["token"])
        creds = Credentials.from_authorized_user_info(token_info, scopes=SCOPES)
    except Exception as e:
        st.error(f"Errore nel caricamento del token dai secrets: {e}")
        return None
    if not creds or not creds.valid:
        st.error("Le credenziali non sono valide. Aggiorna manualmente il token nei secrets.")
        return None
    try:
        service = build('drive', 'v3', credentials=creds)
    except Exception as e:
        st.error(f"Errore nella creazione del service: {e}")
        return None
    return service

def get_drive_files():
    drive_service = authenticate_drive()
    if not drive_service:
        return []
    results = drive_service.files().list(fields="files(id, name)").execute()
    return results.get('files', [])

def select_or_create_file():
    files = get_drive_files()
    if not files:
        st.error("Nessun file trovato su Google Drive.")
        return None, None
    file_names = [file['name'] for file in files]
    selected_file_name = st.selectbox("Seleziona un file da Google Drive:", file_names + ["Crea Nuovo File"], key="file_selectbox_unique2")
    if selected_file_name == "Crea Nuovo File":
        file_metadata = {'name': 'data.json', 'mimeType': 'application/json'}
        drive_service = authenticate_drive()
        file = drive_service.files().create(body=file_metadata).execute()
        return file['id'], file['name']
    selected_file = next(file for file in files if file['name'] == selected_file_name)
    return selected_file['id'], selected_file['name']

def load_data(file_id, drive_service):
    try:
        request = drive_service.files().get_media(fileId=file_id)
        fh = pd.io.common.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        fh.seek(0)
        file_content = fh.read()
        data = json.loads(file_content)
        data = pd.DataFrame(data)
        data['Mese'] = pd.to_datetime(data['Mese'], errors='coerce')
        data = data.sort_values(by="Mese").reset_index(drop=True)
        return data
    except Exception as e:
        st.error(f"Errore nel caricamento del file: {e}")
        return pd.DataFrame()

def save_data(data, file_id, drive_service):
    try:
        data_copy = data.copy()
        if 'Mese' in data_copy.columns:
            data_copy['Mese'] = data_copy['Mese'].dt.strftime('%Y-%m-%d')
        data_dict = data_copy.to_dict(orient="records")
        json_content = json.dumps(data_dict, indent=4, default=str)
        temp_file = "temp_data.json"
        with open(temp_file, "w") as file:
            file.write(json_content)
        media = MediaFileUpload(temp_file, mimetype='application/json')
        drive_service.files().update(fileId=file_id, media_body=media).execute()
        os.remove(temp_file)
        st.success("Dati salvati correttamente su Google Drive.")
    except Exception as e:
        st.error(f"Errore nel salvataggio del file: {e}")

st.title("Storico Bollette")

# Seleziona o crea il file su Google Drive
file_id, file_name = select_or_create_file()
if file_id:
    drive_service = authenticate_drive()
    data = load_data(file_id, drive_service)
    st.session_state.data = data
else:
    st.error("Nessun file selezionato.")
    st.stop()

# Verifica che lo stato contenga dati
if "data" not in st.session_state or st.session_state.data.empty:
    st.error("Nessun dato disponibile. Seleziona o crea un file su Google Drive e carica i dati.")
    st.stop()

data = st.session_state.data

# Suddividi lo schermo in 3 colonne: una a sinistra, uno spazio, e la colonna a destra per l'input
col1sx, colempty, col2dx = st.columns([3, 1, 6])

with col2dx:
    st.write("### Inserisci Bollette")
    # Crea l'elenco dei mesi/anni (formato 'Mese Anno')
    mesi_anni = pd.date_range(start="2024-03-01", end="2030-12-01", freq="MS").strftime("%B %Y")
    selected_mese_anno = st.selectbox("Seleziona il mese e l'anno", mesi_anni, key="mese_anno_bollette")
    
    mese_datetime = datetime.datetime.strptime(selected_mese_anno, "%B %Y")
    
    if "Mese" in data.columns:
        existing_record = data.loc[data["Mese"] == mese_datetime]
    else:
        existing_record = pd.DataFrame()
    
    elettricita_value = existing_record["Elettricità"].values[0] if not existing_record.empty else 0.0
    gas_value = existing_record["Gas"].values[0] if not existing_record.empty else 0.0
    acqua_value = existing_record["Acqua"].values[0] if not existing_record.empty else 0.0
    internet_value = existing_record["Internet"].values[0] if not existing_record.empty else 0.0
    tari_value = existing_record["Tari"].values[0] if not existing_record.empty else 0.0
    
    col_sx, col_dx = st.columns(2)
    with col_dx:
        acqua = st.number_input("Acqua (€)", min_value=0.0, step=10.0, key="acqua_input", value=acqua_value)
        tari = st.number_input("Tari (€)", min_value=0.0, step=10.0, key="tari_input", value=tari_value)
        internet = st.number_input("Internet (€)", min_value=0.0, step=10.0, key="internet_input", value=internet_value)
        if st.button(f"Elimina Record per {selected_mese_anno}", key=f"elimina2_{selected_mese_anno}"):
            if not existing_record.empty:
                data = data[data["Mese"] != mese_datetime]
                save_data(data, file_id, authenticate_drive())
                st.success(f"Record per {selected_mese_anno} eliminato!")
            else:
                st.error(f"Il mese {selected_mese_anno} non è presente nello storico!")
    with col_sx:
        elettricita = st.number_input("Elettricità (€)", min_value=0.0, step=10.0, key="elettricita_input", value=elettricita_value)
        gas = st.number_input("Gas (€)", min_value=0.0, step=10.0, key="gas_input", value=gas_value)
        if st.button("Aggiungi/Modifica Bollette", key="modifica_bollette"):
            if elettricita > 0 or gas > 0 or acqua > 0 or internet > 0 or tari > 0:
                if not existing_record.empty:
                    data.loc[data["Mese"] == mese_datetime, "Elettricità"] = elettricita
                    data.loc[data["Mese"] == mese_datetime, "Gas"] = gas
                    data.loc[data["Mese"] == mese_datetime, "Acqua"] = acqua
                    data.loc[data["Mese"] == mese_datetime, "Internet"] = internet
                    data.loc[data["Mese"] == mese_datetime, "Tari"] = tari
                    st.success(f"Record per {selected_mese_anno} aggiornato!")
                else:
                    new_row = {
                        "Mese": mese_datetime,
                        "Elettricità": elettricita,
                        "Gas": gas,
                        "Acqua": acqua,
                        "Internet": internet,
                        "Tari": tari
                    }
                    data = pd.concat([data, pd.DataFrame([new_row])], ignore_index=True)
                    st.success(f"Bollette per {selected_mese_anno} aggiunte!")
                data = data.sort_values(by="Mese").reset_index(drop=True)
                save_data(data, file_id, authenticate_drive())
            else:
                st.error("Inserisci valori validi per le bollette!")

# Separa l'input dai grafici
st.markdown("---")

# --- CALCOLO SALDO (con incremento mensile) ---
def calcola_saldo(data, decisione_budget_bollette_mensili):
    saldo_iniziale = -50  # Saldo iniziale (puoi modificare questo valore)
    saldi = []
    required_columns = ['Elettricità', 'Gas', 'Acqua', 'Internet', 'Tari']
    for col in required_columns:
        if col not in data.columns:
            data[col] = 0.0
    for _, row in data.iterrows():
        mese_saldo = saldo_iniziale + decisione_budget_bollette_mensili - (row['Elettricità'] +
                        row['Gas'] + row['Acqua'] + row['Internet'] + row['Tari'])
        saldi.append(mese_saldo)
        saldo_iniziale = mese_saldo
    data['Saldo'] = saldi
    data["Saldo"] = pd.to_numeric(data["Saldo"], errors="coerce").fillna(0.0)
    return data

# Ricalcola il saldo con il budget mensile
data = calcola_saldo(data, decisione_budget_bollette_mensili)
st.session_state.data = data

# --- CALCOLO STATISTICHE E GRAFICI ---
@st.cache_data
def calcola_statistiche(data, colonne):
    stats = {col: {'somma': data[col].sum(), 'media': round(data[col].mean(), 2)} for col in colonne}
    return stats

# Funzione per creare i grafici: barre per le bollette e linea per il saldo
def crea_grafico(data, categorie, dominio, colori):
    categorie_bar = [c for c in categorie if c != "Saldo"]
    barre = alt.Chart(data.query(f"Categoria in {categorie_bar}")).mark_bar(
        opacity=0.8, size=70
    ).encode(
        x=alt.X("Mese:T", title="Mese", axis=alt.Axis(tickCount="month")),
        y=alt.Y("Valore:Q", title="Valore (€)"),
        color=alt.Color(
            "Categoria:N",
            scale=alt.Scale(domain=dominio, range=colori),
            legend=alt.Legend(title="Categorie")
        ),
        tooltip=["Mese:T", "Categoria:N", "Valore:Q"]
    )
    
    saldo_neg = data.query("Categoria == 'Saldo' and Valore < 0")
    saldo_pos = data.query("Categoria == 'Saldo' and Valore >= 0")
    
    # Linea rossa per i saldi negativi
    linea_saldo_neg = alt.Chart(saldo_neg).mark_line(
        strokeDash=[5, 5],
        strokeWidth=2,
        color="#FF6961"
    ).encode(
        x="Mese:T",
        y="Valore:Q"
    )
    
    # Punti rossi per i saldi negativi
    punti_saldo_neg = alt.Chart(saldo_neg).mark_point(
        shape="diamond",
        size=80,
        filled=True,
        color="#FF6961"
    ).encode(
        x="Mese:T",
        y="Valore:Q",
        tooltip=["Mese:T", "Valore:Q"]
    )
    
    # Linea verde per i saldi positivi
    linea_saldo_pos = alt.Chart(saldo_pos).mark_line(
        strokeDash=[5, 5],
        strokeWidth=2,
        color="#77DD77"
    ).encode(
        x="Mese:T",
        y="Valore:Q"
    )
    
    # Punti verdi per i saldi positivi
    punti_saldo_pos = alt.Chart(saldo_pos).mark_point(
        shape="diamond",
        size=80,
        filled=True,
        color="#77DD77"
    ).encode(
        x="Mese:T",
        y="Valore:Q",
        tooltip=["Mese:T", "Valore:Q"]
    )
    
    linea_saldo = linea_saldo_neg + punti_saldo_neg + linea_saldo_pos + punti_saldo_pos
    
    return barre + linea_saldo

statistiche = calcola_statistiche(data, ["Elettricità", "Gas", "Acqua", "Internet", "Tari"])

# Prepara i dati per il grafico:
# - "data_melted" per le categorie delle bollette
data_melted = data.melt(id_vars=["Mese"], value_vars=["Elettricità", "Gas", "Acqua", "Internet", "Tari"],
                        var_name="Categoria", value_name="Valore")
# - "data_saldo" per il saldo (con la colonna "Categoria" impostata a "Saldo")
data_saldo = data[["Mese", "Saldo"]].copy()
data_saldo["Categoria"] = "Saldo"

# Unisci i due DataFrame per avere un unico dataset per il grafico
data_completa = pd.concat([data_melted, data_saldo.melt(id_vars=["Mese"], value_vars=["Saldo"],
                                                        var_name="Categoria", value_name="Valore")])

# Layout della visualizzazione: tabella e grafico
if not data.empty:
    col_tabella, col_grafico = st.columns([2, 3.7])
    
    # Tabella dei dati con la colonna "Mese" formattata
    data_display = data.copy()
    data_display["Mese"] = data_display["Mese"].dt.strftime('%B %Y')
    
    with col_tabella:
        st.dataframe(data_display, use_container_width=True)
        col_left, col_right = st.columns(2)
        with col_left:
            st.write(f"**Somma Elettricità:** <span style='color:#84B6F4;'>{statistiche['Elettricità']['somma']:,.2f} €</span>", unsafe_allow_html=True)
            st.write(f"**Somma Gas:** <span style='color:#FF6961;'>{statistiche['Gas']['somma']:,.2f} €</span>", unsafe_allow_html=True)
        with col_right:
            st.write(f"**Somma Acqua:** <span style='color:#96DED1;'>{statistiche['Acqua']['somma']:,.2f} €</span>", unsafe_allow_html=True)
            st.write(f"**Somma Tari:** <span style='color:#C19A6B;'>{statistiche['Tari']['somma']:,.2f} €</span>", unsafe_allow_html=True)
            st.write(f"**Somma Internet:** <span style='color:#FFF5A1;'>{statistiche['Internet']['somma']:,.2f} €</span>", unsafe_allow_html=True)
    
    # Visualizza il grafico combinato (barre + linea per il saldo)
    dominio_categorie = ["Elettricità", "Gas", "Acqua", "Internet", "Tari", "Saldo"]
    scala_colori = ["#84B6F4", "#FF6961", "#96DED1", "#FFF5A1", "#C19A6B", "#FF6961"]
    grafico_principale = crea_grafico(data_completa, dominio_categorie, dominio_categorie, scala_colori)
    
    with col_grafico:
        st.altair_chart(grafico_principale.properties(height=500, width='container'), use_container_width=True)
    
    # Salva i dati aggiornati su Google Drive
    save_data(data, file_id, authenticate_drive())

st.markdown('<hr style="width: 100%; height:5px;border-width:0;color:gray;background-color:gray">', unsafe_allow_html=True)
