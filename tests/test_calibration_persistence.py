"""Calibration survives sessions; Drive is only read on an explicit sync."""
import ast
import base64
import json
import zlib
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from payroll_engine import DEFAULT_RULES, calibrate, migrate_rules

SOURCE = Path(__file__).parents[1] / "spese_mensili.py"


class State(dict):
    def __getattr__(self, key):
        return self[key]

    def __setattr__(self, key, value):
        self[key] = value


def app_helpers():
    tree = ast.parse(SOURCE.read_text())
    defaults = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "DEFAULT_TURNI_RULES" for t in n.targets))
    defaults = ast.literal_eval(defaults)
    names = {"_encode_calibration_snapshot", "_decode_calibration_snapshot", "get_turni_rules",
             "save_turni_rules", "_parse_float_turni", "_render_mobile_calibration"}
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    state = State()
    st = SimpleNamespace(session_state=state, fragment=lambda f: f)
    ns = dict(st=st, pd=pd, json=json, base64=base64, zlib=zlib, asdict=asdict,
              DEFAULT_TURNI_RULES=defaults, PAYROLL_V2_DEFAULTS=DEFAULT_RULES,
              TURNI_RULES_WORKSHEET="Regole Turni", CALIBRATION_STATE_HEADER="Calibrazione salvata",
              TURNI_RULES_HEADERS=list(defaults) + ["Calibrazione salvata"],
              migrate_payroll_rules=migrate_rules)
    exec(compile(ast.Module(body=funcs, type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns, st


def test_snapshot_and_parameters_survive_new_session_and_rule_edits():
    ns, st = app_helpers()
    stored = []
    ns["save_data_gsheets"] = lambda name, headers, data, clear_existing: stored.append((data.copy(), clear_existing)) or True
    snapshot = {"version": 1, "result": {"fixed_net": 2301.5, "rows": []}, "drive": {"checked_at": "27/10/2026 12:00"}}
    rules = dict(ns["DEFAULT_TURNI_RULES"], netto_fisso_mensile=2301.5)
    assert ns["save_turni_rules"](rules, calibration_snapshot=snapshot)
    assert stored[-1][1] is False  # Atomic row overwrite: no destructive clear before update.
    st.session_state = State()  # Fresh browser/server session, no session-only Drive data.
    ns["load_data_gsheets"] = lambda *a, **kw: stored[-1][0].copy()
    restored = ns["get_turni_rules"]()
    assert restored["netto_fisso_mensile"] == 2301.5
    assert st.session_state.payroll_calibration_saved == snapshot
    assert ns["save_turni_rules"](dict(restored, patrono_giorno=11))
    encoded = stored[-1][0].iloc[0]["Calibrazione salvata"]
    assert ns["_decode_calibration_snapshot"](encoded) == snapshot
    assert ns["_decode_calibration_snapshot"]("") is None
    assert ns["_decode_calibration_snapshot"]("broken") is None


@pytest.mark.parametrize("clicked", [False, True])
def test_no_background_work_and_failed_sync_keeps_applied_result(clicked):
    ns, st = app_helpers()
    old_snapshot = {"version": 1, "result": {"rows": []}}
    old_rules = dict(ns["DEFAULT_TURNI_RULES"])
    st.session_state.update(payroll_calibration_saved=old_snapshot, turni_rules=old_rules)
    calls = []
    st.markdown = lambda *a, **kw: None
    st.button = lambda *a, **kw: clicked
    st.spinner = lambda *a, **kw: nullcontext()
    st.warning = lambda msg: calls.append("warning")
    st.error = lambda msg: calls.append("error")
    ns["_render_saved_mobile_calibration"] = lambda snapshot: calls.append(snapshot)
    def offline(**kwargs):
        assert kwargs == {"force": True}
        calls.append("drive")
        return {"error": "Drive non disponibile"}
    ns["_mobile_drive_payslips"] = offline
    ns["_render_mobile_calibration"](old_rules)
    assert calls == (["drive", "warning", old_snapshot] if clicked else [old_snapshot])
    assert st.session_state.turni_rules == old_rules
    assert st.session_state.payroll_calibration_saved == old_snapshot


@pytest.mark.parametrize("save_ok", [False, True])
def test_sync_applies_only_after_successful_persistence(save_ok):
    from payroll_engine import VariableBreakdown, add_months
    ns, st = app_helpers()
    old_rules = dict(ns["DEFAULT_TURNI_RULES"])
    old_snapshot = {"version": 1, "result": {"rows": []}}
    st.session_state.update(turni_rules=old_rules, payroll_calibration_saved=old_snapshot)
    st.markdown = st.warning = st.error = lambda *a, **kw: None
    st.button = lambda *a, **kw: True
    st.spinner = lambda *a, **kw: nullcontext()
    class Rerun(BaseException):
        pass
    def rerun(**kwargs):
        assert kwargs == {"scope": "app"}
        raise Rerun()
    st.rerun = rerun
    saves = []
    def save(rules, **kwargs):
        saves.append((rules, kwargs["calibration_snapshot"]))
        return save_ok
    ns.update(_render_saved_mobile_calibration=lambda snapshot: None,
              _mobile_drive_payslips=lambda **kw: {"error": "", "salaries": {"2026-09": 2500., "2026-10": 2600.}},
              load_turni_data=lambda **kw: pd.DataFrame(),
              _payroll_variables_by_month=lambda *a: {"2026-08": VariableBreakdown(), "2026-09": VariableBreakdown()},
              load_payroll_adjustments=lambda **kw: {}, DEFAULT_PAYROLL_ADJUSTMENT=-63.,
              holiday_compensation=lambda *a: [], _payroll_v2_rules=lambda r: r,
              add_payroll_months=add_months, calibrate_payroll=calibrate,
              save_turni_rules=save)
    if save_ok:
        with pytest.raises(Rerun):
            ns["_render_mobile_calibration"](old_rules)
        assert st.session_state.turni_rules == saves[0][0]
        assert st.session_state.payroll_calibration_saved == saves[0][1]
        assert st.session_state.payroll_calibration_mae >= 0
        assert st.session_state.payroll_calibration_sync_success is True
    else:
        ns["_render_mobile_calibration"](old_rules)
        assert st.session_state.turni_rules == old_rules
        assert st.session_state.payroll_calibration_saved == old_snapshot
        assert "payroll_calibration_mae" not in st.session_state
    assert len(saves) == 1
