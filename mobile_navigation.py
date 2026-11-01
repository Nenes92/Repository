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


def _refresh_live_in_session(key, month, hold_month=False):
    event = st.session_state.get(key, {}).get('refresh')
    if not isinstance(event, dict):
        return
    # Release arrow selection at the month handoff, or at shift end without a hold.
    if (event.get('kind') == 'month' or (event.get('kind') == 'end' and not hold_month)) and st.query_params.get('turni_month') == month:
        del st.query_params['turni_month']


def render_mobile_live_refresh(stats, now):
    deadlines = []
    if stats.get('month_handoff_at', '') > now:
        deadlines.append({'kind': 'month', 'at': stats['month_handoff_at']})
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
             on_refresh_change=lambda: _refresh_live_in_session(key, stats.get('live_month_key', ''), bool(stats.get('month_handoff_at'))))


SALARY_SWIPE_JS = """
export default function() {
  let dispose = () => {};
  function attach() {
    const root = document.querySelector('.st-key-mobile-salary-chart-pages');
    const track = root?.matches('[data-testid="stHorizontalBlock"]') ? root : root?.querySelector('[data-testid="stHorizontalBlock"]');
    const pages = [document.querySelector('.st-key-mobile-salary-chart-trend'), document.querySelector('.st-key-mobile-salary-chart-years')];
    const buttons = [...document.querySelectorAll('[data-salary-page]')];
    if (!track || pages.some(page => !page) || buttons.length !== 2) return false;
    Object.assign(track.style, {flexWrap:'nowrap',gap:'0px',overflowX:'auto',overflowY:'hidden',paddingBottom:'16px',scrollSnapType:'x mandatory',scrollbarWidth:'none',overscrollBehaviorX:'contain',touchAction:'pan-y pinch-zoom'});
    for (const page of pages) {
      let child = page;
      while (child.parentElement && child.parentElement !== track) child = child.parentElement;
      child.style.setProperty('flex', '0 0 100%', 'important');
      child.style.setProperty('min-width', '100%', 'important');
      child.style.setProperty('max-width', '100%', 'important');
      child.style.scrollSnapAlign = 'start';
    }
    let index = Number(track.dataset.salaryPage || 0);
    const label = () => buttons.forEach((button, i) => {
      button.setAttribute('aria-selected', String(i === index));
      button.tabIndex = i === index ? 0 : -1;
    });
    const hideTooltip = () => document.querySelectorAll('.vg-tooltip.visible').forEach(tip => tip.classList.remove('visible'));
    function go(next, smooth=true) {
      hideTooltip();
      index = Math.max(0, Math.min(1, next));
      track.dataset.salaryPage = String(index);
      track.scrollTo({left: index * track.clientWidth, behavior: smooth ? 'smooth' : 'instant'});
      label();
    }
    const click = event => go(Number(event.currentTarget.dataset.salaryPage));
    const key = event => {
      if (!['ArrowLeft','ArrowRight'].includes(event.key)) return;
      event.preventDefault(); go(index + (event.key === 'ArrowRight' ? 1 : -1)); buttons[index].focus();
    };
    let origin = null;
    const start = point => { origin = {x:point.clientX, y:point.clientY}; };
    const end = (point, event) => {
      if (!origin) return;
      const dx = point.clientX - origin.x, dy = point.clientY - origin.y;
      origin = null;
      if (Math.abs(dx) >= 45 && Math.abs(dx) > Math.abs(dy) * 1.3) {
        event.preventDefault(); go(index + (dx < 0 ? 1 : -1));
      }
    };
    const touchStart = event => { if (event.touches.length === 1) start(event.touches[0]); else origin = null; };
    const touchMove = event => {
      if (!origin || event.touches.length !== 1) return;
      const point = event.touches[0];
      if (Math.abs(point.clientX-origin.x)>30 && Math.abs(point.clientX-origin.x)>Math.abs(point.clientY-origin.y)*1.3) event.preventDefault();
    };
    const touchEnd = event => { if (event.changedTouches[0]) end(event.changedTouches[0], event); };
    const pointerDown = event => { if (event.pointerType !== 'touch') start(event); };
    const pointerUp = event => { if (event.pointerType !== 'touch') end(event, event); };
    const scroll = () => { hideTooltip(); index = Math.round(track.scrollLeft / Math.max(1, track.clientWidth)); track.dataset.salaryPage = String(index); label(); };
    buttons.forEach(button => { button.addEventListener('click',click); button.addEventListener('keydown',key); });
    track.addEventListener('touchstart',touchStart,{passive:true});
    track.addEventListener('touchmove',touchMove,{passive:false});
    track.addEventListener('touchend',touchEnd,{passive:false});
    track.addEventListener('pointerdown',pointerDown);
    track.addEventListener('pointerup',pointerUp);
    track.addEventListener('scroll',scroll,{passive:true});
    const resize = new ResizeObserver(() => go(index,false)); resize.observe(track);
    go(index,false);
    dispose = () => {
      resize.disconnect();
      buttons.forEach(button => { button.removeEventListener('click',click); button.removeEventListener('keydown',key); });
      track.removeEventListener('touchstart',touchStart); track.removeEventListener('touchmove',touchMove); track.removeEventListener('touchend',touchEnd);
      track.removeEventListener('pointerdown',pointerDown); track.removeEventListener('pointerup',pointerUp); track.removeEventListener('scroll',scroll);
    };
    return true;
  }
  const observer = new MutationObserver(() => { if (attach()) observer.disconnect(); });
  if (!attach()) observer.observe(document.body,{childList:true,subtree:true});
  return () => { observer.disconnect(); dispose(); };
}
"""


def render_mobile_salary_swipe():
    version = sha256(SALARY_SWIPE_JS.encode()).hexdigest()[:12]
    renderer = component(f'mobile_salary_swipe_{version}', js=SALARY_SWIPE_JS)
    renderer(key=f'mobile_salary_swipe::{version}', height=0)
