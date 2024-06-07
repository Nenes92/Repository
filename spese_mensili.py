import streamlit as st
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


# --- FUNZIONI ---
def color_text(text, color):
    return f'<span style="color:{color}">{text}</span>'


def main():
    st.title("Calcolatore di Spese Personali")

    # Input stipendio
    col1, col2 = st.columns(2)  # Crea due colonne

    with col1:
        stipendio_base = st.number_input("Inserisci il tuo stipendio mensile:", min_value=0)
    with col2:
        stipendio_reale = st.number_input("Inserisci il tuo stipendio mensile che vuoi usare:", min_value=0)


    # Calcolo entrate totali (stipendio + altre entrate)
    stipendio = stipendio_reale + sum(ALTRE_ENTRATE.values())

    # Calcolo spese fisse totali
    spese_fisse_totali = sum(SPESE["Fisse"].values())

    # Calcolo risparmiabili
    risparmiabili = stipendio - spese_fisse_totali

    # Calcola tutte le spese variabili PRIMA di aggiornare il dizionario
    emergenze = 0.066 * risparmiabili
    viaggi = 0.066 * risparmiabili
    
    # Calcola da_spendere SENZA limite
    da_spendere_senza_limite = 0.25 * (risparmiabili - emergenze - viaggi)
    # Calcola da_spendere CON limite
    da_spendere = min(da_spendere_senza_limite, 120)

    # Calcola spese_quotidiane SENZA limite
    spese_quotidiane_senza_limite = risparmiabili - emergenze - viaggi - da_spendere
    # Calcola spese_quotidiane CON limite
    spese_quotidiane = min(spese_quotidiane_senza_limite, 475)

    # Aggiorna il dizionario con le spese variabili calcolate DOPO aver impostato il limite
    SPESE["Variabili"]["Emergenze"] = emergenze
    SPESE["Variabili"]["Viaggi"] = viaggi
    SPESE["Variabili"]["Da spendere"] = da_spendere
    SPESE["Variabili"]["Spese quotidiane"] = spese_quotidiane

    # --- VISUALIZZAZIONE ---

    col1, col2, col3 = st.columns(3)  # Tre colonne 





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




# --- CALCOLO E VISUALIZZAZIONE TRASFERIMENTI ALTRA CARTA ---
        st.markdown('<hr style="height:4px;border-width:0;color:gray;background-color:gray">', unsafe_allow_html=True)
        st.subheader("Altra Carta:")
        altra_carta = {voce: SPESE["Fisse"].get(voce, 0) + SPESE["Variabili"].get(voce, 0) for voce in SPESE["Altra Carta"]}
        altra_carta = {voce: importo for voce, importo in altra_carta.items() if importo != 0}
        altra_carta_totali = sum(altra_carta.values())
        st.markdown(f'**Totale da trasferire su altra carta:** <span style="color:lightcoral;">€{altra_carta_totali:.2f}</span>', unsafe_allow_html=True)


       

# --- CALCOLO E VISUALIZZAZIONE SPESE REVOLUT ---
        st.markdown('<hr style="height:4px;border-width:0;color:gray;background-color:gray">', unsafe_allow_html=True)
        st.subheader("Revolut:")
        spese_revolut = {voce: SPESE["Fisse"].get(voce, 0) + SPESE["Variabili"].get(voce, 0) for voce in SPESE["Revolut"]}
        spese_revolut = {voce: importo for voce, importo in spese_revolut.items() if importo != 0}
        spese_revolut_totali = sum(spese_revolut.values())
        st.markdown(f'**Totale da trasferire su Revolut:** <span style="color:#89CFF0;">€{spese_revolut_totali:.2f}</span>', unsafe_allow_html=True)









       


# --- GRAFICO A TORTA SPESE REVOLUT ---
    df_revolut = pd.DataFrame(spese_revolut.items(), columns=["category", "amount"])
    base = alt.Chart(df_revolut).encode(
        theta=alt.Theta("amount:Q"),
        color="category:N",
        tooltip=["category:N", "amount:Q"]
    )
    pie = base.mark_arc(outerRadius=120)
    text = base.mark_text(radius=140, fill="white").encode(text="amount:Q")
    chart = pie + text
    st.altair_chart(chart, use_container_width=True)  # Visualizza il grafico in Streamlit









if __name__ == "__main__":
    main()
