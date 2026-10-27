import ast
from pathlib import Path
import pandas as pd

SOURCE = Path(__file__).parents[1] / "spese_mensili.py"


def helpers():
    tree = ast.parse(SOURCE.read_text())
    names = {"_calibration_month_label", "_mobile_calibration_table",
             "_money_turni", "_signed_money_turni", "_turni_month_label"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    ns = {"pd": pd}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "calibration-display", "exec"), ns)
    return ns


def test_table_month_labels_preserve_mapping_and_use_italian_months():
    ns = helpers()
    frame = pd.DataFrame({
        "Includi": [True, False], "Mese cedolino": ["2026-09", "2026-10"],
        "Netto reale": [2500., 2450.], "Netto stimato": [2490., 2460.],
        "Scarto reale − stima": [10., -10.], "Motivo esclusione": ["", "Anomalia"],
    })
    styled, mapping = ns["_mobile_calibration_table"](frame)
    assert styled.data["Mese cedolino"].tolist() == ["set 26", "ott 26"]
    assert mapping == {"set 26": "2026-09", "ott 26": "2026-10"}
    assert styled.data["Netto reale"].tolist() == [2500, 2450]
    assert styled.data["Includi"].tolist() == [True, False]
    styles = styled._compute().ctx
    assert ("color", "#34d399") in styles[(0, 4)]
    assert ("color", "#f87171") in styles[(1, 4)]
    assert ("font-weight", "700") in styles[(0, 2)]
    assert ("font-weight", "400") in styles[(0, 3)]
