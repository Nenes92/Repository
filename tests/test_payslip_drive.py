from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock
import pytest

from payslip_drive import Payslip, collect_payslips, list_payslip_files, parse_payslip_text

NAME = "2026_10_D_CED_ORD_SAMPLE.pdf"
TEXT = "Periodo di paga\nOttobre 2026\nCid.\n0300000000001\nNetto a pagare\n2.496,00\nImp.INAIL\n3.956,00"


def test_modern_layout_uses_net_not_taxable_or_empty_header():
    result = parse_payslip_text("Netto a pagare\nImp.INAIL\n3.956,00\n" + TEXT, NAME)
    assert result.month == "2026-10"
    assert result.net == Decimal("2496.00")


def test_legacy_layout_uses_euro_payment_not_lire():
    text = "0300000000002 X I\nMESE\nMARZO 2024\nNETTO A\nPAGARE\nIT00A0000000000000000000000 27/03/2024 1.086,00 2102789"
    assert parse_payslip_text(text, "2024_3_D_CED_ORD_SAMPLE.pdf").net == Decimal("1086")


@pytest.mark.parametrize("text,name", [
    (TEXT.replace("Ottobre", "Novembre"), NAME),
    (TEXT + "\nNetto a pagare\n2.000,00", NAME),
    (TEXT.replace("2.496,00", ""), NAME),
    (TEXT.replace("2.496,00", "0,00"), NAME),
    (TEXT + "\nPeriodo di paga\nNovembre 2026", NAME),
    (TEXT.replace("Cid.\n0300000000001", ""), NAME),
])
def test_ambiguous_values_are_not_guessed(text, name):
    with pytest.raises(ValueError):
        parse_payslip_text(text, name)


def test_same_payroll_repeated_on_pages_is_not_summed():
    assert parse_payslip_text(TEXT + "\n" + TEXT, NAME).net == Decimal("2496")


def metadata(i, name=NAME):
    return {"id": str(i), "name": name}


def test_two_positions_same_month_sum_but_copies_do_not():
    first = Payslip("2026-01", Decimal("2110"), "position1")
    second = Payslip("2026-01", Decimal("646.07"), "position2")
    result = collect_payslips([metadata(1), metadata(2), metadata(3)],
                              lambda item: second if item["id"] == "2" else first)
    assert result["salaries"] == {"2026-01": 2756.07}
    assert result["positions"] == 2


def test_conflicting_copies_exclude_month_and_surface_problem():
    first = Payslip("2026-10", Decimal("2496"), "position1")
    result = collect_payslips([metadata(1), metadata(2)],
                              lambda item: first if item["id"] == "1" else replace(first, net=Decimal("2400")))
    assert result["salaries"] == {}
    assert result["errors"]


def test_unreadable_pdf_does_not_drop_other_months():
    def read(item):
        if item["id"] == "bad":
            raise ValueError("PDF scansionato")
        return Payslip("2026-10", Decimal("2496"), "p")
    result = collect_payslips([metadata("bad"), metadata("good")], read)
    assert result["salaries"] == {"2026-10": 2496}
    assert "PDF scansionato" in result["errors"][0]


def test_only_ordinary_payslips_are_used():
    read = Mock()
    result = collect_payslips([metadata(1, "2026_12_CED_13.pdf")], read)
    read.assert_not_called()
    assert result["errors"]


def response(payload):
    r = Mock()
    r.json.return_value = payload
    return r


def test_drive_listing_verifies_folder_and_follows_pagination():
    session = Mock()
    session.get.side_effect = [
        response({"id": "folder", "mimeType": "application/vnd.google-apps.folder"}),
        response({"files": [metadata(1)], "nextPageToken": "next"}),
        response({"files": [metadata(2)]}),
    ]
    assert len(list_payslip_files(session, "folder")) == 2
    assert session.get.call_args_list[2].kwargs["params"]["pageToken"] == "next"
    assert "trashed=false" in session.get.call_args_list[1].kwargs["params"]["q"]


def test_inaccessible_folder_is_not_silently_treated_as_empty():
    session = Mock()
    session.get.return_value.raise_for_status.side_effect = RuntimeError("403")
    with pytest.raises(RuntimeError):
        list_payslip_files(session, "folder")


def test_drive_cache_only_downloads_new_or_changed_pdfs():
    import ast
    from pathlib import Path
    from datetime import datetime
    from collections import Counter
    from types import SimpleNamespace
    import streamlit as st

    st.cache_data.clear()
    tree = ast.parse((Path(__file__).parents[1] / "spese_mensili.py").read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {
        "_read_drive_payslip", "_load_drive_payslips", "_mobile_drive_payslips"}]
    calls = Counter()
    files = [{"id": "1", "name": NAME, "md5Checksum": "a"}]
    session = object()
    def listing(*args):
        calls["list"] += 1
        return files.copy()
    def download(session, file_id, name):
        calls["download"] += 1
        return Payslip("2026-10", Decimal("2496"), file_id)

    class State(dict):
        def __setattr__(self, key, value):
            self[key] = value

    state = State()
    ns = dict(st=SimpleNamespace(cache_data=st.cache_data, session_state=state),
              CREDENTIALS_INFO={"client_email": "test"}, CEDOLINI_DRIVE_URL="https://drive.google.com/drive/folders/folder",
              _get_payslip_drive_session=lambda *args: session, list_payslip_files=listing,
              download_payslip=download, collect_payslips=collect_payslips,
              _now_italy=lambda: datetime(2026, 10, 27, 7))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "drive-cache", "exec"), ns)
    load = ns["_mobile_drive_payslips"]
    assert load() is None
    assert not calls
    assert load(force=True)["salaries"] == {"2026-10": 2496}
    # Even an expired/cleared cache must not cause a background Drive request.
    ns["_load_drive_payslips"].clear()
    load()
    assert calls == {"list": 1, "download": 1}
    load(force=True)
    assert calls == {"list": 2, "download": 1}
    files.append({"id": "2", "name": NAME, "md5Checksum": "b"})
    load(force=True)
    assert calls == {"list": 3, "download": 2}
    files[0]["md5Checksum"] = "changed"
    load(force=True)
    assert calls == {"list": 4, "download": 3}
    # A temporary failure keeps a clearly marked last successful snapshot.
    def offline(*args):
        calls["list"] += 1
        raise RuntimeError("offline")
    ns["list_payslip_files"] = offline
    stale = load(force=True)
    assert stale["stale"] and stale["error"]
    load()
    assert calls["list"] == 5
    st.cache_data.clear()
