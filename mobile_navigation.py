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


CHART_SWIPE_JS = """
export default function({data}) {
  let dispose = () => {};
  function attach() {
    const root = document.querySelector('.st-key-' + data.track);
    const track = root?.matches('[data-testid="stHorizontalBlock"]') ? root : root?.querySelector('[data-testid="stHorizontalBlock"]');
    const pages = data.pages.map(key => document.querySelector('.st-key-' + key));
    const buttons = [...document.querySelectorAll('[data-chart-carousel="' + data.id + '"] [data-chart-page]')];
    if (!track || pages.some(page => !page) || (buttons.length !== pages.length || pages.length < 2)) return false;
    Object.assign(track.style, {flexWrap:'nowrap',gap:'0px',overflowX:'auto',overflowY:'hidden',paddingBottom:'4px',scrollSnapType:'x mandatory',scrollbarWidth:'none',overscrollBehaviorX:'contain',touchAction:'pan-y pinch-zoom'});
    // Ancestor :has() rules for input grids must not turn the carousel into a grid.
    track.style.setProperty('display', 'flex', 'important');
    for (const page of pages) {
      let child = page;
      while (child.parentElement && child.parentElement !== track) child = child.parentElement;
      child.style.setProperty('flex', '0 0 100%', 'important');
      child.style.setProperty('min-width', '100%', 'important');
      child.style.setProperty('max-width', '100%', 'important');
      child.style.scrollSnapAlign = 'start';
    }
    let index = Number(track.dataset.chartPage || 0);
    const label = () => buttons.forEach((button, i) => {
      button.setAttribute('aria-selected', String(i === index));
      button.tabIndex = i === index ? 0 : -1;
    });
    const updateHeight = () => {
      if (!data.autoHeight) return;
      const height = Math.ceil(pages[index].getBoundingClientRect().height) + 4;
      if (Math.abs(track.getBoundingClientRect().height-height)>1) track.style.height = height+'px';
    };
    const hideTooltip = () => document.querySelectorAll('.vg-tooltip.visible').forEach(tip => tip.classList.remove('visible'));
    function go(next, smooth=true) {
      hideTooltip();
      index = Math.max(0, Math.min(pages.length-1, next));
      track.dataset.chartPage = String(index);
      track.scrollTo({left: index * track.clientWidth, behavior: smooth ? 'smooth' : 'instant'});
      label();
      if (!smooth) updateHeight();
    }
    const click = event => go(Number(event.currentTarget.dataset.chartPage));
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
    const scroll = () => { hideTooltip(); index = Math.max(0, Math.min(pages.length-1, Math.round(track.scrollLeft / Math.max(1, track.clientWidth)))); track.dataset.chartPage = String(index); label(); updateHeight(); };
    buttons.forEach(button => { button.addEventListener('click',click); button.addEventListener('keydown',key); });
    track.addEventListener('touchstart',touchStart,{passive:true});
    track.addEventListener('touchmove',touchMove,{passive:false});
    track.addEventListener('touchend',touchEnd,{passive:false});
    track.addEventListener('pointerdown',pointerDown);
    track.addEventListener('pointerup',pointerUp);
    track.addEventListener('scroll',scroll,{passive:true});
    let previousWidth = track.clientWidth;
    const resize = new ResizeObserver(() => {
      const width = track.clientWidth;
      if (width !== previousWidth) { previousWidth = width; go(index,false); }
      else updateHeight();
    });
    resize.observe(track);
    if (data.autoHeight) pages.forEach(page => resize.observe(page));
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


def render_mobile_chart_styles(track_key, auto_height=False):
    st.html(f"""<style>
      .st-key-{track_key} {{flex-wrap:nowrap!important;gap:0!important;overflow-x:auto;overflow-y:hidden;padding-bottom:4px;scroll-snap-type:x mandatory;scrollbar-width:none;overscroll-behavior-x:contain;touch-action:pan-y pinch-zoom;}}
      .st-key-{track_key}::-webkit-scrollbar {{display:none;}}
      .st-key-{track_key} > [data-testid="stLayoutWrapper"] {{flex:0 0 100%!important;min-width:100%!important;max-width:100%!important;scroll-snap-align:start;}}
      .st-key-{track_key} canvas {{touch-action:pan-y pinch-zoom!important;}}
      [data-testid="stElementContainer"]:has(> [data-testid="stHtml"] .mobile-chart-dots) {{margin-top:-6px;margin-bottom:-6px;}}
      .mobile-chart-dots {{display:flex;justify-content:center;gap:2px;margin:0;}}
      .mobile-chart-dots button {{width:26px;height:26px;padding:8px;border:0;border-radius:50%;background:transparent;display:flex;align-items:center;justify-content:center;cursor:pointer;}}
      .mobile-chart-dots button span {{width:7px;height:7px;border-radius:50%;background:#64748b;display:block;}}
      .mobile-chart-dots button[aria-selected="true"] span {{background:#60a5fa;}}
      .mobile-chart-dots button:focus-visible {{outline:1px solid #60a5fa;}}
      .mobile-chart-dashes {{gap:6px;}}
      .mobile-chart-dashes button {{width:30px;padding:4px;border-radius:5px;}}
      .mobile-chart-dashes button span {{width:18px;height:3px;border-radius:2px;}}
    </style>""")
    if auto_height:
        st.html(f"""<style>
          .st-key-{track_key} {{align-items:flex-start!important;flex:0 0 auto!important;min-height:0!important;}}
          .st-key-{track_key} > [data-testid="stLayoutWrapper"] {{align-self:flex-start;}}
          .st-key-{track_key} [data-testid="stMarkdownContainer"]:has(> div) {{margin-bottom:0!important;}}
        </style>""")


def render_mobile_chart_dots(carousel_id, labels, indicator="dots"):
    from html import escape
    dots = ''.join(
        f'<button type="button" role="tab" aria-label="{escape(label, quote=True)}" aria-selected="{str(i == 0).lower()}" data-chart-page="{i}"><span aria-hidden="true"></span></button>'
        for i, label in enumerate(labels))
    classes = "mobile-chart-dots mobile-chart-dashes" if indicator == "dashes" else "mobile-chart-dots"
    aria_label = "Pagine del riepilogo" if indicator == "dashes" else "Pagine del grafico"
    st.html(f'<div class="{classes}" role="tablist" aria-label="{aria_label}" data-chart-carousel="{escape(carousel_id, quote=True)}">{dots}</div>')


def render_mobile_chart_swipe(carousel_id, track_key, page_keys, auto_height=False):
    version = sha256(CHART_SWIPE_JS.encode()).hexdigest()[:12]
    renderer = component(f'mobile_chart_swipe_{version}', js=CHART_SWIPE_JS)
    renderer(key=f'mobile_chart_swipe::{carousel_id}::{version}', height=0,
             data={'id': carousel_id, 'track': track_key, 'pages': page_keys, 'autoHeight': auto_height})
