"""Exercise the app's I/O helpers without booting the UI or accessing live Sheets."""
import ast
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
import time

import pandas as pd
import pytest
import streamlit

SOURCE = Path(__file__).resolve().parents[1] / "spese_mensili.py"


@pytest.fixture
def app():
    streamlit.cache_data.clear()
    streamlit.cache_resource.clear()
    calls = Counter()

    class Worksheet:
        title = "Test"
        headers = ["Value"]
        records = [{"Value": 1}]
        fail_read = False
        fail_update = False

        def row_values(self, row):
            calls["headers"] += 1
            return self.headers.copy()

        def get_all_records(self):
            calls["records"] += 1
            if self.fail_read:
                raise RuntimeError("429 Quota exceeded")
            return [{key: int(value) if str(value).isdigit() else value
                     for key, value in row.items()} for row in self.records]

        def clear(self):
            calls["clear"] += 1
            self.records = []

        def update(self, *, values, range_name):
            calls["update"] += 1
            if self.fail_update:
                raise RuntimeError("update failed")
            self.headers = values[0]
            self.records = [dict(zip(self.headers, row)) for row in values[1:]]

    worksheet = Worksheet()

    class Spreadsheet:
        def worksheets(self):
            calls["metadata"] += 1
            return [worksheet]

        def worksheet(self, name):
            calls["metadata"] += 1
            return worksheet

    sheet = Spreadsheet()
    st = SimpleNamespace(
        cache_data=streamlit.cache_data,
        cache_resource=streamlit.cache_resource,
        session_state={}, warning=lambda message: None, error=lambda message: None,
    )
    ns = dict(st=st, pd=pd, time=time, MOBILE_VIEW=True, SHEET_URL="test-sheet",
              GSHEETS_CACHE_TTL_SECONDS=1800, GSHEETS_BACKOFF_SECONDS=90,
              GSHEETS_BACKOFF_LABEL="90 secondi", get_gsheet_client=lambda: object(),
              get_gsheet_spreadsheet=lambda: sheet)
    nodes = [node for node in ast.parse(SOURCE.read_text()).body
             if isinstance(node, ast.FunctionDef)
             and node.name not in ("get_gsheet_client", "get_gsheet_spreadsheet")]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), ns)
    yield ns, worksheet, calls
    streamlit.cache_data.clear()
    streamlit.cache_resource.clear()


def test_mobile_reruns_and_new_sessions_reuse_reads(app):
    ns, worksheet, calls = app
    load = ns["load_data_gsheets"]
    first = load("Test", ["Value"])
    first.loc[0, "Value"] = 999  # callers cannot mutate either cache
    assert load("Test", ["Value"]).iloc[0]["Value"] == 1
    ns["st"].session_state.clear()  # mobile navigation / reconnect
    assert load("Test", ["Value"]).iloc[0]["Value"] == 1
    assert calls == {"metadata": 1, "headers": 1, "records": 1}


def test_expired_shared_cache_rereads_only_records(app):
    ns, worksheet, calls = app
    ns["load_data_gsheets"]("Test", ["Value"])
    ns["_mobile_sheet_records"].clear()  # same miss as TTL expiration
    ns["st"].session_state.clear()
    ns["load_data_gsheets"]("Test", ["Value"])
    assert calls == {"metadata": 1, "headers": 1, "records": 2}


def test_force_reload_bypasses_both_data_caches(app):
    ns, worksheet, calls = app
    ns["load_data_gsheets"]("Test", ["Value"])
    worksheet.records = [{"Value": 2}]
    assert ns["load_data_gsheets"]("Test", ["Value"], True).iloc[0]["Value"] == 2
    assert calls["records"] == 2


@pytest.mark.parametrize("mobile_writer", [True, False])
def test_save_invalidates_mobile_cache_for_next_session(app, mobile_writer):
    ns, worksheet, calls = app
    ns["load_data_gsheets"]("Test", ["Value"])
    ns["MOBILE_VIEW"] = mobile_writer
    assert ns["save_data_gsheets"]("Test", ["Value"], pd.DataFrame([{"Value": 7}]))
    ns["MOBILE_VIEW"] = True
    ns["st"].session_state.clear()
    assert ns["load_data_gsheets"]("Test", ["Value"]).iloc[0]["Value"] == 7


def test_failed_write_does_not_leave_shared_old_records(app):
    ns, worksheet, calls = app
    ns["load_data_gsheets"]("Test", ["Value"])
    worksheet.fail_update = True
    assert not ns["save_data_gsheets"]("Test", ["Value"], pd.DataFrame([{"Value": 7}]))
    ns["st"].session_state.clear()
    assert ns["load_data_gsheets"]("Test", ["Value"]).empty
    assert calls["records"] == 2


def test_quota_failure_is_not_cached_and_backoff_retries(app):
    ns, worksheet, calls = app
    worksheet.fail_read = True
    assert ns["load_data_gsheets"]("Test", ["Value"]).empty
    assert ns["load_data_gsheets"]("Test", ["Value"]).empty
    assert calls["records"] == 1
    worksheet.fail_read = False
    ns["st"].session_state["gsheets_backoff_until"] = 0
    assert len(ns["load_data_gsheets"]("Test", ["Value"])) == 1
    assert calls["records"] == 2


def test_desktop_keeps_session_only_read_behavior(app):
    ns, worksheet, calls = app
    ns["MOBILE_VIEW"] = False
    for _ in range(2):
        ns["st"].session_state.clear()
        ns["load_data_gsheets"]("Test", ["Value"])
        ns["load_data_gsheets"]("Test", ["Value"])
    assert calls == {"metadata": 2, "headers": 2, "records": 2}


def test_snapshot_uses_synced_data_widget_rules_and_recomputes_live(app):
    ns, worksheet, calls = app
    empty = pd.DataFrame()
    rules = {"rate": 1}
    ns["get_turni_rules"] = lambda: rules
    ns["_apply_turni_rules_from_widgets"] = lambda old: {"rate": 2}
    ns["ensure_turni_month_synced"] = lambda month: (empty, ["calendar unavailable"])

    def calculate(df, actual_rules):
        assert df is empty
        assert actual_rules == {"rate": 2}
        calls["live"] += 1
        return {"live_month": calls["live"]}

    ns["compute_turni_dashboard"] = calculate
    for expected in (1, 2):
        df, actual_rules, stats, errors = ns["_mobile_turni_snapshot"]("2026-09")
        assert stats["live_month"] == expected
        assert errors == ["calendar unavailable"]
