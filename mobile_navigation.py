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
