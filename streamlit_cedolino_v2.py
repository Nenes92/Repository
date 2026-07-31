"""Entrypoint dedicato alla preview Streamlit del Cedolino V2."""

# L'applicazione resta definita in un solo punto. Questo entrypoint permette a
# Streamlit Community Cloud di creare una preview separata senza duplicare la
# dashboard o cambiare il deployment storico basato su spese_mensili.py.
from spese_mensili import *  # noqa: F401,F403
