# python -m streamlit run C:\Users\longh\Desktop\temp.py

import altair as alt
import pandas as pd
import streamlit as st
import os
from datetime import datetime

st.set_page_config(layout="wide")  # Imposta layout wide per la pagina IMMEDIATAMENTE


# Flag per controllare se la configurazione della pagina è già stata impostata
page_config_set = False

def set_page_config():
    pass # Rimuoviamo il contenuto di questa funzione, non è più necessario

# /////  
# Variabili inizializzate
input_stipendio_originale=2485
input_risparmi_mese_precedente=0
input_stipendio_scelto=2100

percentuale_limite_da_spendere=0.15
limite_da_spendere=80
max_spese_quotidiane=400

emergenze_compleanni=0.1
viaggi=0.06
# /////  




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
        "Sport": 70,
        "Psicologo": 100,
        "World Food Programme": 30,
        "Beneficienza": 15,
        "Netflix": 9,
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
    "Altro": 0,
    "Macchina (Mamma)": 100,
    "Affitto Garage": 000
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
        "Stipendio Totale": "#77DD77",
        "Stipendio Scelto": "#77DD77",
        "Altre Entrate": "#77DD77",
        "Spese Fisse": "#FF6961",
        "Spese Variabili": "#FFFF99",
        "Risparmi": "#77DD77",
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
        stipendio_originale = st.number_input("Inserisci il tuo stipendio mensile:", min_value=input_stipendio_originale)
        risparmi_mese_precedente = st.number_input("Inserisci quanto hai risparmiato nel mese precedente:", min_value=input_risparmi_mese_precedente)
    with col2:
        # Spazio vuoto personalizzabile
        st.markdown(
            '<div style="height: 40px;"></div>',  # Imposta l'altezza desiderata in pixel
            unsafe_allow_html=True,
        )
        stipendio_scelto = st.number_input("Inserisci il tuo stipendio mensile che scegli di usare:", min_value=input_stipendio_scelto)
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






        # --- 2. Creazione DataFrame dei Totali --- (SPOSTATO DOPO IL CALCOLO DEI RISPARMI)
        totali = [df_fisse["Importo"].sum(), df_variabili["Importo"].sum(), stipendio_originale, risparmi_mensili]  # Aggiungi stipendio_originale
        categorie = ["Spese Fisse", "Spese Variabili", "Stipendio Totale", "Risparmi"]  # Aggiungi "Stipendio Totale"
        df_totali = pd.DataFrame({"Totale": totali, "Categoria": categorie})

        # --- Creazione Grafico a Barre --- (SPOSTATO FUORI DA create_charts)
        ordine_categorie = ["Stipendio Totale", "Spese Fisse", "Spese Variabili", "Risparmi"]

        # DataFrame per la barra impilata
        df_impilato = pd.DataFrame({
            "Categoria": ["Stipendio Totale"],
            "Stipendio Scelto": [stipendio_scelto],
            "Altre Entrate": [sum(ALTRE_ENTRATE.values())]
        })

        # Grafico a barre impilate
        chart_impilato = alt.Chart(df_impilato, title='Confronto Totali per Categoria (Impilato)').mark_bar().encode(
            x=alt.X('Categoria:N', sort=None),
            y=alt.Y('Stipendio Scelto:Q', title='Totale'),
            color=alt.Color(field="Categoria", type="nominal", scale=alt.Scale(domain=list(color_map.keys()), range=list(color_map.values())), legend=None),
            tooltip = ['Categoria', 'Stipendio Scelto']
        ).interactive()

        chart_impilato += alt.Chart(df_impilato).mark_bar(opacity=0.7).encode(
            y=alt.Y('Altre Entrate:Q', title='Totale'),
            color=alt.value(color_map["Altre Entrate"]),
            tooltip = ['Categoria', 'Altre Entrate']
        )

        # Converti la Series "Categoria" in una lista di stringhe
        categorie = df_totali["Categoria"].astype(str).tolist()

        # Usa la lista "categorie" nella lambda function per l'ordinamento
        df_totali_sorted = df_totali.sort_values(
            by="Categoria",
            key=lambda x: [ordine_categorie.index(c) for c in x]
        )

        # Grafico a barre singolo
        chart_totali = alt.Chart(df_totali_sorted, title='Confronto Totali per Categoria').mark_bar().encode(
            x=alt.X('Categoria:N', sort=list(df_totali_sorted['Categoria'])), # Ordina in base alle categorie effettivamente presenti nel DataFrame
            y=alt.Y('Totale:Q'),
            color=alt.Color(field="Categoria", type="nominal", scale=alt.Scale(domain=list(color_map.keys()), range=list(color_map.values())), legend=None),
            tooltip = ['Categoria', 'Totale']
        ).interactive()

        # Combina i due grafici in uno solo
        chart_totali_combinato = chart_totali | chart_impilato







    
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
        st.markdown('<hr style="width: 75%; height:5px;border-width:0;color:gray;background-color:gray">', unsafe_allow_html=True)
        st.subheader("Trasferimenti sulle Carte:")

        for carta in ["ING", "Revolut", "BNL"]:
            spese_carta = {voce: SPESE["Fisse"].get(voce, 0) + SPESE["Variabili"].get(voce, 0) 
                           for voce in SPESE[carta]}
            spese_carta = {voce: importo for voce, importo in spese_carta.items() if importo != 0}
            if carta == "Revolut":
                totale_carta = revolut_expenses  # Usa il valore modificato per Revolut
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
            elif carta == "Revolut":
                colore = "#89CFF0"  # Azzurro
                testo = "trasferire"
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
            col1, col2 = st.columns(2)
            
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
                st.altair_chart(chart_totali, use_container_width=True)











if __name__ == "__main__":
    main()












st.markdown('<hr style="width: 100%; height:5px;border-width:0;color:gray;background-color:gray">', unsafe_allow_html=True)


# Funzione per caricare i dati
def load_data(uploaded_file):
    if uploaded_file is not None:
        try:
            data = pd.read_csv(uploaded_file, parse_dates=["Mese"])
        except Exception as e:
            st.error(f"Errore nel caricamento del file: {e}")
            data = pd.DataFrame(columns=["Mese", "Stipendio", "Risparmi"])
    else:
        data = pd.DataFrame(columns=["Mese", "Stipendio", "Risparmi"])
    return data

# Percorso per salvare il file
save_path = "/Users/emanuelelongheu/Library/CloudStorage/GoogleDrive-longheu.emanuele@gmail.com/Il mio Drive/Documents/Spese e guadagni/storico_stipendi.csv"

# Funzione per salvare i dati
def save_data(data, save_path):
    try:
        data.to_csv(save_path, index=False, date_format='%Y-%m')
        st.success(f"Dati salvati in: {save_path}")
    except Exception as e:
        st.error(f"Errore nel salvataggio del file: {e}")

# Controllo e inizializzazione dei dati in session_state
if "data" not in st.session_state:
    try:
        st.session_state.data = pd.read_csv(save_path, parse_dates=["Mese"])
    except FileNotFoundError:
        st.session_state.data = pd.DataFrame(columns=["Mese", "Stipendio", "Risparmi"])

# Caricamento dati
data = st.session_state.data

# Titolo dell'app
st.title("Storico Stipendi e Risparmi Mensili")




















# Layout a tre colonne principali
col_sinistra, col_centrale, col_destra = st.columns([1, 1, 1])

with col_sinistra:
    st.write("### Inserisci Stipendio e Risparmi")
    mese_stipendio_risparmi = st.selectbox(
        "Mese",
        options=pd.date_range("2024-03-01", "2025-12-31", freq="MS").strftime("%B %Y"),
        key="stipendio_risparmi_select"
    )
    stipendio = st.number_input("Stipendio (€)", min_value=0.0, step=100.0, key="stipendio_input")
    risparmi = st.number_input("Risparmi (€)", min_value=0.0, step=100.0, key="risparmi_input")

    # Colonne per posizionare i due bottoni affiancati
    col1, col2 = st.columns([2, 1])  # La colonna sinistra più larga per "Aggiungi"

    with col1:
        if st.button("Aggiungi/Modifica Stipendio e Risparmi", key="aggiungi_modifica_stipendio_risparmi"):
            mese_datetime = pd.Timestamp(datetime.strptime(mese_stipendio_risparmi, '%B %Y'))
            if stipendio > 0 or risparmi > 0:
                if not data.loc[data["Mese"] == mese_datetime].empty:
                    # Se il mese esiste già, aggiorna
                    data.loc[data["Mese"] == mese_datetime, "Stipendio"] = stipendio
                    data.loc[data["Mese"] == mese_datetime, "Risparmi"] = risparmi
                    st.success(f"Record per {mese_stipendio_risparmi} aggiornato!")
                else:
                    # Se il mese non esiste, aggiungi una nuova riga
                    new_row = {"Mese": mese_datetime, "Stipendio": stipendio, "Risparmi": risparmi}
                    data = pd.concat([data, pd.DataFrame([new_row])], ignore_index=True)
                    st.success(f"Stipendio e Risparmi per {mese_stipendio_risparmi} aggiunti!")
                data = data.sort_values(by="Mese").reset_index(drop=True)
                st.session_state.data = data
                save_data(data, save_path)
                st.experimental_rerun()  # Forza il ridisegno
            else:
                st.error("Inserisci valori validi per stipendio e/o risparmi!")

    with col2:
        # Pulsante per eliminare il record
        if st.button(f"Elimina Record per {mese_stipendio_risparmi}", key="elimina_record", help="Elimina il record per il mese selezionato"):
            mese_datetime = pd.Timestamp(datetime.strptime(mese_stipendio_risparmi, '%B %Y'))
            if not data.loc[data["Mese"] == mese_datetime].empty:
                data = data[data["Mese"] != mese_datetime]
                st.session_state.data = data
                save_data(data, save_path)
                st.success(f"Record per {mese_stipendio_risparmi} eliminato!")
                st.experimental_rerun()  # Forza il ridisegno
            else:
                st.error(f"Il mese {mese_stipendio_risparmi} non è presente nello storico!")

    # Stile per i bottoni
    st.markdown(
        """
        <style>
        .stButton > button {
            background-color: #A7C7E7;  /* Azzurro pastello per 'Aggiungi/Modifica' */
            color: black;
            font-weight: bold;
        }

        .stButton > button:hover {
            background-color: #8FA9C8;
        }

        .stButton:nth-child(2) > button {
            background-color: #FF6347;  /* Rosso Tomate per 'Elimina' */
            color: white;
            font-weight: bold;
        }

        .stButton:nth-child(2) > button:hover {
            background-color: #FF4500;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

with col_centrale:
    # Bottone per caricare il file CSV
    uploaded_file = st.file_uploader("Carica il file CSV", type="csv", key="file_uploader")

    if uploaded_file is not None:
        nuovo_data = load_data(uploaded_file)
        st.session_state.data = pd.concat([st.session_state.data, nuovo_data]).drop_duplicates(subset=["Mese"]).reset_index(drop=True)
        data = st.session_state.data
    else:
        data = st.session_state.data



st.markdown("---")






















# Funzione per calcolare somma e media
@st.cache_data
def calcola_statistiche(data, colonne):
    stats = {col: {'somma': data[col].sum(), 'media': data[col].mean()} for col in colonne}
    
    # Calcola la media degli stipendi escludendo luglio e dicembre
    stipendi_senza_picchi = data[~data["Mese"].dt.month.isin([7, 12])]
    if not stipendi_senza_picchi.empty:
        stats["Stipendio"]["media_senza_picchi"] = stipendi_senza_picchi["Stipendio"].mean()
    else:
        stats["Stipendio"]["media_senza_picchi"] = 0.0

    return stats

# Funzione per calcolare medie mobili
def calcola_medie_mobili(data, colonne):
    for col in colonne:
        data[f"Media {col}"] = data[col].expanding().mean()
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
data = calcola_medie_mobili(data, ["Stipendio", "Risparmi"])

# Prepara i dati per i grafici
data_completa = pd.concat([
    st.session_state.data.melt(id_vars=["Mese"], value_vars=["Stipendio", "Risparmi"], var_name="Categoria", value_name="Valore"),
    st.session_state.data.melt(id_vars=["Mese"], value_vars=["Media Stipendio", "Media Risparmi"], var_name="Categoria", value_name="Valore")
])

# Layout Streamlit
if not data.empty:
    st.write("### Storico Stipendi e Risparmi")
    col_tabella, col_grafico = st.columns([1.2, 3.7])

    # Tabella
    data_display = data.copy()
    data_display["Mese"] = data_display["Mese"].dt.strftime('%B %Y')
    with col_tabella:
        st.dataframe(data_display, use_container_width=True)
        col_left, col_right = st.columns(2)
        with col_left:
            st.write(f"**Somma Stipendio:** <span style='color:#77DD77;'>{statistiche['Stipendio']['somma']:,.2f} €</span>", unsafe_allow_html=True)
            st.write(f"**Media Stipendio:** <span style='color:#FF6961;'>{statistiche['Stipendio']['media']:,.2f} €</span>", unsafe_allow_html=True)
            st.write(f"**Media senza 13°/14°:** <span style='color:#FF6961;'>{statistiche['Stipendio']['media_senza_picchi']:,.2f} €</span>", unsafe_allow_html=True)
        with col_right:
            st.write(f"**Somma Risparmi:** <span style='color:#FFFF99;'>{statistiche['Risparmi']['somma']:,.2f} €</span>", unsafe_allow_html=True)
            st.write(f"**Media Risparmi:** <span style='color:#84B6F4;'>{statistiche['Risparmi']['media']:,.2f} €</span>", unsafe_allow_html=True)

    # Grafico
    dominio_categorie = ["Stipendio", "Risparmi", "Media Stipendio", "Media Risparmi"]
    scala_colori = ["#77DD77", "#FFFF99", "#FF6961", "#84B6F4"]

    grafico_principale = crea_grafico(data_completa, ["Stipendio", "Risparmi"], dominio_categorie, scala_colori)
    grafico_medie = crea_grafico(data_completa, ["Media Stipendio", "Media Risparmi"], dominio_categorie, scala_colori, line_style="dashed")

    with col_grafico:
        st.altair_chart((grafico_principale + grafico_medie).properties(height=500, width='container'), use_container_width=True)

st.markdown('<hr style="width: 100%; height:5px;border-width:0;color:gray;background-color:gray">', unsafe_allow_html=True)
