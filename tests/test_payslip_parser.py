import pytest

from payslip_parser import (
    candidates_total,
    extract_payslip_month,
    find_adjustment_candidates,
    label_signature,
    parse_italian_amount,
)


def test_parses_italian_currency_amounts():
    assert parse_italian_amount("1.234,56") == pytest.approx(1234.56)
    assert parse_italian_amount("-63,00") == pytest.approx(-63.0)


def test_extracts_month_from_text_or_filename():
    assert extract_payslip_month("Periodo retributivo LUGLIO 2026") == "2026-07"
    assert extract_payslip_month("Cedolino", "cedolino_2026-08.pdf") == "2026-08"


def test_recognizes_credit_and_local_tax_deductions():
    text = """
    EDR Accredito Integrativo 2021 58,50
    Addizionale regionale saldo 42,35
    Addizionale comunale acconto 19,80
    """
    candidates = find_adjustment_candidates(text)
    assert len(candidates) == 3
    # L'EDR può essere imponibile lordo: viene mostrato, ma non preselezionato.
    assert candidates[0].include is False
    assert candidates_total(candidates) == pytest.approx(-62.15)


def test_excludes_ordinary_pay_shift_variables_and_normal_tax():
    text = """
    Paga base 1.725,00
    Maggiorazione notturna 125,40
    Straordinario feriale 62,30
    Ritenuta IRPEF 410,00
    Addizionale regionale 38,00
    """
    candidates = find_adjustment_candidates(text)
    assert [candidate.category for candidate in candidates] == ["Addizionale regionale"]


def test_multiple_amounts_lower_confidence_and_use_rightmost_total():
    candidates = find_adjustment_candidates(
        "Addizionale regionale 12,00 3,50 42,00"
    )
    assert candidates[0].amount == pytest.approx(42.0)
    assert candidates[0].confidence < 0.8


def test_confirmed_label_is_reused_when_amount_changes():
    learned = {
        "Voce aziendale speciale": {
            "sign": 1,
            "include": True,
            "category": "Accredito confermato",
        }
    }
    candidates = find_adjustment_candidates(
        "Voce aziendale speciale 81,25",
        learned,
    )
    assert len(candidates) == 1
    assert candidates[0].signed_amount == pytest.approx(81.25)
    assert candidates[0].confidence == pytest.approx(0.98)


def test_label_signature_ignores_changing_numbers():
    assert label_signature("EDR 2021 importo 58") == label_signature("EDR 2026 importo 72")
