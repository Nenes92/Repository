"""PDF 730 history is read-only; markers never add cash to salary statistics."""
import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import json
import pandas as pd
import altair as alt


def helpers():
    tree = ast.parse((Path(__file__).parents[1] / 'spese_mensili.py').read_text())
    names = {'_payroll_730_history', '_saved_730_history', '_salary_730_markers',
             'render_grafico_stipendi_desktop_style', '_signed_money_turni', '_money_turni'}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    for node in nodes:
        node.decorator_list = []
    plots, errors = [], []
    st = SimpleNamespace(session_state={}, markdown=lambda *a, **kw: None,
        info=lambda *a, **kw: None, error=errors.append,
        altair_chart=lambda chart, **kw: plots.append(chart.to_dict()))
    env = dict(st=st, pd=pd, alt=alt, MOBILE_VIEW=True,
               _now_italy=lambda: datetime(2026, 11, 1),
               get_turni_rules=lambda: (_ for _ in ()).throw(AssertionError('Unexpected external read')))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'salary-history', 'exec'), env)
    return env, st, plots, errors


def snapshot():
    # Synthetic amounts, and an intentionally different applied calibration map.
    return {'tax_adjustments': {'2025-07': -607.}, 'drive': {
        'tax_adjustments': {'2025-07': -607., '2026-09': 200.},
        'tax_details': {'2025-07': [{'amount': -640.}, {'amount': 33.}],
                        '2026-09': [{'amount': 200.}]}}}


def test_history_uses_paid_month_and_raw_730_even_when_manually_corrected():
    env, _, _, _ = helpers()
    rows = env['_payroll_730_history'](snapshot())
    assert rows == [dict(Mese='2025-07', Anno=2025, Rimborsi=33., Trattenute=640., **{'Saldo 730': -607.}),
                    dict(Mese='2026-09', Anno=2026, Rimborsi=200., Trattenute=0., **{'Saldo 730': 200.})]
    assert env['_payroll_730_history'](None) == []
    assert env['_payroll_730_history']({'drive': {'tax_adjustments': {'2026-09': 12.}}})[0]['Saldo 730'] == 12.


def test_chart_marks_only_matching_months_and_keeps_salary_values_and_means():
    env, st, plots, errors = helpers()
    st.session_state['payroll_calibration_saved'] = snapshot()
    data = pd.DataFrame({'Mese': pd.to_datetime(['2026-08-01', '2026-09-01']),
        'Stipendio': [2400., 2600.], 'Risparmi': [10., 20.], 'Messi da parte Totali': [100., 200.],
        'Media Stipendio': [2400., 2500.], 'Media Stipendio NO 13°/PDR': [2400., 2500.],
        'Media Risparmi': [10., 15.], 'Media Messi da parte Totali': [100., 150.]})
    before = data.copy(deep=True)
    env['render_grafico_stipendi_desktop_style'](data, years_back=3)
    assert not errors
    pd.testing.assert_frame_equal(data, before)
    encoded = json.dumps(plots[0])
    assert 'Saldo 730' in encoded and 'diamond' in encoded and '730 +' in encoded
    fiscal_rows = [row for values in plots[0]['datasets'].values() for row in values if 'Saldo 730' in row]
    assert len(fiscal_rows) == 1
    assert fiscal_rows[0]['Stipendio'] == 2600. and fiscal_rows[0]['Saldo 730'] == 200.


def test_mobile_trend_displays_exactly_sixteen_months_without_recomputing_statistics():
    env, st, plots, errors = helpers()
    st.session_state['payroll_calibration_saved'] = {}
    dates = pd.date_range('2025-06-01', '2026-11-01', freq='MS')
    data = pd.DataFrame({'Mese': dates, 'Stipendio':range(2100,2100+len(dates)),
        'Risparmi':10., 'Messi da parte Totali':100., 'Media Stipendio':2200.,
        'Media Stipendio NO 13°/PDR':2150., 'Media Risparmi':10., 'Media Messi da parte Totali':100.})
    before = data.copy(deep=True)
    env['render_grafico_stipendi_desktop_style'](data, months_back=16)
    assert not errors
    months = {row['Mese'][:7] for rows in plots[0]['datasets'].values() for row in rows if 'Mese' in row}
    assert len(months) == 16 and min(months) == '2025-08' and max(months) == '2026-11'
    pd.testing.assert_frame_equal(data,before)


def test_fifteen_months_end_at_latest_saved_salary_and_keep_august_labels():
    env, st, plots, errors = helpers()
    st.session_state['payroll_calibration_saved'] = {}
    dates = pd.date_range('2025-05-01', '2026-10-01', freq='MS')
    data = pd.DataFrame({'Mese':dates, 'Stipendio':2400., 'Risparmi':10.,
        'Messi da parte Totali':100., 'Media Stipendio':2400.,
        'Media Stipendio NO 13°/PDR':2400., 'Media Risparmi':10., 'Media Messi da parte Totali':100.})
    env['render_grafico_stipendi_desktop_style'](data, months_back=15)
    assert not errors
    months = {row['Mese'][:7] for rows in plots[0]['datasets'].values() for row in rows if 'Mese' in row}
    assert len(months) == 15
    assert min(months) == '2025-08' and max(months) == '2026-10'
    assert '2026-08' in months
    assert '"labelOverlap": false' in json.dumps(plots[0])
