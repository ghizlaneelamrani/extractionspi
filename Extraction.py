import streamlit as st
import pdfplumber
import re
import pandas as pd
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed

st.set_page_config(page_title="SPI PDF Extractor V7", layout="wide")
st.title("📄 SPI PDF Extractor - V7")

def norm(t):
    return re.sub(r'\s+', ' ', str(t or '').lower().strip())

def extract_email(t):
    m = re.search(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', t or '')
    return m.group(0) if m else ''

def extract_phone(t):
    for pat in [r'\+\d[\d\s\-().]{7,20}\d', r'\d{3}[\s\-]\d{3}[\s\-]\d{4}',
                r'\(\d{3,4}\)\s*\d{3,4}[\s\-]\d{3,4}', r'\d{8,15}']:
        m = re.search(pat, t or '')
        if m:
            c = re.sub(r'[^\d\+]', '', m.group(0))
            if len(c) >= 7:
                return c
    return ''

def extract_dim(t):
    m = re.search(r'(\d+[\.,]?\d*)\s*[*xX×]\s*(\d+[\.,]?\d*)\s*[*xX×]\s*(\d+[\.,]?\d*)', t or '')
    return f"{m.group(1)} x {m.group(2)} x {m.group(3)}" if m else ''

def extract_num(t):
    m = re.search(r'\d+[\.,]?\d*', t or '')
    return m.group(0) if m else ''

def extract_date(t):
    for pat in [r'\d{2}[./-]\d{2}[./-]\d{4}', r'\d{4}[./-]\d{2}[./-]\d{2}', r'\d{2}[./-]\d{2}[./-]\d{2}']:
        m = re.search(pat, t or '')
        if m:
            return m.group(0)
    return ''

def clean_name(t):
    if not t:
        return ''
    t = re.sub(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', '', t)
    t = re.sub(r'\+?\d[\d\s\-().]{6,}\d', '', t)
    t = re.sub(r'(tel|email|phone)[.:]?\s*', '', t, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', t).strip().strip(',;:-')

def extract_part_number_from_page(page_words):
    line_map = {}
    for w in page_words:
        yk = round(float(w['top']) / 2) * 2
        line_map.setdefault(yk, []).append(w)

    sorted_ys = sorted(line_map.keys())
    label_bottom = None
    for yk in sorted_ys:
        line_text = ' '.join(w['text'] for w in line_map[yk]).lower()
        if 'part' in line_text and 'number' in line_text:
            label_bottom = max(float(w['bottom']) for w in line_map[yk])
            break

    if label_bottom is None:
        return ''

    for yk in sorted_ys:
        if float(yk) <= label_bottom:
            continue
        line_words = sorted(line_map[yk], key=lambda w: float(w['x0']))
        for w in line_words:
            candidate = w['text'].strip()
            cleaned   = re.sub(r'[_\-]', '', candidate)
            m = re.search(r'\d{7,9}', cleaned)
            if m:
                return m.group(0)
        break

    return ''

def value_below_label(page_words, label_keywords, max_gap=40, multi_line=False):
    line_map = {}
    for w in page_words:
        yk = round(float(w['top']) / 2) * 2
        line_map.setdefault(yk, []).append(w)
    sorted_ys = sorted(line_map.keys())

    label_bottom = None
    for yk in sorted_ys:
        line_text = ' '.join(w['text'] for w in line_map[yk]).lower()
        if all(kw in line_text for kw in label_keywords):
            label_bottom = max(float(w['bottom']) for w in line_map[yk])
            break

    if label_bottom is None:
        return ''

    result_lines = []
    for yk in sorted_ys:
        if float(yk) <= label_bottom:
            continue
        if float(yk) > label_bottom + max_gap and not result_lines:
            break
        if float(yk) > label_bottom + max_gap:
            break
        line_words = sorted(line_map[yk], key=lambda w: float(w['x0']))
        line_text  = ' '.join(w['text'] for w in line_words).strip()
        if not line_text:
            continue
        result_lines.append(line_text)
        if not multi_line:
            break

    return ' '.join(result_lines)

def _build_line_map(words):
    lm = {}
    for w in words:
        yk = round(float(w['top']) / 2) * 2
        lm.setdefault(yk, []).append(w)
    return lm

def _line_text_at_y(lm, yk):
    return ' '.join(w['text'] for w in sorted(lm.get(yk, []), key=lambda w: float(w['x0'])))

def _find_all_label_bottoms(lm, sorted_ys, keywords):
    results = []
    for yk in sorted_ys:
        lt = _line_text_at_y(lm, yk).lower()
        if all(kw in lt for kw in keywords):
            bottom = max(float(w['bottom']) for w in lm[yk])
            results.append((yk, bottom))
    return results

def _extract_contact_block(lm, sorted_ys, label_bottom, next_label_y=None, max_gap=120):
    upper_bound = label_bottom + max_gap
    if next_label_y is not None:
        upper_bound = min(upper_bound, float(next_label_y) - 2)

    name = email = phone = ''
    for yk in sorted_ys:
        y = float(yk)
        if y <= label_bottom:
            continue
        if y > upper_bound:
            break
        lt = _line_text_at_y(lm, yk)
        if not lt.strip():
            continue
        if not email:
            em = extract_email(lt)
            if em:
                email = em
        if not phone:
            ph = extract_phone(lt)
            if ph:
                digits_only = re.sub(r'[^\d]', '', ph)
                has_plus = '+' in lt
                long_enough = len(digits_only) >= 10
                if has_plus or long_enough:
                    phone = ph
        if not name:
            candidate = clean_name(lt)
            if candidate and not re.match(r'^[\d\+\s\-().]+$', candidate) and '@' not in candidate:
                name = candidate

    return name, email, phone

TABLE_SETTINGS = [
    {"vertical_strategy": "lines", "horizontal_strategy": "lines",
     "intersection_tolerance": 5,  "snap_tolerance": 4},
    {"vertical_strategy": "lines", "horizontal_strategy": "lines",
     "intersection_tolerance": 10, "snap_tolerance": 8},
    {"vertical_strategy": "text",  "horizontal_strategy": "lines",
     "intersection_tolerance": 10, "snap_tolerance": 8},
    {"vertical_strategy": "text",  "horizontal_strategy": "text",
     "intersection_tolerance": 10, "snap_tolerance": 8},
]

def _cell_title_value(page, bbox, raw):
    if bbox:
        try:
            x0, top, x1, bottom = bbox
            h = bottom - top
            if h > 6:
                words = page.within_bbox((x0, top, x1, bottom)).extract_words(
                    keep_blank_chars=False, x_tolerance=4, y_tolerance=3)
                if words:
                    lmap = {}
                    for w in words:
                        yk = round((top + float(w['top'])) / 3) * 3
                        lmap.setdefault(yk, []).append(w['text'])
                    ys = sorted(lmap)
                    lines = [' '.join(lmap[y]) for y in ys]
                    if len(lines) >= 2:
                        split = top + h * 0.42
                        above = [' '.join(lmap[y]) for y in ys if y <= split]
                        below = [' '.join(lmap[y]) for y in ys if y > split]
                        if above and below:
                            return ' '.join(above), ' '.join(below)
                        return lines[0], ' '.join(lines[1:])
                    if lines:
                        return lines[0], ''
        except Exception:
            pass
    parts = [l.strip() for l in raw.split('\n') if l.strip()]
    if len(parts) >= 2:
        return parts[0], ' '.join(parts[1:])
    return (parts[0] if parts else ''), ''

def get_cells(page):
    for settings in TABLE_SETTINGS:
        try:
            tobjs  = page.find_tables(settings)
            tdatas = page.extract_tables(settings)
            if not tdatas:
                continue
            cells = []
            for ti, (tobj, tdata) in enumerate(zip(tobjs, tdatas)):
                bboxes = getattr(tobj, 'cells', [])
                fi = 0
                for ri, row in enumerate(tdata):
                    for ci, cell_text in enumerate(row):
                        bbox = bboxes[fi] if fi < len(bboxes) else None
                        raw  = (cell_text or '').strip()
                        title, value = _cell_title_value(page, bbox, raw) if raw else ('', '')
                        cells.append(dict(title=title, value=value, raw=raw,
                                          row=ri, col=ci, table=ti, bbox=bbox))
                        fi += 1
            if sum(1 for c in cells if c['raw']) >= 5:
                return cells
        except Exception:
            continue
    return []

def extract_page(page, filename, page_number):
    cells = get_cells(page)
    cells = sorted(cells, key=lambda c: (c['table'], c['row'], c['col']))
    page_words = page.extract_words(keep_blank_chars=False, x_tolerance=4, y_tolerance=3)

    data = {k: '' for k in [
        'File', 'Page', 'Part Number', 'Supplier Company Name',
        'Shipping Address', 'Manufacturing Address',
        'Scheduling Contact Name', 'Scheduling Email', 'Scheduling Phone',
        'Packaging Contact Name',  'Packaging Email',  'Packaging Phone',
        'Carton Description', 'Carton LWH (mm)',
        'Wood Pallet LWH (mm)', 'Wood Pallet Tare WT (kg)',
        'Standard Pack Quantity', 'No. Primary Containers/Layer',
        'No. Layers on Secondary Container',
        'Pallet Stackability', 'Rustproofing Method', 'Primary Box Handles',
        'Part Weight (kg)', 'Primary Cont Gross Weight (kg)',
        'Secondary Cont Gross Weight (kg)', 'Method to Secure Load',
    ]}
    data['File'] = filename
    data['Page'] = page_number

    idx = {(c['table'], c['row'], c['col']): c for c in cells}

    carton_row = carton_table = pallet_row = pallet_table = None
    for c in cells:
        if c['col'] != 0:
            continue
        rv = norm(c['value'] or c['raw'])
        rt = norm(c['title'])
        if 'carton' in rv and carton_row is None:
            carton_row   = c['row']; carton_table = c['table']
        if ('wood pallet' in rv or 'wooden pallet' in rv) and pallet_row is None:
            pallet_row   = c['row']; pallet_table = c['table']
        if 'carton' in rt and carton_row is None:
            carton_row   = c['row']; carton_table = c['table']
        if ('wood pallet' in rt or 'wooden pallet' in rt) and pallet_row is None:
            pallet_row   = c['row']; pallet_table = c['table']

    def find_col_by_title_keyword(table_idx, keyword):
        for c in cells:
            if c['table'] == table_idx and keyword in norm(c['title']):
                return c['col']
        return None

    if carton_table is not None:
        lwh_col  = find_col_by_title_keyword(carton_table, 'lwh')
        tare_col = find_col_by_title_keyword(carton_table, 'tare wt')

        if lwh_col is not None and carton_row is not None:
            c = idx.get((carton_table, carton_row, lwh_col))
            if c:
                data['Carton LWH (mm)'] = extract_dim(c['value']) or extract_dim(c['raw'])

        if lwh_col is not None and pallet_row is not None:
            c = idx.get((pallet_table, pallet_row, lwh_col))
            if c:
                data['Wood Pallet LWH (mm)'] = extract_dim(c['value']) or extract_dim(c['raw'])

        if tare_col is not None and pallet_row is not None:
            c = idx.get((pallet_table, pallet_row, tare_col))
            if c:
                data['Wood Pallet Tare WT (kg)'] = extract_num(c['value']) or extract_num(c['raw'])

    desc_col = find_col_by_title_keyword(carton_table, 'description') if carton_table is not None else None
    if desc_col is not None and carton_row is not None:
        c = idx.get((carton_table, carton_row, desc_col))
        if c:
            v = c['value'] or c['raw']
            v = re.sub(r'description\s*/?\s*(item\s*#?)?\s*', '', v, flags=re.IGNORECASE).strip()
            data['Carton Description'] = v

    for c in cells:
        t   = norm(c['title'])
        val = c['value'].strip()
        raw = c['raw'].strip()
        v   = val or raw

        if 'supplier company name' in t and not data['Supplier Company Name']:
            data['Supplier Company Name'] = v
        if 'shipping address' in t and not data['Shipping Address']:
            data['Shipping Address'] = v
        if 'manufacturing address' in t and not data['Manufacturing Address']:
            data['Manufacturing Address'] = v
        if 'part number' in t and not data['Part Number']:
            candidate = val or raw
            cleaned = re.sub(r'[_\-]', '', candidate)
            m = re.search(r'\d{7,9}', cleaned)
            if m:
                data['Part Number'] = m.group(0)

        if t in ('standard pack quantity (per primary container)', 'standard pack quantity'):
            if not data['Standard Pack Quantity']:
                data['Standard Pack Quantity'] = extract_num(v)

        if ('no. of primary containers/layer' == t or
                'no. primary containers/layer' == t or
                ('no.' in t and 'primary containers' in t and 'layer' in t)):
            if not data['No. Primary Containers/Layer']:
                data['No. Primary Containers/Layer'] = extract_num(v)

        if ('no. of layers on/in secondary container' == t or
                'no. layers on secondary container' == t or
                ('no.' in t and 'layers' in t and 'secondary' in t)):
            if not data['No. Layers on Secondary Container']:
                data['No. Layers on Secondary Container'] = extract_num(v)

        if 'pallet stackability' in t and not data['Pallet Stackability']:
            data['Pallet Stackability'] = extract_num(v) or v
        if 'rustproofing' in t and not data['Rustproofing Method']:
            data['Rustproofing Method'] = v
        if 'primary box have handles' in t and not data['Primary Box Handles']:
            data['Primary Box Handles'] = v
        if t == 'part weight (kg)' and not data['Part Weight (kg)']:
            data['Part Weight (kg)'] = extract_num(v)
        if 'primary cont gross weight' in t and not data['Primary Cont Gross Weight (kg)']:
            data['Primary Cont Gross Weight (kg)'] = extract_num(v)
        if 'secondary cont gross weight' in t and not data['Secondary Cont Gross Weight (kg)']:
            data['Secondary Cont Gross Weight (kg)'] = extract_num(v)
        if 'method to secure load' in t and not data['Method to Secure Load']:
            data['Method to Secure Load'] = v

    lm = _build_line_map(page_words)
    sys_ys = sorted(lm.keys())

    sched_labels = _find_all_label_bottoms(lm, sys_ys, ['scheduling', 'contact'])
    pack_labels  = _find_all_label_bottoms(lm, sys_ys, ['packaging', 'contact'])

    sched_labels.sort(key=lambda x: x[0])
    pack_labels.sort(key=lambda x: x[0])

    if sched_labels:
        _, sched_bottom = sched_labels[0]
        next_yk = None
        if len(sched_labels) > 1:
            next_yk = sched_labels[1][0]
        if pack_labels:
            candidates = [p[0] for p in pack_labels if float(p[0]) > sched_bottom]
            if candidates:
                nxt = min(candidates)
                next_yk = min(next_yk, nxt) if next_yk else nxt

        sname, semail, sphone = _extract_contact_block(lm, sys_ys, sched_bottom, next_yk, max_gap=120)
        if sname and not data['Scheduling Contact Name']:
            data['Scheduling Contact Name'] = sname
        if semail and not data['Scheduling Email']:
            data['Scheduling Email'] = semail
        if sphone and not data['Scheduling Phone']:
            data['Scheduling Phone'] = sphone

    if pack_labels:
        _, pack_bottom = pack_labels[0]
        next_yk = None
        if len(pack_labels) > 1:
            next_yk = pack_labels[1][0]
        if sched_labels:
            candidates = [s[0] for s in sched_labels if float(s[0]) > pack_bottom]
            if candidates:
                nxt = min(candidates)
                next_yk = min(next_yk, nxt) if next_yk else nxt

        pname, pemail, pphone = _extract_contact_block(lm, sys_ys, pack_bottom, next_yk, max_gap=120)
        if pname and not data['Packaging Contact Name']:
            data['Packaging Contact Name'] = pname
        if pemail and not data['Packaging Email']:
            data['Packaging Email'] = pemail
        if pphone and not data['Packaging Phone']:
            data['Packaging Phone'] = pphone

    if not data['Manufacturing Address']:
        v = value_below_label(page_words, ['manufacturing', 'address'], max_gap=40, multi_line=True)
        if v:
            data['Manufacturing Address'] = v

    if not data['Part Number']:
        pn = extract_part_number_from_page(page_words)
        if pn:
            data['Part Number'] = pn

    text = page.extract_text() or ''
    if text:
        data = _fallback(text, data)

    return data


def _fallback(text, data):
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    def nxt(i):
        for j in range(i + 1, min(i + 3, len(lines))):
            if lines[j].strip():
                return lines[j].strip()
        return ''

    lwh_list = []
    tare_list = []

    for i, line in enumerate(lines):
        ln = norm(line)

        if 'supplier company name' in ln and not data['Supplier Company Name']:
            data['Supplier Company Name'] = nxt(i)
        if 'shipping address' in ln and not data['Shipping Address']:
            data['Shipping Address'] = nxt(i)
        if 'manufacturing address' in ln and not data['Manufacturing Address']:
            data['Manufacturing Address'] = nxt(i)

        if 'part number' in ln and not data['Part Number']:
            nv = nxt(i)
            cleaned = re.sub(r'[_\-]', '', nv)
            m = re.search(r'\d{7,9}', cleaned)
            if m:
                data['Part Number'] = m.group(0)

        if 'supplier scheduling contact' in ln and not data['Scheduling Contact Name']:
            nv = nxt(i)
            name = clean_name(nv)
            em   = extract_email(nv)
            ph   = extract_phone(nv)
            if name:
                data['Scheduling Contact Name'] = name
            if em and not data['Scheduling Email']:
                data['Scheduling Email'] = em
            if ph and not data['Scheduling Phone']:
                data['Scheduling Phone'] = ph

        if ('supplier packaging contact' in ln or 'packaging contact name' in ln) and not data['Packaging Contact Name']:
            nv = nxt(i)
            name = clean_name(nv)
            em   = extract_email(nv)
            ph   = extract_phone(nv)
            if name:
                data['Packaging Contact Name'] = name
            if em and not data['Packaging Email']:
                data['Packaging Email'] = em
            if ph and not data['Packaging Phone']:
                data['Packaging Phone'] = ph

        if 'lwh' in ln:
            d = extract_dim(nxt(i)) or extract_dim(line)
            if d:
                lwh_list.append(d)

        if 'tare wt' in ln:
            n = extract_num(nxt(i))
            if n:
                tare_list.append(n)

        if ln in ('standard pack quantity (per primary container)', 'standard pack quantity'):
            if not data['Standard Pack Quantity']:
                data['Standard Pack Quantity'] = extract_num(nxt(i))

        if 'no.' in ln and 'primary containers' in ln and 'layer' in ln:
            if not data['No. Primary Containers/Layer']:
                data['No. Primary Containers/Layer'] = extract_num(nxt(i))

        if 'no.' in ln and 'layers' in ln and 'secondary' in ln:
            if not data['No. Layers on Secondary Container']:
                data['No. Layers on Secondary Container'] = extract_num(nxt(i))

        if 'pallet stackability' in ln and not data['Pallet Stackability']:
            data['Pallet Stackability'] = extract_num(nxt(i))

        if ln == 'part weight (kg)' and not data['Part Weight (kg)']:
            data['Part Weight (kg)'] = extract_num(nxt(i))

        if 'primary cont gross weight' in ln and not data['Primary Cont Gross Weight (kg)']:
            data['Primary Cont Gross Weight (kg)'] = extract_num(nxt(i))

        if 'secondary cont gross weight' in ln and not data['Secondary Cont Gross Weight (kg)']:
            data['Secondary Cont Gross Weight (kg)'] = extract_num(nxt(i))

        if 'method to secure load' in ln and not data['Method to Secure Load']:
            data['Method to Secure Load'] = nxt(i)

    if lwh_list:
        if not data['Carton LWH (mm)']:
            data['Carton LWH (mm)'] = lwh_list[0]
        if len(lwh_list) >= 2 and not data['Wood Pallet LWH (mm)']:
            last = lwh_list[-1]
            if last != data['Carton LWH (mm)']:
                data['Wood Pallet LWH (mm)'] = last
    if tare_list and not data['Wood Pallet Tare WT (kg)']:
        data['Wood Pallet Tare WT (kg)'] = tare_list[-1]

    return data


def extract_pdf(file):
    results = []
    try:
        with pdfplumber.open(file) as pdf:
            for pn, page in enumerate(pdf.pages, 1):
                try:
                    d = extract_page(page, file.name, pn)
                    if any(v for k, v in d.items() if k not in ('File', 'Page')):
                        results.append(d)
                except Exception as e:
                    st.warning(f"⚠️ {file.name} p{pn}: {e}")
    except Exception as e:
        st.error(f"❌ {file.name}: {e}")
    return results


COLS = [
    'File', 'Page', 'Part Number',
    'Shipping Address', 'Manufacturing Address',
    'Scheduling Contact Name', 'Scheduling Email', 'Scheduling Phone',
    'Packaging Contact Name',  'Packaging Email',  'Packaging Phone',
    'Carton LWH (mm)',
    'Wood Pallet LWH (mm)',
    'Standard Pack Quantity', 'No. Primary Containers/Layer',
    'No. Layers on Secondary Container',
]

for k, v in [('cache', []), ('results', {}), ('desel', set())]:
    if k not in st.session_state:
        st.session_state[k] = v

def reset():
    st.session_state.cache   = []
    st.session_state.results = {}
    st.session_state.desel   = set()

c1, c2 = st.columns([6, 1])
with c2:
    st.button('🔄 Réinitialiser', on_click=reset, use_container_width=True)

files = st.file_uploader('Upload SPI PDFs', type='pdf', accept_multiple_files=True)

if files:
    existing = {f.name for f in st.session_state.cache}
    new = [f for f in files if f.name not in existing]
    if new:
        prog = st.progress(0, text=f'0 / {len(new)} fichiers...')
        done = [0]
        res_map = {}
        with ThreadPoolExecutor(max_workers=min(8, len(new))) as ex:
            fmap = {ex.submit(extract_pdf, f): f for f in new}
            for fut in as_completed(fmap):
                f = fmap[fut]
                try:
                    rows = fut.result()
                except Exception:
                    rows = []
                res_map[f.name] = rows
                done[0] += 1
                prog.progress(done[0] / len(new), text=f'{done[0]} / {len(new)} fichiers...')
        for f in new:
            st.session_state.results[f.name] = res_map.get(f.name, [])
            st.session_state.cache.append(f)
            st.session_state.desel.discard(f.name)
        prog.progress(1.0, text=f'✅ {len(new)} fichier(s) traité(s)')

all_files = st.session_state.cache
if not all_files:
    st.stop()

with st.expander(f'📁 Fichiers ({len(all_files)}) — décochez pour exclure', expanded=True):
    st.caption('Décochez pour exclure un fichier des résultats.')
    for row_names in [
        [f.name for f in all_files][i:i+3]
        for i in range(0, len(all_files), 3)
    ]:
        gcols = st.columns(3)
        for gc, fname in zip(gcols, row_names):
            with gc:
                active = fname not in st.session_state.desel
                if st.checkbox(fname, value=active, key=f'chk_{fname}'):
                    st.session_state.desel.discard(fname)
                else:
                    st.session_state.desel.add(fname)

active_names = {f.name for f in all_files if f.name not in st.session_state.desel}
all_rows = [r for n, rows in st.session_state.results.items()
            if n in active_names for r in rows]

if not active_names:
    st.info('Aucun fichier actif.')
    st.stop()

if not all_rows:
    st.warning('Aucune donnée extraite.')
    with st.expander('🔧 Debug brut'):
        for fname in active_names:
            f = next((x for x in all_files if x.name == fname), None)
            if not f:
                continue
            st.write(f'**{fname}**')
            with pdfplumber.open(f) as pdf:
                for pn, page in enumerate(pdf.pages, 1):
                    cells = get_cells(page)
                    st.write(f'Page {pn} — {len(cells)} cellules')
                    if cells:
                        st.dataframe(pd.DataFrame(
                            [{'R': c['row'], 'C': c['col'],
                              'TITRE': c['title'], 'VALEUR': c['value']}
                             for c in cells]
                        ), use_container_width=True)
                    st.divider()
    st.stop()

df = pd.DataFrame(all_rows)
for col in COLS:
    if col not in df.columns:
        df[col] = ''
df = df[COLS]

st.success(f'✅ {len(df)} ligne(s) — {len(active_names)}/{len(all_files)} fichier(s)')
st.dataframe(df, use_container_width=True)

st.markdown('---')
m = st.columns(5)
m[0].metric('Fichiers', len(active_names))
m[1].metric('Lignes',   len(df))
m[2].metric('Carton LWH ✓',  df[df['Carton LWH (mm)'] != ''].shape[0])
m[3].metric('Pallet LWH ✓',  df[df['Wood Pallet LWH (mm)'] != ''].shape[0])
m[4].metric('Pack Email ✓',  df[df['Packaging Email'] != ''].shape[0])

with st.expander('📦 Dimensions & Quantités'):
    st.dataframe(df[['File', 'Part Number',
                      'Carton LWH (mm)', 'Wood Pallet LWH (mm)',
                      'Standard Pack Quantity',
                      'No. Primary Containers/Layer',
                      'No. Layers on Secondary Container']],
                 use_container_width=True)

with st.expander('📧 Contacts'):
    ca, cb = st.columns(2)
    with ca:
        st.write('**Scheduling**')
        st.dataframe(df[['File', 'Scheduling Contact Name',
                          'Scheduling Email', 'Scheduling Phone']],
                     use_container_width=True)
    with cb:
        st.write('**Packaging**')
        st.dataframe(df[['File', 'Packaging Contact Name',
                          'Packaging Email', 'Packaging Phone']],
                     use_container_width=True)

with st.expander('🔧 Debug — cellules'):
    active_objs = [f for f in all_files if f.name in active_names]
    dbg = st.selectbox('Fichier', [f.name for f in active_objs])
    dbg_f = next(f for f in active_objs if f.name == dbg)
    with pdfplumber.open(dbg_f) as pdf:
        for pn, page in enumerate(pdf.pages, 1):
            cells = get_cells(page)
            if cells:
                st.write(f'**Page {pn} — {len(cells)} cellules**')
                st.dataframe(pd.DataFrame(
                    [{'T': c['table'], 'R': c['row'], 'C': c['col'],
                      'TITRE': c['title'], 'VALEUR': c['value'], 'RAW': c['raw']}
                     for c in cells]
                ), use_container_width=True)
            txt = page.extract_text()
            if txt:
                with st.expander(f'Texte brut p{pn}'):
                    st.text(txt[:3000])
            st.divider()

st.markdown('---')
out = BytesIO()
with pd.ExcelWriter(out, engine='openpyxl') as writer:
    df.to_excel(writer, sheet_name='Données SPI', index=False)
out.seek(0)

d1, d2 = st.columns(2)
with d1:
    st.download_button('⬇ Excel', data=out, file_name='SPI_V7.xlsx',
                       mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                       use_container_width=True)
with d2:
    csv = BytesIO()
    df.to_csv(csv, index=False, encoding='utf-8-sig')
    csv.seek(0)
    st.download_button('⬇ CSV', data=csv, file_name='SPI_V7.csv',
                       mime='text/csv', use_container_width=True)
