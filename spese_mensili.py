import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors

# Definizione delle spese (personalizzabili)
spese_fisse = {
    "Affitto": 450,
    "Bollette": 100,
    "Investimenti": 100,
    "Macchina": 200,
    "Trasporti": 120,
    "Sport": 100,
    "Psicologo": 100,
    "Donazioni": 60,
    "Abbonamenti": 31,
}

spese_variabili = {
    "Emergenze": 0.066,  # 6.6% del risparmiabile
    "Viaggi": 0.066,      # 6.6% del risparmiabile
    "Da spendere": 0.25, # 25% del rimanente
}

def calcola_spese(stipendio, spese_fisse, spese_variabili):
    """Calcola le spese e il risparmio in base allo stipendio."""
    totale_spese_fisse = sum(spese_fisse.values())
    totale_risparmiabile = stipendio - totale_spese_fisse

    # Aggiunta della fetta "Risparmiabili"
    spese_variabili["Risparmiabili"] = totale_risparmiabile

    for spesa in ["Emergenze", "Viaggi"]:
        spese_variabili[spesa] = totale_risparmiabile * spese_variabili[spesa]

    rimanente_1 = totale_risparmiabile - sum([spese_variabili[spesa] for spesa in ["Emergenze", "Viaggi"]])
    spese_variabili["Da spendere"] = rimanente_1 * spese_variabili["Da spendere"]
    spese_variabili["Spese Quotidiane"] = rimanente_1 - spese_variabili["Da spendere"]

    spese = {**spese_fisse, **spese_variabili}
    df = pd.DataFrame(spese.items(), columns=["Voce", "Importo"])
    df["Tipo"] = df["Voce"].apply(lambda x: "Spesa Fissa" if x in spese_fisse else "Spesa Variabile")
    df["Importo"] = df["Importo"].apply(lambda x: round(x, 2))

    # Calcolo percentuale PRIMA della rinomina
    df["% Stipendio"] = (df["Importo"] / stipendio * 100).map("{:.1f}%".format)

    # Formattazione della colonna "Importo (€)" DOPO il calcolo
    df = df.rename(columns={"Importo": "Importo (€)"})
    df["Importo (€)"] = df["Importo (€)"].astype(float).map("€ {:.2f}".format)

    return df, totale_spese_fisse





# Stili personalizzati
st.markdown(
    """
    <style>
    .reportview-container {
        background: #f0f2f6; 
    }
    .sidebar .sidebar-content {
        background: #ffffff; 
    }
    </style>
    """,
    unsafe_allow_html=True
)






st.title("Calcolatore di Spese Personali")

# Input stipendio
stipendio = st.number_input("Inserisci il tuo stipendio mensile:", min_value=0, value=2250)

if stipendio > 0:
    risultati_df, totale_spese_fisse = calcola_spese(stipendio, spese_fisse, spese_variabili)

    # Conversione esplicita a numerico (rimuove "€" e formattazione)
    risultati_df["Importo Numerico (€)"] = risultati_df["Importo (€)"].astype(str).str.replace(r'[€\s]', '', regex=True).astype(float)

    # Calcolo percentuale DOPO la conversione a numerico
    risultati_df["% su Stipendio"] = (risultati_df["Importo Numerico (€)"] / stipendio * 100).map("{:.1f}%".format)

    # Sposta "Risparmiabili" alla posizione 9 e rimuovila da df_variabili
    idx_risparmiabili = risultati_df[risultati_df['Voce'] == 'Risparmiabili'].index[0]
    riga_risparmiabili = risultati_df.iloc[idx_risparmiabili]
    risultati_df = pd.concat([risultati_df.iloc[:8], riga_risparmiabili.to_frame().T, risultati_df.iloc[9:]], ignore_index=True)
    risultati_df = risultati_df.drop(idx_risparmiabili)

    st.subheader("Ecco il dettaglio delle tue spese:")
    st.table(risultati_df[["Voce", "Importo (€)", "Tipo", "% su Stipendio"]])

    st.write(f"\nTotale Spese Fisse: € {totale_spese_fisse:.2f}")
    st.write(f"Totale Risparmiabile: € {stipendio - totale_spese_fisse:.2f}")

    # Calcolo per Revolut (attenzione alla conversione a numerico)
    voci_revolut = ["Trasporti", "Sport", "Psicologo", "Donazioni", "Abbonamenti", "Emergenze", "Viaggi", "Da spendere", "Spese Quotidiane"]
    totale_revolut = risultati_df[risultati_df['Voce'].isin(voci_revolut)]["Importo Numerico (€)"].sum()
    st.subheader("Totale da versare su Revolut:")
    st.write(f"€ {totale_revolut:.2f}")



    # Preparazione dati per il grafico a doppia torta
    df_fisse = risultati_df[risultati_df['Tipo'] == 'Spesa Fissa'].copy()  
    df_variabili = risultati_df[risultati_df['Tipo'] == 'Spesa Variabile'].copy()

    # Aggiungi "Risparmiabili" a df_fisse e rimuovila da df_variabili
    if 'Risparmiabili' in risultati_df['Voce'].values:
        idx_risparmiabili = risultati_df[risultati_df['Voce'] == 'Risparmiabili'].index[0]
        riga_risparmiabili = risultati_df.iloc[idx_risparmiabili]
        df_fisse = pd.concat([df_fisse, riga_risparmiabili.to_frame().T], ignore_index=True)
        df_variabili = df_variabili.drop(idx_risparmiabili)  






    # Grafico a doppia torta
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 6), width_ratios=[2, 1]) 

    # Colori sfumati per le spese fisse (marrone/beige)
    cmap_fisse = mcolors.LinearSegmentedColormap.from_list("", ["#D2B48C", "#BC8F8F", "#F4A460", "#DEB887"])  # Marrone chiaro, marrone rossastro, marrone dorato, beige
    colors_fisse = cmap_fisse(df_fisse.index / len(df_fisse))

    # Colora "Risparmiabili" di celeste pastello solo se presente
    if 'Risparmiabili' in df_fisse['Voce'].values:
        idx_risparmiabili = df_fisse[df_fisse['Voce'] == 'Risparmiabili'].index[0]
        colors_fisse[idx_risparmiabili] = mcolors.to_rgba('#B0E2FF')

    # Colori per le spese variabili (tonalità pastello celeste/verdino)
    colors_variabili = ["#9BD8F5", "#87CEEB", "#6EC7E4", "#56BFE0"]  # Azzurro pastello, azzurro pallido, ciano pastello, acquamarina

    # Torta spese fisse
    wedges1, texts1, autotexts1 = ax1.pie(df_fisse['Importo Numerico (€)'], labels=df_fisse['Voce'], autopct="%1.1f%%", startangle=140, colors=colors_fisse, textprops={'color': "#E0CDA9"})
    plt.setp(autotexts1, size=8, weight="bold", color='black')
    ax1.set_title('Spese Fisse', fontsize=14, fontweight='bold', color='white')

    # Torta spese variabili - usa df_variabili_nonzero per escludere le voci con importo zero
    df_variabili_nonzero = df_variabili[df_variabili['Importo Numerico (€)'] > 0]
    wedges2, texts2, autotexts2 = ax2.pie(df_variabili_nonzero['Importo Numerico (€)'], labels=df_variabili_nonzero['Voce'], autopct=lambda pct: f"{pct:.1f}%", startangle=140, colors=colors_variabili[:len(df_variabili_nonzero)], textprops={'color': "#E0CDA9"})
    plt.setp(autotexts2, size=8, weight="bold", color='black')

    # Titolo principale in grassetto
    ax2.set_title('Spese Variabili', fontsize=14, fontweight='bold', color='white', ha='center', y=1.1)  # Sposta il titolo più in alto

    # Sottotitolo in corsivo
    ax2.text(0.5, 1.05, '% su Risparmiabili', fontsize=12, style='italic', color='white', ha='center', va='top', transform=ax2.transAxes)

    # Sfondo trasparente per entrambi i grafici
    fig.patch.set_facecolor('none')
    ax1.patch.set_facecolor('none')
    ax2.patch.set_facecolor('none')

    # Mostra il grafico
    st.pyplot(fig)
