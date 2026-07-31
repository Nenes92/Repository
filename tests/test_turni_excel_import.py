import pandas as pd

from turni_excel_import import extract_turni_from_sheets, merge_turni_history


def test_extracts_monthly_turns_sites_and_quarter_hour_overtime():
    source = pd.DataFrame({
        "Data": [None, None, None, None, None],
        "Turno": ["M", "P", "N", "F", "G"],
        "fer/Fest": ["F", "f", "f", "f", "f"],
        "Straord.": [None, 2, 3, 4, None],
        "Sede": [False, True, False, False, True],
    })
    result = extract_turni_from_sheets({"Gennaio 2025": source})
    assert result["Turno"].tolist() == ["Mattina", "Pomeriggio", "Notte", "Ferie", "Giornata"]
    assert result["Straordinario minuti"].tolist() == [0, 30, 45, 60, 0]
    assert result["Sede"].tolist() == [False, True, False, False, True]
    assert result.iloc[0]["Festivo"]


def test_merge_preserves_existing_google_rows_on_duplicate_dates():
    imported = pd.DataFrame([{
        "Data": "2025-01-02", "Turno": "Mattina", "Festivo": False,
        "Straordinario minuti": 0, "Sede": False,
    }])
    existing = pd.DataFrame([{
        "Data": "2025-01-02", "Turno": "Notte", "Festivo": False,
        "Straordinario minuti": 30, "Sede": True,
    }])
    merged = merge_turni_history(existing, imported)
    assert len(merged) == 1
    assert merged.iloc[0]["Turno"] == "Notte"
    assert merged.iloc[0]["Straordinario minuti"] == 30
