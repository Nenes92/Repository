"""Keep mobile section links in the active Streamlit session."""
from hashlib import sha256
import streamlit as st
from streamlit.components.v2 import component

JS = """
export default function({data, setTriggerValue}) {
  function navigate(event) {
    const link = event.target.closest?.('a.mobile-section-link');
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const section = new URL(link.href).searchParams.get('mobile_section');
    if (!data.sections.includes(section)) return;
    event.preventDefault();
    if (section === data.selected) return;
    for (const item of document.querySelectorAll('a.mobile-section-link')) {
      item.classList.toggle('active', item === link);
    }
    setTriggerValue('navigate', section);
  }
  document.addEventListener('click', navigate);
  return () => document.removeEventListener('click', navigate);
}
"""


def _navigate_in_session(key, sections):
    state = st.session_state.get(key, {})
    section = state.get('navigate')
    if not isinstance(section, str) or section not in sections:
        return
    st.session_state['mobile_section_select'] = section
    st.session_state['_pending_mobile_section'] = section
    previous_params = st.query_params.to_dict()
    params = dict(previous_params)
    params.update(view='mobile', mobile_section=section)
    # Preserve the latest unsaved salary inputs; old URL values must not reset them.
    salary_keys = {'stip':'mobile_salary_stipendio_percepito_value',
                   'quota':'mobile_salary_budget_da_stipendio_value',
                   'risp':'mobile_salary_risparmi_mese_precedente_value'}
    for param, value_key in salary_keys.items():
        if value_key in st.session_state:
            params[param] = f"{float(st.session_state[value_key]):.2f}"
    if section == 'Turni':
        params.pop('turni_month', None)
        if 'turni_month' in previous_params:
            del st.query_params['turni_month']
    params.pop('turni_day', None)
    if 'turni_day' in previous_params:
        del st.query_params['turni_day']
    for name, value in params.items():
        if previous_params.get(name) != value:
            st.query_params[name] = value


def render_mobile_navigation(sections, selected):
    version = sha256(JS.encode()).hexdigest()[:12]
    key = f'mobile_navigation::{version}'
    renderer = component(f'mobile_navigation_{version}', js=JS)
    renderer(key=key, data={'sections': list(sections), 'selected': selected}, height=0,
             on_navigate_change=lambda: _navigate_in_session(key, sections))


LIVE_REFRESH_JS = """
export default function({data, setTriggerValue}) {
  const deadlines = data.deadlines.map(item => ({...item, at: Date.parse(item.at)}))
    .filter(item => Number.isFinite(item.at)).sort((a, b) => a.at - b.at);
  if (!deadlines.length) return;
  const next = deadlines[0];
  const timer = setTimeout(() => setTriggerValue('refresh', {kind:next.kind, at:next.at}),
    Math.min(2147483647, Math.max(100, next.at - Date.now() + 100)));
  return () => clearTimeout(timer);
}
"""


def _refresh_live_in_session(key, month):
    event = st.session_state.get(key, {}).get('refresh')
    if not isinstance(event, dict):
        return
    # Release arrow selection only when the active shift actually ends.
    if event.get('kind') == 'end' and st.query_params.get('turni_month') == month:
        del st.query_params['turni_month']


def render_mobile_live_refresh(stats, now):
    deadlines = []
    active = (stats.get('is_on_shift') or stats.get('is_on_leave')) and stats.get('current_shift_end', '') > now
    for kind, name in [('end', 'current_shift_end'), ('rate', 'current_rate_change_at')]:
        if active and stats.get(name, '') > now:
            deadlines.append({'kind': kind, 'at': stats[name]})
    if not active and stats.get('next_shift_start', '') > now:
        deadlines.append({'kind': 'start', 'at': stats['next_shift_start']})
    if not deadlines:
        return
    version = sha256(LIVE_REFRESH_JS.encode()).hexdigest()[:12]
    # A new deadline gets a fresh trigger even if the app was backgrounded.
    identity = sha256(str(deadlines).encode()).hexdigest()[:12]
    key = f'mobile_live_refresh::{version}::{identity}'
    renderer = component(f'mobile_live_refresh_{version}', js=LIVE_REFRESH_JS)
    renderer(key=key, data={'deadlines': deadlines}, height=0,
             on_refresh_change=lambda: _refresh_live_in_session(key, stats.get('live_month_key', '')))
