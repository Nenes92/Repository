    if data_stipendi.empty:
        data_stipendi = pd.DataFrame(columns=["Mese", "Stipendio", "Risparmi", "Messi da parte Totali"])

    record_esistente = data_stipendi[data_stipendi["Mese"] == mese_dt] if not data_stipendi.empty else pd.DataFrame()
    stipendio_val = float(record_esistente["Stipendio"].iloc[0]) if not record_esistente.empty else 0.0
    risparmi_val = float(record_esistente["Risparmi"].iloc[0]) if not record_esistente.empty else 0.0
    messi_da_parte_mese_corrente_val = float(record_esistente["Risparmi"].iloc[0]) if not record_esistente.empty else 0.0

    col_input1, col_input2 = st.columns(2)
    with col_input1:
        stipendio = st.number_input("Stipendio (€)", min_value=0.0, step=100.0, value=stipendio_val, key="stipendio_input")
        aggiungi_button = st.button("Aggiungi/Modifica Dati", key="aggiorna_stipendi")
    with col_input2:
        risparmi = st.number_input("Risparmi (€)", min_value=0.0, step=100.0, value=risparmi_val, key="risparmi_input")
        messi_da_parte_mese_corrente = st.number_input("Messi da parte Totali (€)", min_value=0.0, step=100.0, value=messi_da_parte_mese_corrente_val, key="messi_da_parte_mese_corrente_val_input")
        elimina_button = st.button(f"Elimina Record per {selected_mese}", key="elimina_stipendi")

    # Ora fuori dai blocchi, controlli i pulsanti:
    if aggiungi_button:
        if stipendio > 0 or risparmi > 0 or messi_da_parte_mese_corrente > 0:
            if not record_esistente.empty:
                data_stipendi.loc[data_stipendi["Mese"] == mese_dt, "Stipendio"] = stipendio
                data_stipendi.loc[data_stipendi["Mese"] == mese_dt, "Risparmi"] = risparmi
                data_stipendi.loc[data_stipendi["Mese"] == mese_dt, "Messi da parte Totali"] = risparmi
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
            save_data_local(stipendi_file, data_stipendi)
        else:
            placeholder = st.empty()
            placeholder.error("Inserisci valori validi per stipendio e/o risparmi!")
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
