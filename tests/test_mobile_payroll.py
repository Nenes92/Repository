"""Validate mobile live earnings against the already-net allowance rule."""
import ast
import calendar
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from payroll_engine import estimate_live_net_accrual


def app_functions():
    tree = ast.parse((Path(__file__).parents[1] / "spese_mensili.py").read_text())
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    wanted = {"compute_turno_net_estimate", "_turni_premium_net_parts"}
    pending = list(wanted)
    while pending:
        node = functions[pending.pop()]
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                name = child.func.id
                if name in functions and name not in wanted and name != "_now_italy":
                    wanted.add(name)
                    pending.append(name)
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            try:
                ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
            nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in wanted:
            node.decorator_list = []
            nodes.append(node)
    env = dict(pd=pd, datetime=datetime, timedelta=timedelta, calendar=calendar,
               MOBILE_VIEW=True, estimate_live_net_accrual=estimate_live_net_accrual,
               _now_italy=lambda: datetime(2026, 10, 11, 10))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app-functions", "exec"), env)
    return env


def test_mobile_net_allowance_and_component_totals():
    env = app_functions()
    rules = env["DEFAULT_TURNI_RULES"]
    result = env["compute_turno_net_estimate"](
        "2026-10-11", "Mattina", False, rules, 12,
        until=datetime.max, straordinario_minuti=30,
    )
    assert result["allowance_net"] == rules["ind_m_p_festivo"]
    assert result["total"] == pytest.approx(
        result["base"] + result["premium_net"] + result["allowance_net"] + result["overtime_net"]
    )
    env["MOBILE_VIEW"] = False
    desktop = env["compute_turno_net_estimate"]("2026-10-11", "Mattina", False, rules, 12, until=datetime.max)
    assert desktop["allowance_net"] == pytest.approx(
        rules["ind_m_p_festivo"] * rules["coefficiente_netto_variabili"]
    )


def test_night_percentage_parts_add_up_to_net_premium():
    env = app_functions()
    rules = env["DEFAULT_TURNI_RULES"]
    for day in ["2026-10-10", "2026-10-11"]:
        parts = env["_turni_premium_net_parts"](day, "Notte", False, rules)
        assert set(parts) == {20, 60}
        result = env["compute_turno_net_estimate"](day, "Notte", False, rules, 12, until=datetime.max)
        assert sum(parts.values()) == pytest.approx(result["premium_net"])
