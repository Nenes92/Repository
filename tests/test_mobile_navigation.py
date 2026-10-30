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
