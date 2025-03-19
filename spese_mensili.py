# python -m streamlit run C:\Users\longh\Desktop\temp.py

import altair as alt
import streamlit as st
import pandas as pd
import json
import os
from datetime import datetime
import time
import io

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
        "Trasporti": 135,
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
                            legend=alt.Legend(title=None)),
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
        st.markdown(f'**Totale Spese Fisse:** <span style="color:#F08080;">€{spese_fisse_totali:.2f}</span><span style="color:#B8C070; float:right;"> - Risparmiabili: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp; <span style="color:#808080;"> Stipendio da Utilizzare - Spese fisse = </span>€{risparmiabili:.2f}</span>', unsafe_allow_html=True)
        st.markdown(f' <small span style="color:#F08080;"> {(spese_fisse_totali) / stipendio * 100:.2f} % dello Stipendio da Utilizzare</span> <small span style="color:#B8C070; float:right;"> {(risparmiabili) / stipendio * 100:.2f} % dello Stipendio da Utilizzare </span>', unsafe_allow_html=True)
        st.markdown(f' <small span style="color:#F08080;"> {(spese_fisse_totali) / (stipendio_originale + sum(ALTRE_ENTRATE.values())) * 100:.2f} % dello Stipendio Totale</span> <small span style="color:#B8C070; float:right;"> {(risparmiabili) / (stipendio_originale + sum(ALTRE_ENTRATE.values())) * 100:.2f} % dello Stipendio Totale </span>', unsafe_allow_html=True)

        # Calcolo dei due stipendi
        stipendio_totale = stipendio_originale + sum(ALTRE_ENTRATE.values())
        stipendio_utilizzare = stipendio_scelto + sum(ALTRE_ENTRATE.values())

        # DataFrame per ciascun grafico
        df_totale = pd.DataFrame({
            'Component': ['Spese Fisse', 'Risparmiabili'],
            'Value': [spese_fisse_totali, stipendio_totale - spese_fisse_totali]
        })
        df_utilizzare = pd.DataFrame({
            'Component': ['Spese Fisse', 'Risparmiabili'],
            'Value': [spese_fisse_totali, stipendio_utilizzare - spese_fisse_totali]
        })

        # Creiamo la colonna per la percentuale nel dataframe
        df_totale["Percentuale"] = (df_totale["Value"] / df_totale["Value"].sum()) * 100
        df_utilizzare["Percentuale"] = (df_utilizzare["Value"] / df_utilizzare["Value"].sum()) * 100

        # Chart per lo Stipendio Totale (colori spenti, opachi)
        chart_totale = alt.Chart(df_totale).mark_arc(innerRadius=35, outerRadius=50).encode(
            theta=alt.Theta(field="Value", type="quantitative"),
            color=alt.Color(
                field="Component", 
                type="nominal", 
                scale=alt.Scale(
                    domain=['Spese Fisse', 'Risparmiabili'], 
                    range=['rgba(255, 100, 100, 0.3)', 'rgba(184, 192, 112, 0.3)']
                ),
                legend=None
            ),
            tooltip=[
                alt.Tooltip("Component:N", title="Categoria"),
                alt.Tooltip("Value:Q", title="Valore (€)", format=".2f"),
                alt.Tooltip("Percentuale:Q", title="Percentuale", format=".2f")
            ]
        ).properties(
            title="Stipendio Totale", 
            width=100, 
            height=100
        )

        # Chart per lo Stipendio da Utilizzare (colori originali)
        chart_utilizzare = alt.Chart(df_utilizzare).mark_arc(innerRadius=35, outerRadius=50).encode(
            theta=alt.Theta(field="Value", type="quantitative"),
            color=alt.Color(
                field="Component", 
                type="nominal", 
                scale=alt.Scale(
                    domain=['Spese Fisse', 'Risparmiabili'], 
                    range=['#FF6961', '#B8C070']
                ),
                legend=alt.Legend(title=None)
            ),
            tooltip=[
                alt.Tooltip("Component:N", title="Categoria"),
                alt.Tooltip("Value:Q", title="Valore (€)", format=".2f"),
                alt.Tooltip("Percentuale:Q", title="Percentuale", format=".2f")
            ]
        ).properties(
            title="Stipendio da Utilizzare", 
            width=100, 
            height=100
        )

        # Unione orizzontale dei due grafici con scale di colore indipendenti
        chart_donut = (chart_totale | chart_utilizzare).resolve_scale(color='independent')

        # Centratura del grafico usando le colonne di Streamlit
        _, col2utilizzato, _ = st.columns([0.5, 1, 0.5])
        with col2utilizzato:
            st.altair_chart(chart_donut, use_container_width=True)




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
        _, right_col = st.columns([1, 2])
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

        _, left_left_col, _ = st.columns([0.5, 1.5, 1])
        with left_left_col:
            # Calcolo dei singoli risparmi (assumendo che le variabili siano già state calcolate in precedenza)
            savings_from_salary = risparmio_stipendi            # Risparmio derivante dalla scelta dello stipendio
            savings_from_previous = risparmi_mese_precedente       # Risparmi portati dal mese precedente
            savings_from_da_spendere = risparmio_da_spendere       # Risparmio extra dalla quota "Da spendere"
            savings_from_daily = risparmio_spese_quotidiane        # Risparmio extra dalla quota "Spese quotidiane"

            # Totale dei risparmi
            total_savings = savings_from_salary + savings_from_previous + savings_from_da_spendere + savings_from_daily

            # Calcolo della parte non risparmiata rispetto allo stipendio totale
            non_saved = stipendio_totale - total_savings

            # Creazione del DataFrame per il grafico ad anello
            df_savings = pd.DataFrame({
                'Component': [
                    'Risparmi da Stipendi', 
                    'Risparmi da Spendere', 
                    'Risparmi da Mese Precedente', 
                    'Risparmi da Spese Quotidiane', 
                    'Spesi'
                ],
                'Value': [
                    savings_from_salary, 
                    savings_from_previous, 
                    savings_from_da_spendere, 
                    savings_from_daily, 
                    non_saved
                ]
            })

            # Creazione del grafico ad anello (donut chart)
            chart_savings = alt.Chart(df_savings).mark_arc(innerRadius=35, outerRadius=50).encode(
                theta=alt.Theta(field="Value", type="quantitative"),
                color=alt.Color(
                    field="Component",
                    type="nominal",
                    scale=alt.Scale(
                        # Specifica l'ordine (domain) e i colori da usare
                        domain=[
                            'Risparmi da Stipendi', 
                            'Risparmi da Spendere', 
                            'Risparmi da Mese Precedente', 
                            'Risparmi da Spese Quotidiane', 
                            'Spesi'
                        ],
                        range=['#808080', '#FFFF99', '#808080', '#FFFF99', '#E0E0E0']
                    ),
                    legend=alt.Legend(title=None, orient='right', direction='vertical')
                ),
                tooltip=['Component', 'Value']
            ).properties(
                title="Risparmiati del mese",
                width=100,
                height=100,
            )

            st.altair_chart(chart_savings, use_container_width=False)




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
                st.markdown(f'Totale da &nbsp; **<em style="color: #A0A0A0;">{testo}</em> &nbsp; su <span style="color:{colore}; text-decoration: underline;">{carta}</span>:** <span style="color:{colore}">€{totale_carta:.2f}</span> <span style="font-size: 14px; color: gray;"> &nbsp;&nbsp;( + <span style="color:{colore}; font-size: 14px;">{risparmi_mese_precedente:.2f}</span> dai Risparmi + <span style="color:{colore}; font-size: 14px;">€25.50</span> da Disn/Spoti -> Vedrai: <span style="color:{colore}; font-size: 14px;">€{somma_valori:.2f}</span> )</span>', unsafe_allow_html=True)
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















#####################################
# FUNZIONI PER GESTIONE FILE LOCALE
#####################################

def load_data_local(percorso_file):
    """
    Carica i dati da un file JSON locale e restituisce un DataFrame.
    Se il file non esiste, restituisce un DataFrame vuoto.
    """
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
    """
    Salva il DataFrame in formato JSON sul percorso indicato.
    ATTENZIONE: Su Streamlit Cloud il filesystem è effimero, quindi le modifiche
    non verranno committate automaticamente sul repository GitHub.
    """
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
    # Converte il DataFrame in un dizionario e poi in una stringa JSON formattata con indentazione
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
    for col in colonne:
        data[f"Media {col}"] = data[col].expanding().mean().round(2)
        if col == "Stipendio":
            data[f"Media {col} NO 13°/PDR"] = data[col].where(~data["Mese"].dt.month.isin([7, 12])).expanding().mean().round(2)
    return data

def crea_grafico_stipendi(data):
    # Prepara i dati unendo i valori originali e le medie
    data_completa = pd.concat([
        data.melt(id_vars=["Mese"], value_vars=["Stipendio", "Risparmi", "Messi da parte Totali"],
                  var_name="Categoria", value_name="Valore"),
        data.melt(id_vars=["Mese"], value_vars=["Media Stipendio", "Media Risparmi", "Media Stipendio NO 13°/PDR", "Media Messi da parte Totali"],
                  var_name="Categoria", value_name="Valore")
    ])

    # Rinominare le categorie per rendere plurale quelle relative agli stipendi
    data_completa["Categoria"] = data_completa["Categoria"].replace({
        "Stipendio": "Stipendi",
        "Media Stipendio": "Media Stipendi",
        "Media Stipendio NO 13°/PDR": "Media Stipendi NO 13°/PDR"
    })

    # Definisci le serie che vuoi visualizzare come barre
    bar_categories = ["Risparmi", "Messi da parte Totali"]
    bar_color_range = ["rgba(255, 255, 204, 0.2)", "#CFCB62"]
    # Le altre serie (linee)
    line_categories = ["Stipendi", "Media Stipendi", "Media Stipendi NO 13°/PDR", "Media Risparmi", "Media Messi da parte Totali"]
    line_color_range = ["#77DD77", "rgba(255, 105, 97, 0.2)", "#FFA07A", "rgba(132, 182, 244, 0.2)", "#2E75B6"]

    # Per l'asse X, creiamo una colonna formattata
    data_completa["Mese_str"] = data_completa["Mese"].dt.strftime("%b %Y")

    # Suddividi il dataset in due: uno per le barre e uno per le linee
    df_bar = data_completa[data_completa["Categoria"].isin(bar_categories)]
    df_line = data_completa[~data_completa["Categoria"].isin(bar_categories)]

    # Grafico a linee (con i punti) per le serie lineari
    base_line = alt.Chart(df_line).encode(
        x=alt.X("Mese:T", title="Mese", axis=alt.Axis(tickCount="month")),
        y=alt.Y("Valore:Q", title="Valore (€)")
    )
    line_chart = base_line.mark_line(strokeWidth=2, strokeDash=[5,5]).encode(
        alt.Color("Categoria:N",
                  scale=alt.Scale(domain=line_categories, range=line_color_range),
                  title="Stipendi")
    )
    points_chart = base_line.mark_point(shape="diamond", size=100, filled=True, opacity=0.7).encode(
        alt.Color("Categoria:N",
                  scale=alt.Scale(domain=line_categories, range=line_color_range),
                  title="Stipendi")
    )
    chart_line = line_chart + points_chart

    # Grafico a barre per "Risparmi" e "Messi da parte Totali"
    chart_bar = alt.Chart(df_bar).mark_bar(size=60).encode(
        x=alt.X("Mese:T", title="Mese"),
        xOffset="Categoria:N",
        y=alt.Y("Valore:Q", title="Valore (€)"),
        color=alt.Color("Categoria:N",
                        scale=alt.Scale(domain=bar_categories, range=bar_color_range),
                        title="Risparmi")
    )

    # Etichette sopra le barre di "Messi da parte Totali"
    text_labels = alt.Chart(df_bar[df_bar["Categoria"] == "Messi da parte Totali"]).mark_text(
        dy=-20, size=12, color='white'
    ).encode(
        x=alt.X("Mese:T"),
        y=alt.Y("Valore:Q"),
        text=alt.Text("Valore:Q")
    )

    # Sovrapponi i due grafici; per avere lo stesso asse Y e scale colore indipendenti
    final_chart = alt.layer(chart_bar, text_labels, chart_line).resolve_scale(
        y="shared",
        color="independent"
    )
    return final_chart

def crea_grafico_bollette_linea_continua(data_completa, order):
    """
    Crea un grafico a barre impilate con etichette per le categorie delle bollette
    e una linea continua (in grigio) con punti colorati (rosso per i saldi negativi,
    verde per i saldi positivi) per rappresentare il saldo.
    """
    # Dati per le barre (escludendo il saldo)
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
    ).mark_text(
        color="black",
        align="center",
        baseline="middle"
    ).encode(
        x=alt.X("Mese_str:N", sort=order),
        y=alt.Y("mid:Q"),
        text=alt.Text("Valore:Q", format=".2f")
    )
    
    df_saldo = data_completa[data_completa["Categoria"] == "Saldo"]
    # --- Linea continua per il saldo con punti colorati ---
    linea_saldo_unica = alt.Chart(df_saldo).mark_line(
        strokeWidth=2,
        strokeDash=[5,5],    # Linea tratteggiata
        color="#F0F0F0",     # Grigio molto chiaro
        opacity=0.25          # Maggiore trasparenza
    ).encode(
        x=alt.X("Mese_str:N", sort=order),
        y=alt.Y("Valore:Q"),
        tooltip=["Mese_str:N", "Valore:Q"]
    )

    punti_saldo_color = alt.Chart(df_saldo).mark_point(
        shape="diamond",
        size=80,
        filled=True
    ).encode(
        x=alt.X("Mese_str:N", sort=order),
        y=alt.Y("Valore:Q"),
        color=alt.condition("datum.Valore < 0",
                            alt.value("#FF6961"),  # rosso per valori negativi
                            alt.value("#77DD77")), # verde per valori positivi
        tooltip=["Mese_str:N", "Valore:Q"]
    )

    linea_saldo = linea_saldo_unica + punti_saldo_color
    
    # --- Totale mensile sopra le barre ---
    # Raggruppa solo i dati delle bollette (escludendo il saldo) per ottenere il totale per ogni mese
    df_totali = data_completa[data_completa["Categoria"].isin(["Elettricità", "Gas", "Acqua", "Internet", "Tari"])].groupby(
        ["Mese", "Mese_str"], as_index=False
    )["Valore"].sum()
    
    testo_totale = alt.Chart(df_totali).mark_text(
        align="center",
        baseline="bottom",
        dy=-5,         # sposta il testo leggermente verso l'alto
        fontSize=12,
        color="white"
    ).encode(
        x=alt.X("Mese_str:N", sort=order),
        y=alt.Y("Valore:Q"),
        text=alt.Text("Valore:Q", format=".2f")
    )
    
    # Combina la linea continua e i punti colorati
    linea_saldo = linea_saldo_unica + punti_saldo_color
    
    # Crea il grafico finale sovrapponendo barre, etichette e la linea del saldo
    grafico_finale = alt.layer(barre, labels, linea_saldo, testo_totale)
    return grafico_finale
    
def crea_confronto_anno_su_anno_stipendi(data):
    """
    Crea un grafico a linee che confronta la media mensile dello stipendio 
    per ciascun anno.
    """
    df = data.copy()
    # Aggiunge la colonna "Anno" come stringa e "Mese_str" con il nome abbreviato del mese
    df["Anno"] = df["Mese"].dt.year.astype(str)
    df["Mese_str"] = df["Mese"].dt.strftime("%b")
    
    # Crea il grafico a linee: asse X = mese (ordinato cronologicamente), Y = media dello stipendio, colore = anno
    chart = alt.Chart(df).mark_line(point=True).encode(
        x=alt.X("Mese_str:N", title="Mese",
                sort=["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]),
        y=alt.Y("Stipendio:Q", title="Stipendio (€)", aggregate="mean"),
        color=alt.Color("Anno:N", title="Anno"),
        tooltip=["Anno", "Mese_str", alt.Tooltip("Stipendio:Q", aggregate="mean", format=".2f")]
    ).properties(
        title=""
    )
    return chart

def crea_confronto_anno_su_anno_bollette(data):
    """
    Crea un grafico a linee che confronta la spesa totale media per le bollette
    per ciascun mese, raggruppando i dati per anno.
    """
    df = data.copy()
    # Se non esiste già, calcola il totale delle bollette per ogni record
    if "Totale_Bollette" not in df.columns:
        df["Totale_Bollette"] = df["Elettricità"] + df["Gas"] + df["Acqua"] + df["Internet"] + df["Tari"]
    
    # Aggiunge una colonna per l'anno e una per il mese in forma abbreviata
    df["Anno"] = df["Mese"].dt.year.astype(str)
    df["Mese_str"] = df["Mese"].dt.strftime("%b")
    
    # Crea il grafico a linee: 
    # - asse X: il mese (ordinato correttamente)
    # - asse Y: la spesa totale media per quel mese
    # - colore: l'anno
    chart = alt.Chart(df).mark_line(point=True).encode(
        x=alt.X("Mese_str:N", title="Mese",
                sort=["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]),
        y=alt.Y("Totale_Bollette:Q", title="Spesa Totale (€)"),
        color=alt.Color("Anno:N", title="Anno"),
        tooltip=["Anno", "Mese_str", alt.Tooltip("Totale_Bollette:Q", format=".2f")]
    ).properties(
        title=""
    )
    return chart




#######################################
# SEZIONE: Storico Stipendi e Risparmi
#######################################

st.title("Storico Stipendi e Risparmi")

stipendi_file = "storico_stipendi.json"
data_stipendi = load_data_local(stipendi_file)

col_sx_stip, col_cx_stip_download, col_dx_stip_chart = st.columns([1, 1, 2])
with col_sx_stip:
    # --- Sezione Input (in alto) ---
    st.subheader("Inserisci Dati")
    mesi_anni = pd.date_range(start="2024-03-01", end="2030-12-01", freq="MS").strftime("%B %Y")
    selected_mese = st.selectbox("Seleziona il mese e l'anno", mesi_anni, key="mese_stipendi")
    mese_dt = datetime.strptime(selected_mese, "%B %Y")

    if data_stipendi.empty:
        data_stipendi = pd.DataFrame(columns=["Mese", "Stipendio", "Risparmi", "Messi da parte Totali"])

    record_esistente = data_stipendi[data_stipendi["Mese"] == mese_dt] if not data_stipendi.empty else pd.DataFrame()
    stipendio_val = float(record_esistente["Stipendio"].iloc[0]) if not record_esistente.empty else 0.0
    risparmi_val = float(record_esistente["Risparmi"].iloc[0]) if not record_esistente.empty else 0.0
    # Correggi qui: leggi dalla colonna "Messi da parte Totali"
    messi_da_parte_mese_corrente_val = float(record_esistente["Messi da parte Totali"].iloc[0]) if not record_esistente.empty else 0.0

    col_input1, col_input2 = st.columns(2)
    with col_input1:
        stipendio = st.number_input("Stipendio (€)", min_value=0.0, step=100.0, value=stipendio_val, key="stipendio_input")
        aggiungi_button = st.button("Aggiungi/Modifica Dati", key="aggiorna_stipendi")
    with col_input2:
        risparmi = st.number_input("Risparmi mese prec. (€)", min_value=0.0, step=100.0, value=risparmi_val, key="risparmi_input")
        # Usa un key unico per l’input dei "Messi da parte Totali"
        messi_da_parte_mese_corrente = st.number_input("Messi da parte Totali (Risp. su BNL) (€)", min_value=0.0, step=100.0, value=messi_da_parte_mese_corrente_val, key="messi_da_parte_input")
        elimina_button = st.button(f"Elimina Record per {selected_mese}", key="elimina_stipendi")

    # Quando aggiorni un record esistente, assicurati di usare il valore dell’input corretto:
    if aggiungi_button:
        if stipendio > 0 or risparmi > 0 or messi_da_parte_mese_corrente > 0:
            if not record_esistente.empty:
                data_stipendi.loc[data_stipendi["Mese"] == mese_dt, "Stipendio"] = stipendio
                data_stipendi.loc[data_stipendi["Mese"] == mese_dt, "Risparmi"] = risparmi
                # Usa il valore corretto per "Messi da parte Totali"
                data_stipendi.loc[data_stipendi["Mese"] == mese_dt, "Messi da parte Totali"] = messi_da_parte_mese_corrente
                placeholder = st.empty()
                placeholder.success(f"Record per {selected_mese} aggiornato!")
                time.sleep(3)
                placeholder.empty()
            else:
                nuovo_record = {
                    "Mese": mese_dt,
                    "Stipendio": stipendio,
                    "Risparmi": risparmi,
                    "Messi da parte Totali": messi_da_parte_mese_corrente
                }
                data_stipendi = pd.concat([data_stipendi, pd.DataFrame([nuovo_record])], ignore_index=True)
                placeholder = st.empty()
                placeholder.success(f"Dati per {selected_mese} aggiunti!")
                time.sleep(3)
                placeholder.empty()

            data_stipendi = data_stipendi.sort_values(by="Mese").reset_index(drop=True)
            save_data_local(stipendi_file, data_stipendi)
        else:
            placeholder = st.empty()
            placeholder.error("Inserisci valori validi per stipendio, risparmi o messi da parte!")
            time.sleep(3)
            placeholder.empty()

    if elimina_button:
        if not record_esistente.empty:
            data_stipendi = data_stipendi[data_stipendi["Mese"] != mese_dt]
            save_data_local(stipendi_file, data_stipendi)
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
    # Pulsante di download per i dati stipendi
    download_data_button(data_stipendi, "storico_stipendi.json")
with col_dx_stip_chart:
    st.markdown("### Confronto Anno su Anno degli Stipendi")
    confronto_chart = crea_confronto_anno_su_anno_stipendi(data_stipendi)
    st.altair_chart(confronto_chart, use_container_width=True)


# --- Separatore e Subheader per la visualizzazione ---
st.markdown("---")
st.subheader("Dati Storici Stipendi/Risparmi")

# --- Sezione Visualizzazione (Tabella e Grafico) ---
col_table, col_chart = st.columns([1.3, 3])
with col_table:
    df_stip = data_stipendi.copy()
    if not df_stip.empty:
        df_stip["Mese"] = df_stip["Mese"].dt.strftime("%B %Y")
    st.dataframe(df_stip, use_container_width=True)
    
    # Calcola medie e statistiche
    data_stipendi = calcola_medie(data_stipendi, ["Stipendio", "Risparmi", "Messi da parte Totali"])
    stats_stip = calcola_statistiche(data_stipendi, ["Stipendio", "Risparmi", "Messi da parte Totali"])
    
    col_somme1, col_somme2, col_somme3 = st.columns([1.3, 1, 1])
    with col_somme1:
        st.markdown(f"**Somma Stipendi:** <span style='color:#77DD77;'>{stats_stip['Stipendio']['somma']:,.2f} €</span>", unsafe_allow_html=True)
        st.markdown(f"**Media Stipendi:** <span style='color:rgba(255, 105, 97, 0.2);'>{stats_stip['Stipendio']['media']:,.2f} €</span>", unsafe_allow_html=True)
        if "Media Stipendio NO 13°/PDR" in data_stipendi.columns and not data_stipendi.empty:
            st.markdown(f"**Media Stipendi NO 13°/PDR:** <span style='color:#FFA07A;'>{data_stipendi['Media Stipendio NO 13°/PDR'].iloc[-1]:,.2f} €</span>", unsafe_allow_html=True)
    with col_somme2:
        st.markdown(f"**Somma Risparmi:** <span style='color:rgba(255, 255, 204, 0.2);'>{stats_stip['Risparmi']['somma']:,.2f} €</span>", unsafe_allow_html=True)
        st.markdown(f"**Media Risparmi:** <span style='color:rgba(132, 182, 244, 0.2);'>{stats_stip['Risparmi']['media']:,.2f} €</span>", unsafe_allow_html=True)
    with col_somme3:
        st.markdown(f"**Somma Messi da parte:** <span style='color:#CFCB62;'>{stats_stip['Messi da parte Totali']['somma']:,.2f} €</span>", unsafe_allow_html=True)
        st.markdown(f"**Media Messi da parte:** <span style='color:#2E75B6;'>{stats_stip['Messi da parte Totali']['media']:,.2f} €</span>", unsafe_allow_html=True)

with col_chart:
    st.altair_chart(crea_grafico_stipendi(data_stipendi).properties(height=500, width='container'), use_container_width=True)

st.markdown('<hr style="width: 100%; height:5px;border-width:0;color:gray;background-color:gray">', unsafe_allow_html=True)




############################
# SEZIONE: Storico Bollette
#############################

st.title("Storico Bollette")

bollette_file = "storico_bollette.json"
data_bollette = load_data_local(bollette_file)

col_sx_bol, col_cx_bol_download, col_dx_bol_chart = st.columns([1, 1, 2])

with col_sx_bol:
    # --- Sezione Input per Bollette ---
    with st.container():
        st.subheader("Inserisci Bollette")
        # Menu a tendina per selezionare il mese
        mesi_anni_bol = pd.date_range(start="2024-03-01", end="2030-12-01", freq="MS").strftime("%B %Y")
        selected_mese_bol = st.selectbox("Seleziona il mese e l'anno", mesi_anni_bol, key="mese_bollette")
        mese_dt_bol = datetime.strptime(selected_mese_bol, "%B %Y")
        
        # Carica i dati dal file locale
        if data_bollette.empty:
            data_bollette = pd.DataFrame(columns=["Mese", "Elettricità", "Gas", "Acqua", "Internet", "Tari"])
        
        # Cerca se esiste già un record per il mese selezionato
        record_bol = data_bollette[data_bollette["Mese"] == mese_dt_bol] if not data_bollette.empty else pd.DataFrame()
        elettricita_val = float(record_bol["Elettricità"].iloc[0]) if not record_bol.empty else 0.0
        gas_val = float(record_bol["Gas"].iloc[0]) if not record_bol.empty else 0.0
        acqua_val = float(record_bol["Acqua"].iloc[0]) if not record_bol.empty else 0.0
        internet_val = float(record_bol["Internet"].iloc[0]) if not record_bol.empty else 0.0
        tari_val = float(record_bol["Tari"].iloc[0]) if not record_bol.empty else 0.0
        
        # Disposizione degli input in due colonne per le bollette
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
                    nuovo_record_bol = {
                        "Mese": mese_dt_bol,
                        "Elettricità": elettricita,
                        "Gas": gas,
                        "Acqua": acqua,
                        "Internet": internet,
                        "Tari": tari
                    }
                    data_bollette = pd.concat([data_bollette, pd.DataFrame([nuovo_record_bol])], ignore_index=True)
                    placeholder = st.empty()
                    placeholder.success(f"Bollette per {selected_mese_bol} aggiunte!")
                    time.sleep(3)
                    placeholder.empty()

                data_bollette = data_bollette.sort_values(by="Mese").reset_index(drop=True)
                save_data_local(bollette_file, data_bollette)
            else:
                placeholder = st.empty()
                placeholder.error("Inserisci valori validi per le bollette!")
                time.sleep(3)
                placeholder.empty()


        if elimina_bollette:
            if not record_bol.empty:
                data_bollette = data_bollette[data_bollette["Mese"] != mese_dt_bol]
                save_data_local(bollette_file, data_bollette)
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
    # Pulsante di download per i dati bollette
    download_data_button(data_bollette, "storico_bollette.json")
with col_dx_bol_chart:
    st.markdown("### Confronto Anno su Anno delle Bollette")
    confronto_bollette_chart = crea_confronto_anno_su_anno_bollette(data_bollette)
    st.altair_chart(confronto_bollette_chart, use_container_width=True)

# --- Separatore e Subheader per Visualizzazione Dati ---
st.markdown("---")
st.subheader("Dati Storici Bollette")

# --- Sezione Visualizzazione (Tabella e Grafico) ---
col_bol_table, col_bol_chart = st.columns([1, 3])
with col_bol_table:
    df_bol = data_bollette.copy()
    if not df_bol.empty:
        df_bol["Mese"] = df_bol["Mese"].dt.strftime("%B %Y")
    st.dataframe(df_bol, use_container_width=True)
    
    # Calcola statistiche per le bollette
    stats_bollette = calcola_statistiche(data_bollette, ["Elettricità", "Gas", "Acqua", "Internet", "Tari"])
    
    col_bol_somme1, col_bol_somme2 = st.columns(2)
    with col_bol_somme1:
        st.markdown(f"**Somma Elettricità:** <span style='color:#84B6F4;'>{stats_bollette['Elettricità']['somma']:,.2f} €</span>", unsafe_allow_html=True)
        st.markdown(f"**Somma Gas:** <span style='color:#FF6961;'>{stats_bollette['Gas']['somma']:,.2f} €</span>", unsafe_allow_html=True)
    with col_bol_somme2:
        st.markdown(f"**Somma Acqua:** <span style='color:#96DED1;'>{stats_bollette['Acqua']['somma']:,.2f} €</span>", unsafe_allow_html=True)
        st.markdown(f"**Somma Tari:** <span style='color:#C19A6B;'>{stats_bollette['Tari']['somma']:,.2f} €</span>", unsafe_allow_html=True)
        st.markdown(f"**Somma Internet:** <span style='color:#FFF5A1;'>{stats_bollette['Internet']['somma']:,.2f} €</span>", unsafe_allow_html=True)
    
    # Input per il budget mensile (se necessario per il calcolo del saldo)    
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
    
    # 1. Trasforma le colonne delle bollette in formato long
    data_melted = data_bollette.melt(
        id_vars=["Mese"],
        value_vars=["Elettricità", "Gas", "Acqua", "Internet", "Tari"],
        var_name="Categoria",
        value_name="Valore"
    )
    # 2. Prepara i dati del saldo: usa la colonna "Saldo" e imposta la Categoria a "Saldo"
    data_saldo = data_bollette[["Mese", "Saldo"]].copy()
    data_saldo["Categoria"] = "Saldo"
    # *** Assegna i valori del saldo alla colonna Valore ***
    data_saldo["Valore"] = data_saldo["Saldo"]  # <--- ECCO IL PASSO FONDAMENTALE
    # Se desideri rimuovere la colonna "Saldo", puoi farlo qui:
    data_saldo.drop(columns=["Saldo"], inplace=True)
    # 3. Combina i dati
    data_completa_bollette = pd.concat([data_melted, data_saldo], ignore_index=True)
    # 4. Colonna formattata per l’asse X
    data_completa_bollette["Mese_str"] = data_completa_bollette["Mese"].dt.strftime("%b %Y")
    # 5. Ordine cronologico dei mesi
    ordine = data_completa_bollette.sort_values("Mese")["Mese_str"].unique().tolist()
    
with col_bol_chart:
    st.altair_chart(crea_grafico_bollette_linea_continua(data_completa_bollette, ordine).properties(height=500), use_container_width=True)

    # Calcola il totale delle bollette sommando le somme per ciascuna categoria
    total_bollette = (stats_bollette["Elettricità"]["somma"] +
                    stats_bollette["Gas"]["somma"] +
                    stats_bollette["Acqua"]["somma"] +
                    stats_bollette["Internet"]["somma"] +
                    stats_bollette["Tari"]["somma"])

    # Numero di mesi (assumendo che ogni record rappresenti un mese univoco)
    n_mesi = data_bollette["Mese"].nunique() if data_bollette["Mese"].nunique() > 0 else 1

    # Calcola la media annua
    media_annua = total_bollette / n_mesi

    st.markdown(f"**Media mensile bollette:** <span style='color:#FFA500;'>{media_annua:,.2f} €</span>", unsafe_allow_html=True)

st.markdown('<hr style="width: 100%; height:5px;border-width:0;color:gray;background-color:gray">', unsafe_allow_html=True)
