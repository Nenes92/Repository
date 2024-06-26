# python -m streamlit run C:\Users\longh\Desktop\temp.py

import streamlit as st
st.set_page_config(layout="wide")  # Imposta layout wide per la pagina IMMEDIATAMENTE


# Flag per controllare se la configurazione della pagina è già stata impostata
page_config_set = False

def set_page_config():
    pass # Rimuoviamo il contenuto di questa funzione, non è più necessario

import altair as alt
import pandas as pd


# --- CONFIGURAZIONE ---

# Dizionario delle spese (ristrutturato)
SPESE = {
    "Fisse": {
        "Affitto": 450,
        "Bollette": 100,
        "MoneyFarm - PAC 5": 100,
        "Alleanza - PIP": 100,
        "Macchina": 178.75,
        "Trasporti": 120,
        "Sport": 100,
        "Psicologo": 100,
        "World Food Programme": 40,
        "Beneficienza": 20,
        "Netflix": 9,
        "Disney+": 12,
        "Wind": 10
    },
    "Variabili": {
        "Emergenze": 0.066,
        "Viaggi": 0.066,
        "Da spendere": 0.25,
        "Spese quotidiane": 0  # Inizializzato a zero
    },
    "Revolut": ["Trasporti", "Sport", "Psicologo", "World Food Programme", "Beneficienza", "Netflix", "Disney+", "Wind", "Emergenze", "Viaggi", "Da spendere", "Spese quotidiane"],
    "Altra Carta": ["Affitto", "Bollette", "MoneyFarm - PAC 5", "Alleanza - PIP", "Macchina"],
}

# Dizionario delle altre entrate
ALTRE_ENTRATE = {
    "Alleanza - PIP (Nonna)": 100,
    "Macchina (Mamma)": 100
}

@st.cache_data  # Aggiungiamo il decoratore per il caching
def create_charts(stipendio_scelto, risparmiabili, df_altre_entrate):

    # --- 1. Creazione DataFrame ---

    # DataFrame per Spese Fisse (con accorpamento)
    df_fisse = pd.DataFrame.from_dict(SPESE["Fisse"], orient="index", columns=["Importo"]).reset_index().rename(columns={"index": "Categoria"})
    df_fisse.loc[(df_fisse["Categoria"] == "World Food Programme") | (df_fisse["Categoria"] == "Beneficienza"), "Categoria"] = "Donazioni"
    df_fisse.loc[(df_fisse["Categoria"] == "MoneyFarm - PAC 5") | (df_fisse["Categoria"] == "Alleanza - PIP"), "Categoria"] = "Investimenti"
    df_fisse.loc[(df_fisse["Categoria"] == "Netflix") | (df_fisse["Categoria"] == "Disney+") | (df_fisse["Categoria"] == "Wind"), "Categoria"] = "Abbonamenti"
    df_fisse.loc[(df_fisse["Categoria"] == "Sport") | (df_fisse["Categoria"] == "Psicologo"), "Categoria"] = "Salute"
    df_fisse.loc[(df_fisse["Categoria"] == "Trasporti") | (df_fisse["Categoria"] == "Macchina"), "Categoria"] = "Macchina"
    df_fisse.loc[(df_fisse["Categoria"] == "Bollette") | (df_fisse["Categoria"] == "Affitto"), "Categoria"] = "Casa"
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
        "Affitto": "#CD5C5C",
        "Bollette": "#CD5C5C",
        "MoneyFarm - PAC 5": "#6495ED",
        "Alleanza - PIP": "#6495ED",
        "Macchina": "#D2B48C",
        "Trasporti": "#D2B48C",
        "Sport": "#40E0D0",
        "Psicologo": "#40E0D0",
        "World Food Programme": "#B57EDC",
        "Beneficienza": "#B57EDC",
        "Netflix": "#D2691E",
        "Disney+": "#D2691E",
        "Wind": "#D2691E",
        "Emergenze": "#50C878",
        "Viaggi": "#50C878",
        "Da spendere": "#FFFF99",
        "Spese quotidiane": "#FFFF99",
        "Alleanza - PIP (Nonna)": "#6495ED",
        "Macchina (Mamma)": "#D2B48C",
        "Stipendio Totale": "#77DD77",
        "Stipendio Scelto": "#77DD77",
        "Altre Entrate": "#77DD77",
        "Spese Fisse": "#FF6961",
        "Spese Variabili": "#77DD77",
        "Risparmi": "#FFFF99",
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
        stipendio_originale = st.number_input("Inserisci il tuo stipendio mensile:", min_value=0)
    with col2:
        stipendio_scelto = st.number_input("Inserisci il tuo stipendio mensile che scegli di usare:", min_value=0)
    with col3:
        st.markdown("<br>", unsafe_allow_html=True)  # Add space above the table
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
            '<div style="height: 0px;"></div>',  # Imposta l'altezza desiderata in pixel
            unsafe_allow_html=True,
        )





    # Calcolo entrate e spese (Ottimizzato)
    stipendio = stipendio_scelto + sum(ALTRE_ENTRATE.values())
    spese_fisse_totali = sum(SPESE["Fisse"].values())
    risparmiabili = stipendio - spese_fisse_totali

    # Calcolo spese variabili (Ottimizzato con list comprehension)
    percentuali_variabili = {"Emergenze": 0.066, "Viaggi": 0.066}
    for voce, percentuale in percentuali_variabili.items():
        SPESE["Variabili"][voce] = percentuale * risparmiabili

    da_spendere_senza_limite = 0.25 * (risparmiabili - sum(percentuali_variabili.values()) * risparmiabili)
    SPESE["Variabili"]["Da spendere"] = min(da_spendere_senza_limite, 120)

    spese_quotidiane_senza_limite = risparmiabili - sum(percentuali_variabili.values()) * risparmiabili - da_spendere_senza_limite
    SPESE["Variabili"]["Spese quotidiane"] = min(spese_quotidiane_senza_limite, 500)
    
    # Calcolo risparmi mensili considerando il limite delle spese quotidiane
    risparmi_mensili = stipendio_originale - stipendio_scelto
    da_spendere = SPESE["Variabili"]["Da spendere"]
    spese_quotidiane = SPESE["Variabili"]["Spese quotidiane"]

    if spese_quotidiane_senza_limite > 500:
        eccesso_spese_quotidiane = spese_quotidiane_senza_limite - 500
        risparmi_mensili += eccesso_spese_quotidiane
    if da_spendere_senza_limite > 120:
        eccesso_da_spendere = da_spendere_senza_limite - 120
        risparmi_mensili += eccesso_da_spendere

    # Calcolo risparmi individuali
    risparmio_stipendi = stipendio_originale - stipendio_scelto
    risparmio_da_spendere = da_spendere_senza_limite - da_spendere if da_spendere_senza_limite > 120 else 0
    risparmio_spese_quotidiane = spese_quotidiane_senza_limite - spese_quotidiane if spese_quotidiane_senza_limite > 500 else 0

    # DataFrame per Altre Entrate
    df_altre_entrate = pd.DataFrame.from_dict(ALTRE_ENTRATE, orient="index", columns=["Importo"]).reset_index().rename(columns={"index": "Categoria"})

    # Creazione dinamica dei grafici (passa risparmiabili e df_altre_entrate)
    with st.spinner("Creazione dei grafici..."):
        chart_fisse, chart_variabili, chart_altre_entrate, df_fisse, df_variabili, df_altre_entrate, color_map = create_charts(stipendio, risparmiabili, df_altre_entrate)

        # --- 2. Creazione DataFrame dei Totali --- (SPOSTATO DOPO IL CALCOLO DEI RISPARMI)
        totali = [df_fisse["Importo"].sum(), df_variabili["Importo"].sum(), df_altre_entrate["Importo"].sum(), stipendio_scelto, risparmi_mensili]  # Aggiungi risparmi_mensili
        categorie = ["Spese Fisse", "Spese Variabili", "Altre Entrate", "Stipendio Scelto", "Risparmi"]  # Aggiungi "Risparmi"
        df_totali = pd.DataFrame({"Totale": totali, "Categoria": categorie})
        
        # --- Creazione Grafico a Barre --- (SPOSTATO FUORI DA create_charts)
        ordine_categorie = ["Stipendio Scelto", "Altre Entrate", "Spese Fisse", "Spese Variabili", "Risparmi"]

        # Converti la Series "Categoria" in una lista di stringhe
        categorie = df_totali["Categoria"].astype(str).tolist()

        # Usa la lista "categorie" nella lambda function per l'ordinamento
        df_totali_sorted = df_totali.sort_values(
            by="Categoria",
            key=lambda x: [ordine_categorie.index(c) for c in x]
        )

        chart_totali = alt.Chart(df_totali_sorted, title='Confronto Totali per Categoria').mark_bar().encode(
            x=alt.X('Categoria:N', sort=list(df_totali_sorted['Categoria'])), # Ordina in base alle categorie effettivamente presenti nel DataFrame
            y=alt.Y('Totale:Q'),
            color=alt.Color(field="Categoria", type="nominal", scale=alt.Scale(domain=list(color_map.keys()), range=list(color_map.values())), legend=None),
            tooltip = ['Categoria', 'Totale']
        ).interactive()



    
    # Creazione di df_fisse_percentuali
    df_fisse_percentuali = df_fisse.rename(columns={'Importo': 'Valore €'})
    # Formattiamo la colonna 'Valore €' una sola volta, prima di rinominarla
    df_fisse['Valore €'] = df_fisse['Importo'].apply(lambda x: f"€ {x:.2f}")  





    # --- COLONNA 1: SPESE FISSE (con grafico e tabella) ---
    with col1:
        st.markdown("---")
        st.subheader("Spese Fisse:")
        for voce, importo in SPESE["Fisse"].items():
            if voce in ["Affitto", "Bollette"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#F08080"), unsafe_allow_html=True)
            elif voce in ["Beneficienza", "World Food Programme"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#D8BFD8"), unsafe_allow_html=True)
            elif voce in ["Wind", "Disney+", "Netflix"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#CC7722"), unsafe_allow_html=True)
            elif voce in ["Sport", "Psicologo"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#80E6E6"), unsafe_allow_html=True)
            elif voce in ["MoneyFarm - PAC 5", "Alleanza - PIP"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#89CFF0"), unsafe_allow_html=True)
            elif voce in ["Macchina"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#E6C48C"), unsafe_allow_html=True)
                st.markdown('<hr style="width:50%; margin-left:0;">', unsafe_allow_html=True)
            elif voce in ["Trasporti"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#E6C48C"), unsafe_allow_html=True)
            else:
                st.write(f"- {voce}: €{importo:.2f}")

        st.markdown("---")
        st.markdown(f'**Totale Spese Fisse:** <span style="color:#F08080;">€{spese_fisse_totali:.2f}</span><span style="color:#808080; float:right;"> - Risparmiabili: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp; Stipendio da Utilizzare - Spese fisse = €{risparmiabili:.2f}</span>', unsafe_allow_html=True)





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
            if voce in ["Emergenze", "Viaggi"]:
                percentuale_emergenze = percentuali_variabili.get("Emergenze", 0) * 100
                st.markdown(color_text(f"- {voce}: €{importo:.2f}&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;", "#66D1A3") + f'<span style="margin-right: 20px; color:#808080;">- {percentuale_emergenze:.2f}% dei Risparmiabili</span>', unsafe_allow_html=True)
            elif voce in ["Spese quotidiane"]:
                percentuale_da_spendere = SPESE["Variabili"]["Da spendere"] / risparmiabili * 100
                st.markdown(color_text(f"- {voce}: €{importo:.2f}&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;", "#F0E68C") + f'<span style="margin-right: 20px; color:#808080;">- il rimanente &nbsp;&nbsp;(con un limite a 500€)</span>', unsafe_allow_html=True)
            elif voce in ["Da spendere"]:
                spese_emergenze_viaggi = SPESE["Variabili"]["Emergenze"] + SPESE["Variabili"]["Viaggi"]
                risparmiabili_dopo_emergenze_viaggi = risparmiabili - spese_emergenze_viaggi
                st.markdown(color_text(f"- {voce}: €{importo:.2f}&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;", "#F0E68C") + f'<span style="margin-right: 20px; color:#808080;">- {da_spendere_senza_limite*100/risparmiabili_dopo_emergenze_viaggi:.2f}% &nbsp;&nbsp; del rimanente €{risparmiabili_dopo_emergenze_viaggi:.2f} &nbsp;&nbsp; (con un limite a 120€)</span>', unsafe_allow_html=True)
            else:
                st.write(f"- {voce}: €{importo:.2f}")
            if voce == "Da spendere":  # Aggiunta per visualizzare da_spendere_senza_limite
                da_spendere = min(da_spendere_senza_limite, 120)  # Calcolo di da_spendere
                risparmio_da_spendere = da_spendere_senza_limite - da_spendere  # Calcolo dei risparmi (spostato qui)
                st.markdown(color_text(f'<small>- {voce} (reali): €{da_spendere_senza_limite:.2f} -> Risparmiati: €{risparmio_da_spendere:.2f}</small>', "#808080"), unsafe_allow_html=True)
            if voce == "Spese quotidiane":  # Aggiunta per visualizzare spese_quotidiane_senza_limite
                spese_quotidiane = min(spese_quotidiane_senza_limite, 500)  # Calcolo di spese_quotidiane
                risparmio_spese_quotidiane = spese_quotidiane_senza_limite - spese_quotidiane  # Calcolo dei risparmi (spostato qui)
                st.markdown(color_text(f'<small>- {voce} (reali): €{spese_quotidiane_senza_limite:.2f} -> Risparmiati: €{risparmio_spese_quotidiane:.2f}</small>', "#808080"), unsafe_allow_html=True)
        

        st.markdown("---")
        st.markdown(f'**Totale Spese Variabili utilizzate:** <span style="color:#77DD77;">€{spese_variabili_totali:.2f}</span>', unsafe_allow_html=True)




        # Spazio vuoto personalizzabile
        st.markdown(
            '<div style="height: 150px;"></div>',  # Imposta l'altezza desiderata in pixel
            unsafe_allow_html=True,
        )




        # --- CALCOLO E VISUALIZZAZIONE RISPARMIATI DEL MESE ---
        _, right_col = st.columns([1, 2]) # Crea due colonne, il titolo va nella seconda
        with right_col:
            st.markdown('<hr style="width: 100%; height:4px;border-width:0;color:gray;background-color:gray">', unsafe_allow_html=True)
            st.subheader("Risparmiati del mese:")

            # Calcolo risparmi mensili considerando il limite delle spese quotidiane
            risparmi_mensili = stipendio_originale - stipendio_scelto
            
            # Calcolo spese variabili (Ottimizzato con list comprehension)
            percentuali_variabili = {"Emergenze": 0.066, "Viaggi": 0.066}
            for voce, percentuale in percentuali_variabili.items():
                SPESE["Variabili"][voce] = percentuale * risparmiabili

            da_spendere_senza_limite = 0.25 * (risparmiabili - sum(percentuali_variabili.values()) * risparmiabili)
            SPESE["Variabili"]["Da spendere"] = min(da_spendere_senza_limite, 120)

            spese_quotidiane_senza_limite = risparmiabili - sum(percentuali_variabili.values()) * risparmiabili - da_spendere_senza_limite
            SPESE["Variabili"]["Spese quotidiane"] = min(spese_quotidiane_senza_limite, 500)
            
            if spese_quotidiane_senza_limite > 500:
                eccesso_spese_quotidiane = spese_quotidiane_senza_limite - 500
                risparmi_mensili += eccesso_spese_quotidiane
            if da_spendere_senza_limite > 120:
                eccesso_da_spendere = da_spendere_senza_limite - 120
                risparmi_mensili += eccesso_da_spendere

            # Calcolo risparmi individuali
            risparmio_stipendi = stipendio_originale - stipendio_scelto
            risparmio_da_spendere = da_spendere_senza_limite - da_spendere if da_spendere_senza_limite > 120 else 0
            risparmio_spese_quotidiane = spese_quotidiane_senza_limite - spese_quotidiane if spese_quotidiane_senza_limite > 500 else 0

            # Visualizzazione con formattazione
            st.markdown(
                f'**Totale Risparmiato:**</span> + <span style="color:#808080;">€{risparmio_stipendi:.2f}</span> + <span style="color:#F0E68C;">€{risparmio_da_spendere:.2f}</span> + <span style="color:#F0E68C;">€{risparmio_spese_quotidiane:.2f}</span> = <span style="color:#77DD77;">€{risparmi_mensili:.2f}</span>',
                unsafe_allow_html=True
            )
            st.markdown(
                f'<div style="text-align:center;"><small style="color:#808080;">Risparmi da Stipendi</small> + <small style="color:#FFFF99;">Risparmi Da Spendere</small></div>',
                unsafe_allow_html=True,
            )









# --- COLONNA 3: ALTRE ENTRATE ---
    with col3:
        st.markdown("---")
        st.subheader("Altre Entrate:")
        for voce, importo in ALTRE_ENTRATE.items():
            if voce in ["Macchina (Mamma)"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#E6C48C"), unsafe_allow_html=True)
            elif voce in ["Alleanza - PIP (Nonna)"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#89CFF0"), unsafe_allow_html=True)
            else:
                st.write(f"- {voce}: €{importo:.2f}")

        st.markdown("---")
        st.markdown(f'**Totale Altre Entrate:** <span style="color:#77DD77;">€{sum(ALTRE_ENTRATE.values()):.2f}</span>', unsafe_allow_html=True)


        # Spazio vuoto personalizzabile
        st.markdown(
            '<div style="height: 315px;"></div>',  # Imposta l'altezza desiderata in pixel
            unsafe_allow_html=True,
        )


        # --- CALCOLO E VISUALIZZAZIONE TRASFERIMENTI E SPESE --- (Ottimizzato con dict comprehension)
        st.markdown('<hr style="width: 75%; height:5px;border-width:0;color:gray;background-color:gray">', unsafe_allow_html=True)
        st.subheader("Trasferimenti sulle Carte:")  # Aggiunta del sottotitolo

        for carta in ["Altra Carta", "Revolut"]:
            spese_carta = {voce: SPESE["Fisse"].get(voce, 0) + SPESE["Variabili"].get(voce, 0) 
                           for voce in SPESE[carta]}
            spese_carta = {voce: importo for voce, importo in spese_carta.items() if importo != 0}
            totale_carta = sum(spese_carta.values())
            colore = "#F08080" if carta == "Altra Carta" else "#89CFF0"
            st.markdown(f'**Totale da trasferire su {carta}:** <span style="color:{colore}">€{totale_carta:.2f}</span>', unsafe_allow_html=True)



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
                        .set_properties(**{'text-align': 'center'})
                    )
                    st.dataframe(styled_df_fisse)  # Visualizza il DataFrame stilizzato
                    st.markdown('<small style="color:#808080;">Percentuali sullo Sipendio Scelto</small>', unsafe_allow_html=True)


           

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
                        .set_properties(**{'text-align': 'center'})
                    )
                    st.dataframe(styled_df_variabili)  # Visualizza il DataFrame stilizzato
                    st.markdown('<small style="color:#808080;">Percentuali sui Risparmiabili</small>', unsafe_allow_html=True)

            
        with st.container():
            col1, col2 = st.columns(2)
            
            # --- Spese Variabili ---
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
                        .set_properties(**{'text-align': 'center'}) # Centra il testo nelle celle
                    )
                    st.dataframe(styled_df_altre_entrate)  # Visualizza il DataFrame stilizzato
                    st.markdown('<small style="color:#808080;">Percentuali sullo Sipendio Scelto</small>', unsafe_allow_html=True)



            # --- Grafico Categorie ---
            with col2:
                st.altair_chart(chart_totali, use_container_width=True)











if __name__ == "__main__":
    main()
