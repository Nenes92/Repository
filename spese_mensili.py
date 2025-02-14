

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
        json_content = json.dumps(data_dict, indent=4)
        
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
            st.experimental_rerun()
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
                st.experimental_rerun()
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
    st.session_state.data.melt(id_vars=["Mese"], value_vars=["Stipendio", "Risparmi"], var_name="Categoria", value_name="Valore"),
    st.session_state.data.melt(id_vars=["Mese"], value_vars=["Media Stipendio", "Media Risparmi", "Media Stipendio NO 13°/PDR"], var_name="Categoria", value_name="Valore")
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
    # Usa un key univoco per il selectbox
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
        file_content = request.execute()
        data = json.loads(file_content)
        data = pd.DataFrame(data)
        
        # Converti la colonna 'Mese' in datetime
        data['Mese'] = pd.to_datetime(data['Mese'], errors='coerce')
        data = data.sort_values(by="Mese").reset_index(drop=True)
        return data
    except Exception as e:
        st.error(f"Errore nel caricamento del file: {e}")
        return pd.DataFrame()

def save_data(data, file_id, drive_service):
    try:
        # Crea una copia dei dati per non modificare l'originale
        data_copy = data.copy()
        # Se esiste la colonna 'Mese', convertila in stringa (es. "2024-03-01")
        if 'Mese' in data_copy.columns:
            data_copy['Mese'] = data_copy['Mese'].dt.strftime('%Y-%m-%d')
        
        data_dict = data_copy.to_dict(orient="records")
        json_content = json.dumps(data_dict, indent=4)
        
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






st.title("Storico Bollette")

# Seleziona o crea il file su Google Drive
file_id, file_name = select_or_create_file()

if file_id:
    # Carica i dati e salvali nello stato della sessione
    drive_service = authenticate_drive()
    data = load_data(file_id, drive_service)
    st.session_state.data = data

    # --- INTERFACCIA DI MODIFICA/INSERIMENTO ---
    st.write("### Inserisci Bollette")
    # Crea l'elenco dei mesi/anni (in formato 'Mese Anno')
    mesi_anni = pd.date_range(start="2024-03-01", end="2030-12-01", freq="MS").strftime("%B %Y")
    selected_mese_anno = st.selectbox("Seleziona il mese e l'anno", mesi_anni, key="mese_anno_bollette")
    
    mese_datetime = datetime.strptime(selected_mese_anno, "%B %Y")
    
    # Se la colonna "Mese" esiste, cerca un record esistente
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
                st.experimental_rerun()
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
                    new_row = {"Mese": mese_datetime, "Elettricità": elettricita, "Gas": gas,
                               "Acqua": acqua, "Internet": internet, "Tari": tari}
                    data = pd.concat([data, pd.DataFrame([new_row])], ignore_index=True)
                    st.success(f"Bollette per {selected_mese_anno} aggiunte!")
                data = data.sort_values(by="Mese").reset_index(drop=True)
                save_data(data, file_id, authenticate_drive())
                st.experimental_rerun()
            else:
                st.error("Inserisci valori validi per le bollette!")
    
    # --- CALCOLO SALDO (con incremento mensile) ---
    def calcola_saldo(data, decisione_budget_bollette_mensili):
        saldo_iniziale = -150  # Saldo iniziale (puoi modificare questo valore)
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
        # Escludi "Saldo" dalla parte a barre
        categorie_bar = [c for c in categorie if c != "Saldo"]
        barre = alt.Chart(data.query(f"Categoria in {categorie_bar}")).mark_bar(opacity=0.8, size=70).encode(
            x=alt.X("Mese:T", title="Mese", axis=alt.Axis(tickCount="month")),
            y=alt.Y("Valore:Q", title="Valore (€)"),
            color=alt.Color(
                "Categoria:N",
                scale=alt.Scale(domain=dominio, range=colori),
                legend=alt.Legend(title="Categorie")
            ),
            tooltip=["Mese:T", "Categoria:N", "Valore:Q"]
        )
        linea_saldo = alt.Chart(data.query("Categoria == 'Saldo'")).mark_line(
            color="#FF6961",
            strokeDash=[5, 5],
            strokeWidth=3
        ).encode(
            x="Mese:T",
            y="Valore:Q",
            tooltip=["Mese:T", "Valore:Q"]
        ) + alt.Chart(data.query("Categoria == 'Saldo'")).mark_point(
            shape="diamond",
            color="#FF6961",
            size=80
        ).encode(
            x="Mese:T",
            y="Valore:Q",
            tooltip=["Mese:T", "Valore:Q"]
        )
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
