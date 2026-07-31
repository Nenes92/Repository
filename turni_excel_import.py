"""Importazione dello storico turni dal prototipo Excel mensile."""

from __future__ import annotations

import calendar
import re
from datetime import datetime
from typing import BinaryIO, Mapping

import pandas as pd

from payroll_engine import is_holiday


TURNI_HEADERS = ["Data", "Turno", "Festivo", "Straordinario minuti", "Sede"]
ITALIAN_MONTHS = {
    "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
    "settembre": 9,
    "ottobre": 10,
    "novembre": 11,
    "dicembre": 12,
}
TURN_CODE_MAP = {
    "M": "Mattina",
    "P": "Pomeriggio",
    "N": "Notte",
    "F": "Ferie",
    "G": "Giornata",
}


def _sheet_month(sheet_name: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*([A-Za-zÀ-ÿ]+)\s+(20\d{2})\s*", str(sheet_name))
    if not match:
        return None
    month = ITALIAN_MONTHS.get(match.group(1).lower())
    return (int(match.group(2)), month) if month else None


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value) != 0.0
    return str(value).strip().lower() in {"1", "true", "vero", "sì", "si", "yes", "x"}


def _overtime_minutes(value: object) -> int:
    try:
        if pd.isna(value) or str(value).strip() == "":
            return 0
        # Nel prototipo Excel la durata è memorizzata in quarti d'ora.
        return int(round(max(0.0, float(value)) * 15.0))
    except (TypeError, ValueError):
        return 0


def extract_turni_from_sheets(sheets: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Estrae i turni dai fogli mensili A:E del prototipo."""
    rows: list[dict[str, object]] = []
    for sheet_name, frame in sheets.items():
        parsed = _sheet_month(sheet_name)
        if parsed is None or frame is None or frame.empty:
            continue
        year, month = parsed
        days_in_month = calendar.monthrange(year, month)[1]
        monthly = frame.iloc[:days_in_month, :5]
        for day_index, (_, source_row) in enumerate(monthly.iterrows(), start=1):
            code = str(source_row.iloc[1] if len(source_row) > 1 else "").strip().upper()
            turno = TURN_CODE_MAP.get(code)
            if not turno:
                continue
            day = datetime(year, month, day_index)
            raw_festive = source_row.iloc[2] if len(source_row) > 2 else ""
            rows.append({
                "Data": day.strftime("%Y-%m-%d"),
                "Turno": turno,
                "Festivo": str(raw_festive).strip() == "F" or is_holiday(day),
                "Straordinario minuti": _overtime_minutes(source_row.iloc[3] if len(source_row) > 3 else 0),
                "Sede": _truthy(source_row.iloc[4] if len(source_row) > 4 else False),
            })
    if not rows:
        return pd.DataFrame(columns=TURNI_HEADERS)
    return pd.DataFrame(rows, columns=TURNI_HEADERS).sort_values("Data").reset_index(drop=True)


def read_turni_excel(file: str | BinaryIO) -> pd.DataFrame:
    """Legge tutti i fogli del file Excel senza modificarlo."""
    book = pd.ExcelFile(file, engine="openpyxl")
    sheets = {
        sheet_name: pd.read_excel(book, sheet_name=sheet_name, usecols="A:E")
        for sheet_name in book.sheet_names
        if _sheet_month(sheet_name) is not None
    }
    return extract_turni_from_sheets(sheets)


def merge_turni_history(existing: pd.DataFrame, imported: pd.DataFrame) -> pd.DataFrame:
    """Unisce lo storico mantenendo la versione Google sulle date duplicate."""
    frames = []
    for frame in (imported, existing):
        normalized = frame.copy() if frame is not None else pd.DataFrame()
        for column in TURNI_HEADERS:
            if column not in normalized.columns:
                normalized[column] = ""
        frames.append(normalized[TURNI_HEADERS])
    merged = pd.concat(frames, ignore_index=True)
    merged["Data"] = pd.to_datetime(merged["Data"], errors="coerce").dt.strftime("%Y-%m-%d")
    merged = merged.dropna(subset=["Data"])
    return (
        merged.drop_duplicates(subset=["Data"], keep="last")
        .sort_values("Data")
        .reset_index(drop=True)
    )
