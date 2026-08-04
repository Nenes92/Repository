"""Entrypoint definitivo per Spese mensili versione 2 su Streamlit Cloud."""

from pathlib import Path
import runpy


runpy.run_path(
    str(Path(__file__).with_name("spese_mensili.py")),
    run_name="__main__",
)
