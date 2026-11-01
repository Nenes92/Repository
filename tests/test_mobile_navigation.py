from types import SimpleNamespace
import mobile_navigation as nav


class Query(dict):
    def to_dict(self): return dict(self)
    def from_dict(self, data): self.clear();self.update(data)


def test_navigation_keeps_salary_draft_and_updates_route_in_same_session(monkeypatch):
    state={'navigation':{'navigate':'Carte'}, 'unrelated_cached_data':123,
           'mobile_salary_stipendio_percepito_value':2600.,
           'mobile_salary_budget_da_stipendio_value':2500.,
           'mobile_salary_risparmi_mese_precedente_value':100.}
    query=Query(view='mobile',mobile_section='Panoramica',stip='2000',turni_day='2026-10-20',turni_month='2026-10')
    monkeypatch.setattr(nav, 'st', SimpleNamespace(session_state=state,query_params=query))
    nav._navigate_in_session('navigation',['Panoramica','Carte'])
    assert state['mobile_section_select']=='Carte'
    assert state['unrelated_cached_data']==123
    assert query['mobile_section']=='Carte' and query['stip']=='2600.00'
    assert query['quota']=='2500.00' and query['risp']=='100.00'
    assert 'turni_day' not in query and query['turni_month']=='2026-10'


def test_invalid_navigation_does_not_modify_route(monkeypatch):
    state={'navigation':{'navigate':'unknown'}}
    query=Query(mobile_section='Panoramica')
    monkeypatch.setattr(nav, 'st', SimpleNamespace(session_state=state,query_params=query))
    nav._navigate_in_session('navigation',['Panoramica','Carte'])
    assert query=={'mobile_section':'Panoramica'}
    assert 'mobile_section_select' not in state


def test_opening_turni_resets_previous_manual_month_to_follow_active_work(monkeypatch):
    state = {'navigation': {'navigate': 'Turni'}}
    query = Query(view='mobile', mobile_section='Carte', turni_month='2025-05')
    monkeypatch.setattr(nav, 'st', SimpleNamespace(session_state=state, query_params=query))
    nav._navigate_in_session('navigation', ['Carte', 'Turni'])
    assert query['mobile_section'] == 'Turni'
    assert 'turni_month' not in query


def test_live_deadline_releases_only_the_selected_active_month(monkeypatch):
    state = {'timer': {'refresh': {'kind': 'rate', 'at': 123}}}
    query = Query(turni_month='2026-10')
    monkeypatch.setattr(nav, 'st', SimpleNamespace(session_state=state, query_params=query))
    nav._refresh_live_in_session('timer', '2026-10')
    assert query['turni_month'] == '2026-10'
    state['timer']['refresh']['kind'] = 'end'
    nav._refresh_live_in_session('timer', '2026-09')
    assert query['turni_month'] == '2026-10'  # Keep intentional historical browsing.
    nav._refresh_live_in_session('timer', '2026-10')
    assert 'turni_month' not in query


def test_live_timer_ignores_expired_leave_end_and_schedules_next_work(monkeypatch):
    calls = []
    monkeypatch.setattr(nav, 'component', lambda *a, **kw: lambda **data: calls.append(data))
    stats = {'is_on_leave': True, 'current_shift_end': '2026-11-01T17:00:00',
             'next_shift_start': '2026-11-02T06:00:00'}
    nav.render_mobile_live_refresh(stats, '2026-11-01T18:00:00')
    assert calls[0]['data']['deadlines'] == [{'kind': 'start', 'at': '2026-11-02T06:00:00'}]
    calls.clear()
    nav.render_mobile_live_refresh(stats, '2026-11-03T00:00:00')
    assert not calls


def test_fixed_morning_handoff_keeps_month_after_work_and_refreshes_at_eight(monkeypatch):
    state = {'timer': {'refresh': {'kind': 'end', 'at': 123}}}
    query = Query(turni_month='2026-10')
    monkeypatch.setattr(nav, 'st', SimpleNamespace(session_state=state, query_params=query))
    nav._refresh_live_in_session('timer', '2026-10', hold_month=True)
    assert query['turni_month'] == '2026-10'
    state['timer']['refresh']['kind'] = 'month'
    nav._refresh_live_in_session('timer', '2026-10', hold_month=True)
    assert 'turni_month' not in query
    calls = []
    monkeypatch.setattr(nav, 'component', lambda *a, **kw: lambda **data: calls.append(data))
    nav.render_mobile_live_refresh({'is_on_shift':False, 'live_month_key':'2026-10',
        'month_handoff_at':'2026-11-01T08:00:00'}, '2026-11-01T06:30:00')
    assert calls[0]['data']['deadlines'] == [{'kind':'month','at':'2026-11-01T08:00:00'}]
