"""User-entered annual bonuses and welfare, kept separate from payroll totals."""
from copy import deepcopy
from html import escape
import math
import streamlit as st

SHEET = 'Premi e welfare'
HEADERS = ['ID', 'Anno', 'Tipo', 'Mese erogazione', 'Importo', 'Tipo importo', 'Stato', 'Note', 'Quota Leonardo', 'Residuo speso', 'Versione']
TYPES = {'production': 'Premio di produzione', 'welfare': 'Welfare contrattuale'}
AMOUNT_TYPES = ['Da confermare', 'Lordo', 'Netto', 'Credito welfare']
STATES = ['Confermato', 'Dichiarato', 'Indicativo', 'Previsto']
MONTHS = ['Gennaio','Febbraio','Marzo','Aprile','Maggio','Giugno','Luglio','Agosto','Settembre','Ottobre','Novembre','Dicembre']


def default_row(year, kind):
    amount = ({2025: 2800., 2026: 3500.}.get(year) if kind == 'production' else 250. if year >= 2026 else None)
    return {'ID': f'{kind}-{year}', 'Anno': year, 'Tipo': kind, 'Mese erogazione': 7 if kind == 'production' else 2,
            'Importo': amount, 'Tipo importo': 'Netto' if kind == 'production' and year in {2025,2026} else 'Da confermare' if kind == 'production' else 'Credito welfare',
            'Stato': 'Confermato' if kind == 'production' and year in {2025,2026} else 'Dichiarato' if year <= 2026 else 'Previsto',
            'Note': '', 'Quota Leonardo': 2000. if kind == 'production' and year in {2025,2026} else None,
            'Residuo speso': kind == 'production' and year in {2025,2026}, 'Versione': 2}


def initial_rows():
    return [default_row(2025, 'production'), default_row(2026, 'production'), default_row(2026, 'welfare')]


def validate_row(row):
    item = deepcopy(row)
    kind = item['Tipo']
    if kind not in TYPES:
        raise ValueError('Tipo di voce non valido.')
    year, month = int(item['Anno']), int(item['Mese erogazione'])
    if float(item['Anno']) != year or not 1980 <= year <= 2100 or float(item['Mese erogazione']) != month or not 1 <= month <= 12:
        raise ValueError('Controlla anno e mese.')
    amount = item.get('Importo')
    amount = None if amount is None or amount == '' else float(amount)
    if amount is not None and (not math.isfinite(amount) or amount < 0):
        raise ValueError('Inserisci un importo positivo o lascia vuoto.')
    if item.get('Tipo importo') not in AMOUNT_TYPES or item.get('Stato') not in STATES:
        raise ValueError('Controlla tipo di importo e stato.')
    allocated = item.get('Quota Leonardo')
    allocated = None if allocated is None or allocated == '' else float(allocated)
    if allocated is not None and (not math.isfinite(allocated) or allocated < 0 or (amount is not None and allocated > amount)):
        raise ValueError('La quota Leonardo deve essere compresa tra zero e il premio.')
    spent = item.get('Residuo speso', False)
    if spent in ('', None):spent = False
    if isinstance(spent, str) and spent.upper() in {'TRUE','FALSE'}:spent = spent.upper() == 'TRUE'
    if not isinstance(spent, bool):raise ValueError('Controlla lo stato del residuo del premio.')
    item['Mese erogazione'] = month
    item.update(ID=f'{kind}-{year}', Anno=year, Importo=round(amount, 2) if amount is not None else None,
                Note=str(item.get('Note') or '')[:500], Versione=2)
    item['Quota Leonardo'] = round(allocated, 2) if allocated is not None else None
    item['Residuo speso'] = spent
    return {key: item[key] for key in HEADERS}


def decode_rows(rows):
    if not rows:
        return initial_rows()
    result, seen = [], set()
    for row in rows:
        if not any(str(value).strip() for value in row.values()):
            continue
        row = deepcopy(row)
        if int(row.get('Versione') or 0) < 2 and row.get('Tipo') == 'production' and int(row['Anno']) in {2025,2026}:
            default = default_row(int(row['Anno']), 'production')
            row.update({key: default[key] for key in ['Importo','Tipo importo','Stato','Quota Leonardo','Residuo speso']})
            if row.get('Note') == 'Importo 2025 da verificare':row['Note'] = ''
        item = validate_row(row)
        if item['ID'] != row.get('ID') or item['ID'] in seen:
            raise ValueError('Il registro premi contiene una voce non valida o duplicata.')
        seen.add(item['ID'])
        result.append(item)
    return result


def for_year(rows, year, kind):
    return deepcopy(next((row for row in rows if row['Anno'] == year and row['Tipo'] == kind), default_row(year, kind)))


def merge_rows(rows, replacements):
    validated = [validate_row(row) for row in replacements]
    replacements = {row['ID']: row for row in validated}
    result = [deepcopy(replacements.pop(row['ID'], row)) for row in rows]
    return result+list(replacements.values())


def money(amount):
    return '—' if amount is None else f'€{amount:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def card_html(row):
    detail = f"{MONTHS[row['Mese erogazione']-1]} · {row['Tipo importo']} · {row['Stato'].lower()}"
    note = f'<div class="salary-extra-note">{escape(row["Note"])}</div>' if row.get('Note') else ''
    if row['Tipo'] == 'production' and row.get('Quota Leonardo') is not None:
        note += f'<div class="salary-extra-note">Azioni Leonardo: {money(row["Quota Leonardo"])}</div>'
        if row['Importo'] is not None:
            label = 'Speso' if row.get('Residuo speso') else 'Residuo'
            note += f'<div class="salary-extra-note">{label}: {money(row["Importo"]-row["Quota Leonardo"])}</div>'
    return f'<div class="salary-extra-card"><strong>{TYPES[row["Tipo"]]}</strong><div class="salary-extra-value">{money(row["Importo"])}</div><div class="salary-extra-note">{escape(detail)}</div>{note}</div>'


@st.fragment
def render_history(rows, save_rows, saved, today):
    if rows is None:
        st.info('Premi e welfare non disponibili. Riprova il caricamento prima di modificarli.')
        return
    with st.container(key='salary-extra-history'):
        st.html('''<style>
        .st-key-salary-extra-history {margin-top:8px;}
        .salary-extra-grid {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;}
        .salary-extra-card {padding:12px;border-radius:14px;background:rgba(255,255,255,.05);border:.5px solid rgba(255,255,255,.12);color:#cbd5e1;font-size:12px;min-width:0;overflow-wrap:anywhere;}
        .salary-extra-value {color:#fbbf24;font:18px monospace;margin:10px 0;}
        .salary-extra-note {color:#94a3b8;font-size:11px;line-height:1.5;margin-top:6px;}
        .st-key-salary-extra-history [data-testid="stCaptionContainer"] {color:#94a3b8;}
        .st-key-salary-extra-history [data-testid="stWidgetLabel"] p {white-space:normal!important;color:#cbd5e1!important;}
        </style>''')
        title, picker = st.columns([1.5, 1], vertical_alignment='center')
        with title:st.markdown('**Premio e welfare**')
        years = sorted({row['Anno'] for row in rows}|{today.year, today.year+1}, reverse=True)
        with picker:year = st.selectbox('Anno premio e welfare', years, index=years.index(today.year), key='salary_extra_year', label_visibility='collapsed')
        annual = [for_year(rows, year, kind) for kind in TYPES]
        st.html('<div class="salary-extra-grid">'+''.join(card_html(row) for row in annual)+'</div>')
        st.caption('Premi e welfare restano separati dagli stipendi e dalle loro medie. La quota Leonardo è parte del premio, già investita.')
        with st.expander('Modifica premio e welfare'):
            with st.form(f'salary_extras_{year}', border=False, enter_to_submit=False):
                edits = []
                for old in annual:
                    kind = old['Tipo']
                    item = deepcopy(old)
                    st.markdown('**'+TYPES[kind]+'**')
                    amount_col, month_col = st.columns(2)
                    with amount_col:item['Importo'] = st.number_input('Importo (€)', min_value=0., value=old['Importo'], step=50., key=f'extra_{kind}_{year}_amount')
                    with month_col:item['Mese erogazione'] = st.selectbox('Mese erogazione', list(range(1,13)), index=old['Mese erogazione']-1, format_func=lambda month: MONTHS[month-1], key=f'extra_{kind}_{year}_month')
                    item['Tipo importo'] = st.selectbox('Tipo di importo', AMOUNT_TYPES, index=AMOUNT_TYPES.index(old['Tipo importo']), key=f'extra_{kind}_{year}_type')
                    item['Stato'] = st.selectbox('Stato', STATES, index=STATES.index(old['Stato']), key=f'extra_{kind}_{year}_state')
                    if kind == 'production':
                        item['Quota Leonardo'] = st.number_input('Destinato ad azioni Leonardo (€)', min_value=0., value=old.get('Quota Leonardo'), step=100., key=f'extra_{kind}_{year}_leonardo')
                        item['Residuo speso'] = st.checkbox('Residuo già speso', value=old.get('Residuo speso', False), key=f'extra_{kind}_{year}_spent')
                    item['Note'] = st.text_input('Nota', value=old['Note'], max_chars=500, key=f'extra_{kind}_{year}_note')
                    edits.append(item)
                submit = st.form_submit_button('💾 Salva premio e welfare', type='primary', use_container_width=True)
            if submit:
                try:
                    edits = [validate_row(row) for row in edits]
                    if save_rows(edits):saved('Premio e welfare salvati.')
                except (ValueError, TypeError, KeyError) as exc:
                    st.error(str(exc))
