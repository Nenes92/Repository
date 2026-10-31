"""Rule forms must never apply drafts before a successful explicit save."""
import ast
import html
from contextlib import nullcontext
from copy import deepcopy
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest


class State(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


@pytest.mark.parametrize("form", ["save_mobile_holiday_rules", "save_turni_rules_google"])
@pytest.mark.parametrize("submitted,save_ok", [(False, False), (True, False), (True, True)])
def test_rule_drafts_are_isolated_and_only_successful_saves_refresh(form, submitted, save_ok):
    source = Path(__file__).parents[1] / "spese_mensili.py"
    tree = ast.parse(source.read_text())
    defaults = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "DEFAULT_TURNI_RULES" for t in n.targets))
    names = {"_render_mobile_turni_rules", "_apply_turni_rules_from_widgets", "_mobile_save_refresh"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    rules = deepcopy(defaults)
    state = State(turni_rules=rules)
    saves, reruns = [], []
    drafts = {"turni_festivita_quota": 135., "turni_notte_feriale": 55.}
    st = SimpleNamespace(
        session_state=state, markdown=lambda *a, **kw: None,
        expander=lambda *a, **kw: nullcontext(), form=lambda *a, **kw: nullcontext(),
        columns=lambda number: [nullcontext() for _ in range(number)],
        number_input=lambda label, value, key, **kw: drafts.get(key, value),
        checkbox=lambda label, value, **kw: value,
        text_input=lambda label, value, **kw: value,
        form_submit_button=lambda *a, key, **kw: submitted and key == form,
        dataframe=lambda *a, **kw: None, caption=lambda *a, **kw: None,
        warning=lambda *a, **kw: None, error=lambda *a, **kw: None, success=lambda *a, **kw: None,
    )
    class Rerun(BaseException):
        pass
    def rerun(**kwargs):
        assert kwargs == {"scope": "app"}
        reruns.append(True)
        raise Rerun()
    st.rerun = rerun
    def save(candidate):
        saves.append(deepcopy(candidate))
        return save_ok
    ns = dict(st=st, html=html, pd=pd, MOBILE_VIEW=True,
              DEFAULT_PAYROLL_ADJUSTMENT=-63., DEFAULT_PAYROLL_ADJUSTMENT_DESCRIPTION="Rettifica",
              paid_holidays=lambda *a: [], save_turni_rules=save,
              load_payroll_adjustments=lambda: {},
              payroll_adjustment_for_month=lambda *a: {"amount": -63., "description": "Rettifica"},
              _turni_month_label=lambda day: "Ottobre 2026", _money_turni=lambda amount: str(amount),
              _mobile_contract_rules_html=lambda saved: "Rules")
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), ns)
    if submitted and save_ok:
        with pytest.raises(Rerun):
            ns["_render_mobile_turni_rules"](rules, date(2026, 10, 1))
        assert len(reruns) == 1
        assert state.turni_rules == saves[0]
    else:
        ns["_render_mobile_turni_rules"](rules, date(2026, 10, 1))
        assert not reruns
        assert state.turni_rules == defaults
    assert rules == defaults  # Rendering/submitting cannot mutate the original object.
    assert len(saves) == int(submitted)
    if saves:
        if form == "save_mobile_holiday_rules":
            assert saves[0]["festivita_quota_lorda"] == 135.
            assert saves[0]["notte_feriale_pct"] == defaults["notte_feriale_pct"]
        else:
            assert saves[0]["notte_feriale_pct"] == 55.
            assert saves[0]["festivita_quota_lorda"] == defaults["festivita_quota_lorda"]
