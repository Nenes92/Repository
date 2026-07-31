"""Entrypoint isolato per la preview online del Cedolino V2."""

from pathlib import Path
import runpy


runpy.run_path(
    str(Path(__file__).with_name("spese_mensili.py")),
    run_name="__main__",
)
