"""Mobile calendar: browse/edit locally; notify Streamlit only on explicit save."""
from streamlit.components.v2 import component

CSS = """
:host {display:block;color:#d1d5db;font-family:var(--st-font,'DM Sans',sans-serif);font-size:13px;}
* {box-sizing:border-box;} button,input,select {font:inherit;}
.grid {display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:7px;margin-top:10px;}
.head {text-align:center;color:rgba(255,255,255,.46);font-size:11px;font-weight:800;padding-bottom:2px;}
.day {position:relative;min-width:0;min-height:42px;border-radius:10px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;background:rgba(30,58,92,.70);border:.5px solid rgba(96,165,250,.36);color:#d1d5db;font-size:13px;font-weight:800;padding:3px 1px;cursor:pointer;}
.day.empty {visibility:hidden;}.day[aria-pressed=true] {border-color:rgba(125,211,252,.70);box-shadow:0 0 0 1px rgba(125,211,252,.44),0 0 14px rgba(96,165,250,.16);}
.day:hover {border-color:rgba(125,211,252,.62);background:rgba(30,64,115,.82);}
.day:focus-visible,select:focus-visible,input:focus-visible,button:focus-visible {outline:2px solid #7dd3fc;outline-offset:2px;}
.day-main {display:flex;align-items:center;justify-content:center;gap:3px;line-height:1;}
.shift {font-size:14px;font-weight:900;text-shadow:0 0 2px currentColor;}
.holiday {color:#ef4444;}.badges {min-height:12px;display:flex;align-items:center;justify-content:center;gap:3px;line-height:1;}
.badge {display:inline-flex;align-items:center;justify-content:center;flex:0 0 12px;width:12px;height:12px;border-radius:4px;font-size:9px;font-weight:900;line-height:1;}
.mark {color:#f5d0fe;background:rgba(168,85,247,.26);border:.5px solid rgba(216,180,254,.55);}
.site {color:#fef3c7;background:rgba(245,158,11,.24);border:.5px solid rgba(251,191,36,.55);}
.badge.overtime {font-size:12px;}
.today {position:absolute;right:4px;top:4px;width:6px;height:6px;border-radius:50%;background:#fb923c;box-shadow:0 0 0 1px rgba(251,146,60,.25);}

.editor {margin-top:14px;}.editor[hidden] {display:none;}.title {font-size:16px;font-weight:750;margin:0 0 10px;color:#e2e8f0;}
form {padding:14px;border:1px solid rgba(148,163,184,.22);border-radius:12px;}
.fields {display:grid;grid-template-columns:minmax(0,.85fr) minmax(0,1.25fr) minmax(0,1.1fr) minmax(0,1.35fr);gap:6px;align-items:end;}
label {min-width:0;display:flex;flex-direction:column;gap:7px;color:#94a3b8;font-size:9px;}
label.check {min-height:38px;flex-direction:row;align-items:center;gap:5px;color:#e2e8f0;font-size:10px;}
input[type=checkbox] {width:15px;height:15px;margin:0;accent-color:#7da3c3;}
select {min-width:0;width:100%;height:38px;padding:0 3px;color:#e2e8f0;background:#252d3b;border:1px solid rgba(148,163,184,.35);border-radius:9px;font-size:11px;}
select:disabled {opacity:.45;}.actions {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:16px;}
.actions button {min-width:0;min-height:38px;border-radius:11px;font-size:12px;font-weight:650;cursor:pointer;}
.save {background:rgba(66,105,139,.36);border:1px solid rgba(139,177,207,.40);color:#dbeafe;}.save:hover {background:rgba(66,105,139,.48);}
.close {background:rgba(255,255,255,.045);border:1px solid rgba(148,163,184,.22);color:#cbd5e1;}.actions button:disabled {opacity:.45;cursor:wait;}
"""

HTML = """
<div class="grid" role="group" aria-label="Giorni del mese"></div>
<section class="editor" hidden aria-label="Modifica giorno">
  <h3 class="title"></h3>
  <form>
    <div class="fields">
      <label class="check"><input name="holiday" type="checkbox">Festivo</label>
      <label>STRAORDINARIO<select name="overtime" aria-label="Straordinario"></select></label>
      <label>PERMESSO<select name="leave" aria-label="Permesso"></select></label>
      <label>POSIZIONE<select name="position" aria-label="Posizione permesso"><option value="end">Fine</option><option value="start">Inizio</option></select></label>
    </div>
    <div class="actions"><button class="save" type="submit">Salva giorno</button><button class="close" type="button">Chiudi</button></div>
  </form>
</section>
"""

JS = """
export default function({data, parentElement, setTriggerValue}) {
  const root = parentElement;
  const grid = root.querySelector('.grid');
  const editor = root.querySelector('.editor');
  const title = root.querySelector('.title');
  const form = root.querySelector('form');
  const save = root.querySelector('.save');
  const close = root.querySelector('.close');
  const fields = form.elements;
  const drafts = new Map();
  const days = new Map(data.days.filter(Boolean).map(day => [day.date, day]));
  let selected = null;
  let saving = false;
  grid.replaceChildren();
  editor.hidden = true;
  function duration(n) { return n === 0 ? 'No' : (n < 60 ? `${n}m` : `${Math.floor(n/60)}h${n%60 ? `${n%60}m` : ''}`); }
  for (const [name, options] of [['overtime',[0,30,45,60,75,90,105,120]],['leave',Array.from({length:17},(_,i)=>i*15)]]) {
    fields[name].replaceChildren(...options.map(n => new Option(duration(n), String(n))));
  }
  function values() {
    return {holiday:fields.holiday.checked, overtime:Number(fields.overtime.value),
            leave:Number(fields.leave.value), at_start:fields.position.value === 'start'};
  }
  function remember() { if (selected) drafts.set(selected, values()); }
  function selectDay(date) {
    if (saving) return;
    remember();
    selected = selected === date ? null : date;
    for (const button of grid.querySelectorAll('[data-date]')) button.setAttribute('aria-pressed', String(button.dataset.date === selected));
    editor.hidden = selected === null;
    if (!selected) return;
    const day = days.get(selected);
    const draft = drafts.get(selected) || day;
    title.textContent = `Modifica giorno · ${day.label}${day.shift ? ' · ' + day.shift : ''}`;
    fields.holiday.checked = draft.holiday;
    fields.overtime.value = String(day.allowed ? draft.overtime : 0);
    fields.leave.value = String(day.allowed ? draft.leave : 0);
    fields.position.value = draft.at_start ? 'start' : 'end';
    for (const name of ['overtime','leave','position']) fields[name].disabled = !day.allowed;
  }
  for (const name of ['L','M','M','G','V','S','D']) {
    const head = document.createElement('div'); head.className='head'; head.textContent=name; grid.appendChild(head);
  }
  for (const day of data.days) {
    if (!day) {const empty=document.createElement('div');empty.className='day empty';grid.appendChild(empty);continue;}
    const button = document.createElement('button');
    button.type='button';button.className='day';button.dataset.date=day.date;
    button.setAttribute('aria-label', `${day.label}${day.shift ? ' ' + day.shift : ''}`);
    button.setAttribute('aria-pressed','false');
    const main = document.createElement('span');main.className='day-main';button.appendChild(main);
    const number = document.createElement('span');number.textContent=String(day.number);number.className=day.festive ? 'holiday' : '';main.appendChild(number);
    if (day.short) {const shift=document.createElement('span');shift.textContent=day.short;shift.className='shift';shift.style.color=day.color;main.appendChild(shift);}
    const badges = document.createElement('span');badges.className='badges';
    for (const [show,text,kind,label] of [[day.leave>0,'p','mark','Permesso'],[day.overtime>0,'+','mark overtime','Straordinario'],[day.onsite,'S','site','Sede']]) {
      if (show) {const mark=document.createElement('span');mark.textContent=text;mark.className='badge '+kind;mark.title=label;mark.setAttribute('aria-label',label);badges.appendChild(mark);}
    }
    button.appendChild(badges);
    if (day.today) {const dot=document.createElement('span');dot.className='today';dot.title='Giorno corrente';dot.setAttribute('aria-label','Giorno corrente');button.appendChild(dot);}
    button.onclick=()=>selectDay(day.date);grid.appendChild(button);
  }
  close.onclick=()=>selectDay(selected);
  form.onsubmit=event=>{
    event.preventDefault();
    if (saving || !selected) return;
    saving=true;for (const control of form.elements) control.disabled=true;save.textContent='Salvataggio…';
    setTriggerValue('save', {date:selected,...values(),request_id:crypto.randomUUID()});
  };
  return ()=>{form.onsubmit=null;close.onclick=null;};
}
"""

def render_mobile_shift_calendar(days, key, revision=""):
    # Identical definitions register idempotently. Register in the active
    # runtime rather than retaining a renderer from an earlier server runtime.
    renderer = component("mobile_shift_calendar", html=HTML, css=CSS, js=JS)
    return renderer(data={"days": days, "revision": revision}, key=key,
                    on_save_change=lambda: None)


def validate_day_save(payload, allowed_dates):
    """Never trust browser-provided dates, booleans, or duration limits."""
    if not isinstance(payload, dict) or not isinstance(payload.get("date"), str) or payload["date"] not in allowed_dates:
        raise ValueError("Giorno non valido.")
    if not isinstance(payload.get("request_id"), str) or not payload["request_id"] or len(payload["request_id"]) > 100:
        raise ValueError("Richiesta non valida.")
    for name in ("holiday", "at_start"):
        if type(payload.get(name)) is not bool:
            raise ValueError("Opzione non valida.")
    overtime, leave = payload.get("overtime"), payload.get("leave")
    if type(overtime) is not int or overtime not in (0,30,45,60,75,90,105,120):
        raise ValueError("Straordinario non valido.")
    if type(leave) is not int or leave not in range(0,241,15):
        raise ValueError("Permesso non valido.")
    return payload
