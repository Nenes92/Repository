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



# --- 1. Creazione DataFrame ---

# DataFrame per Spese Fisse
df_fisse = pd.DataFrame.from_dict(SPESE["Fisse"], orient="index", columns=["Importo"]).reset_index().rename(columns={"index": "Categoria"})

# DataFrame per Spese Variabili
df_variabili = pd.DataFrame.from_dict(SPESE["Variabili"], orient="index", columns=["Importo"]).reset_index().rename(columns={"index": "Categoria"})

# DataFrame per Altre Entrate
df_altre_entrate = pd.DataFrame.from_dict(ALTRE_ENTRATE, orient="index", columns=["Importo"]).reset_index().rename(columns={"index": "Categoria"})

# --- 2. Creazione DataFrame dei Totali ---

totali = [df_fisse["Importo"].sum(), df_variabili["Importo"].sum(), df_altre_entrate["Importo"].sum()]
categorie = ["Spese Fisse", "Spese Variabili", "Altre Entrate"]
df_totali = pd.DataFrame({"Totale": totali, "Categoria": categorie})

# --- 3. Creazione Grafici ---

# Grafico a torta per Spese Fisse
chart_fisse = alt.Chart(df_fisse, title='Distribuzione Spese Fisse').mark_arc().encode(
    theta=alt.Theta(field="Importo", type="quantitative"),
    color=alt.Color(field="Categoria", type="nominal", legend=None),
    tooltip=["Categoria", "Importo"]
).interactive()

chart_fisse.save('distribuzione_spese_fisse_pie_chart.json')

# Grafico a torta per Spese Variabili
chart_variabili = alt.Chart(df_variabili, title='Distribuzione Spese Variabili').mark_arc().encode(
    theta=alt.Theta(field="Importo", type="quantitative"),
    color=alt.Color(field="Categoria", type="nominal", legend=None),
    tooltip=["Categoria", "Importo"]
).interactive()

chart_variabili.save('distribuzione_spese_variabili_pie_chart.json')

# Grafico a torta per Altre Entrate
chart_altre_entrate = alt.Chart(df_altre_entrate, title='Distribuzione Altre Entrate').mark_arc().encode(
    theta=alt.Theta(field="Importo", type="quantitative"),
    color=alt.Color(field="Categoria", type="nominal", legend=None),
    tooltip=["Categoria", "Importo"]
).interactive()

chart_altre_entrate.save('distribuzione_altre_entrate_pie_chart.json')

# Grafico a barre per Confronto Totali
chart_totali = alt.Chart(df_totali, title='Confronto Totali per Categoria').mark_bar().encode(
    x=alt.X('Categoria:N', axis=alt.Axis(labelAngle=-45)),
    y=alt.Y('Totale:Q'),
    color=alt.Color('Categoria:N', legend=None),
    tooltip = ['Categoria', 'Totale']
).interactive()

chart_totali.save('confronto_totali_per_categoria_bar_chart.json')




# --- FUNZIONI ---
def color_text(text, color):  # (Nessuna modifica)
    return f'<span style="color:{color}">{text}</span>'


def main():
    set_page_config() # Chiama la funzione per impostare la configurazione della pagina

    st.title("Calcolatore di Spese Personali")

    # Input stipendio
    col1, col2 = st.columns(2)  # Crea due colonne

    with col1:
        stipendio_base = st.number_input("Inserisci il tuo stipendio mensile:", min_value=0)
    with col2:
        stipendio_reale = st.number_input("Inserisci il tuo stipendio mensile che vuoi usare:", min_value=0)


    # Calcolo entrate e spese (Ottimizzato)
    stipendio = stipendio_reale + sum(ALTRE_ENTRATE.values())
    spese_fisse_totali = sum(SPESE["Fisse"].values())
    risparmiabili = stipendio - spese_fisse_totali

    # Calcolo spese variabili (Ottimizzato con list comprehension)
    percentuali_variabili = {"Emergenze": 0.066, "Viaggi": 0.066}
    for voce, percentuale in percentuali_variabili.items():
        SPESE["Variabili"][voce] = percentuale * risparmiabili

    da_spendere_senza_limite = 0.25 * (risparmiabili - sum(percentuali_variabili.values()) * risparmiabili)
    SPESE["Variabili"]["Da spendere"] = min(da_spendere_senza_limite, 120)

    spese_quotidiane_senza_limite = risparmiabili - sum(SPESE["Variabili"].values())
    SPESE["Variabili"]["Spese quotidiane"] = min(spese_quotidiane_senza_limite, 475)





    # --- VISUALIZZAZIONE ---
    with st.container():  # Container per raggruppare le colonne
        col1, col2, col3 = st.columns([1.5, 1.5, 1])  # Tre colonne con larghezze personalizzate




# --- COLONNA 1: SPESE FISSE ---
    with col1:
        st.subheader("Dettaglio Spese Fisse:")
        for voce, importo in SPESE["Fisse"].items():
            if voce in ["Affitto", "Bollette"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "lightcoral"), unsafe_allow_html=True)
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
                st.markdown('<hr style="width:50%; margin-left:0;">', unsafe_allow_html=True)  # Linea lunga la metà
            elif voce in ["Trasporti"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#E6C48C"), unsafe_allow_html=True)
            else:
                st.write(f"- {voce}: €{importo:.2f}")

        st.markdown("---")
        st.markdown(f'**Totale Spese Fisse:** <span style="color:lightcoral;">€{spese_fisse_totali:.2f}</span>', unsafe_allow_html=True)





# --- COLONNA 2: SPESE VARIABILI E RIMANENTE ---
    with col2:
        st.subheader("Spese Variabili Rimanenti:")

        # Calcola e visualizza spese variabili (semplificato)
        spese_variabili_totali = sum(SPESE["Variabili"].values())
        for voce, importo in SPESE["Variabili"].items():
            if voce in ["Emergenze", "Viaggi"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#66D1A3"), unsafe_allow_html=True)
            elif voce in ["Spese quotidiane", "Da spendere"]:
                st.markdown(color_text(f"- {voce}: €{importo:.2f}", "#F0E68C"), unsafe_allow_html=True)
            else:
                st.write(f"- {voce}: €{importo:.2f}")
            if voce == "Da spendere":  # Aggiunta per visualizzare da_spendere_senza_limite
                st.markdown(color_text(f'<small>- {voce} (reali): €{da_spendere_senza_limite:.2f}</small>', "#808080"), unsafe_allow_html=True)
            if voce == "Spese quotidiane":  # Aggiunta per visualizzare spese_quotidiane_senza_limite
                st.markdown(color_text(f'<small>- {voce} (reali): €{spese_quotidiane_senza_limite:.2f}</small>', "#808080"), unsafe_allow_html=True)
                

        st.markdown("---")
        st.markdown(f'**Totale Spese Variabili:** <span style="color:#77DD77;">€{spese_variabili_totali:.2f}</span>', unsafe_allow_html=True)



        st.markdown("")
        st.markdown("")
        st.markdown("")
        st.markdown("")
        st.markdown("")
        st.markdown("")
        st.markdown("")



# --- CALCOLO E VISUALIZZAZIONE RISPARMIATI DEL MESE ---
        st.markdown('<hr style="height:4px;border-width:0;color:gray;background-color:gray">', unsafe_allow_html=True)
        st.subheader("Risparmiati del mese:")
        # Calcolo risparmi mensili considerando il limite delle spese quotidiane
        risparmi_mensili = stipendio_base - stipendio_reale
        if spese_quotidiane_senza_limite > 475:
            eccesso_spese_quotidiane = spese_quotidiane_senza_limite - 475
            risparmi_mensili += eccesso_spese_quotidiane
        if da_spendere_senza_limite > 120:
            eccesso_da_spendere = da_spendere_senza_limite - 120
            risparmi_mensili += eccesso_da_spendere

        # Visualizzazione con formattazione
        st.markdown(f'**Totale Risparmiato:** <span style="color:#77DD77;">€{risparmi_mensili:.2f}</span>', unsafe_allow_html=True)








# --- COLONNA 3: ALTRE ENTRATE ---
    with col3:
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

        st.markdown("")
        st.markdown("")
        st.markdown("")
        st.markdown("")
        st.markdown("")
        st.markdown("")
        st.markdown("")




        # --- CALCOLO E VISUALIZZAZIONE TRASFERIMENTI E SPESE --- (Ottimizzato con dict comprehension)
        st.markdown('<hr style="height:4px;border-width:0;color:gray;background-color:gray">', unsafe_allow_html=True)
        st.subheader("Trasferimenti sulle Carte:")  # Aggiunta del sottotitolo

        for carta in ["Altra Carta", "Revolut"]:
            spese_carta = {voce: SPESE["Fisse"].get(voce, 0) + SPESE["Variabili"].get(voce, 0) 
                           for voce in SPESE[carta]}
            spese_carta = {voce: importo for voce, importo in spese_carta.items() if importo != 0}
            totale_carta = sum(spese_carta.values())
            colore = "lightcoral" if carta == "Altra Carta" else "#89CFF0"
            st.markdown(f'**Totale da trasferire su {carta}:** <span style="color:{colore}">€{totale_carta:.2f}</span>', unsafe_allow_html=True)


    # --- GRAFICI ---
    st.markdown("---")  # Separatore visivo

    # Grafici a torta per le spese (con container per ogni riga)
    with st.container():
        col1, col2 = st.columns(2)
        with col1:
            st.altair_chart(chart_fisse, use_container_width=True)
        with col2:
            st.altair_chart(chart_variabili, use_container_width=True)

    with st.container():
        col1, col2 = st.columns(2)
        with col1:
            st.altair_chart(chart_altre_entrate, use_container_width=True)
        with col2:
            st.altair_chart(chart_totali, use_container_width=True)















if __name__ == "__main__":
    main()
