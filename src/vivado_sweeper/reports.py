"""Parsers for the reports a build leaves in its run directory.

    timing_summary.rpt     report_timing_summary      -> wns, tns, whs, ths, ...
    utilization.rpt        report_utilization         -> lut, ff, bram, dsp, ... (+ _pct)
    utilization_hier.rpt   report_utilization -hier   -> <instance>.lut, <instance>.ff, ...
    power.rpt              report_power               -> power_total_w, ... and <instance>.power_w
    *.vds                  the synthesis log          -> session start, modules and bound parameters

Power is always reported in watts, whatever unit Vivado chose for the report.
"""
import glob
import os
import re


def read_text(path):
    with open(path, 'rb') as f:
        return f.read().decode('utf-8', 'replace')


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _clean(s):
    return '' if s in (None, 'NA', 'N/A', '-') else s


# ---------------------------------------------------------------------------
# report_timing_summary
# ---------------------------------------------------------------------------
TIMING_COLUMNS = {
    'WNS(ns)': 'wns', 'TNS(ns)': 'tns', 'TNS Failing Endpoints': 'tns_failing',
    'WHS(ns)': 'whs', 'THS(ns)': 'ths', 'THS Failing Endpoints': 'ths_failing',
    'WPWS(ns)': 'wpws',
}


def parse_timing_summary(text):
    """The 'Design Timing Summary' row. Its header is the only one that starts with WNS(ns):
    the per-clock tables put a Clock column in front."""
    out = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith('WNS(ns)') and i + 2 < len(lines):
            names = re.split(r'\s{2,}', line.strip())
            values = lines[i + 2].split()
            if len(values) == len(names):
                for name, value in zip(names, values):
                    if name in TIMING_COLUMNS:
                        out[TIMING_COLUMNS[name]] = _clean(value)
            break
    if 'All user specified timing constraints are met.' in text:
        out['timing_met'] = 'yes'
    elif 'Timing constraints are not met.' in text:
        out['timing_met'] = 'no'
    return out


# ---------------------------------------------------------------------------
# report_utilization (flat)
# ---------------------------------------------------------------------------
UTIL_ROWS = {                       # 7-series and UltraScale names for the same resource
    'Slice LUTs': 'lut', 'CLB LUTs': 'lut',
    'LUT as Memory': 'lutram',
    'Slice Registers': 'ff', 'CLB Registers': 'ff',
    'Block RAM Tile': 'bram',
    'URAM': 'uram',
    'DSPs': 'dsp',
    'Bonded IOB': 'io',
}


def _cells(line):
    return [c.strip() for c in line.strip().strip('|').split('|')]


def parse_utilization(text):
    """Used count and Util% of the main resources. The first table row with a given name wins
    (7-series reports list Slice Registers twice, with the same value)."""
    out, cols = {}, None
    for line in text.splitlines():
        if not line.startswith('|'):
            continue
        cells = _cells(line)
        if cells and cells[0] == 'Site Type':
            cols = {name: i for i, name in enumerate(cells)}
            continue
        if cols is None or not cells:
            continue
        key = UTIL_ROWS.get(cells[0].rstrip('*').strip())
        if key is None or key in out:
            continue
        get = lambda name: cells[cols[name]] if name in cols and cols[name] < len(cells) else ''
        out[key] = get('Used')
        out[key + '_pct'] = get('Util%')
    return out


# ---------------------------------------------------------------------------
# Hierarchical tables (report_utilization -hierarchical, report_power's By Hierarchy)
# ---------------------------------------------------------------------------
def _hier_rows(lines, name_col):
    """(path, cells) for every row of a hierarchical table, path built from the indentation
    of the name column (two spaces per level)."""
    cols, stack = None, []
    for line in lines:
        if not line.startswith('|'):
            continue
        raw = line.rstrip().strip('|').split('|')
        cells = [c.strip() for c in raw]
        if cells and cells[0] == name_col:
            cols = {name: i for i, name in enumerate(cells)}
            stack = []
            continue
        if cols is None or not cells or not cells[0]:
            continue
        depth = max(0, (len(raw[0]) - len(raw[0].lstrip(' ')) - 1) // 2)
        name = cells[0]
        del stack[depth:]
        stack.append(name)
        if name.startswith('(') and name.endswith(')'):
            continue                            # (top): the parent's own logic, not an instance
        yield '/'.join(stack), cells, cols


def _wanted(path, wanted):
    """The entry of wanted that names path: a leaf name, or a path suffix a/b/c."""
    leaf = path.rsplit('/', 1)[-1]
    for w in wanted:
        w = w.strip('/')
        if ('/' in w and (path == w or path.endswith('/' + w))) or w == leaf:
            return w
    return None


UTIL_HIER_COLUMNS = {
    'Total LUTs': 'lut', 'LUTRAMs': 'lutram', 'FFs': 'ff', 'RAMB36': 'ramb36', 'RAMB18': 'ramb18',
    'URAM': 'uram', 'DSP48 Blocks': 'dsp', 'DSP Blocks': 'dsp',
}


def parse_utilization_hier(text, wanted):
    """{'<instance>.lut': ..., ...} for each entry of wanted (the first matching row wins)."""
    out, done = {}, set()
    if not wanted:
        return out
    for path, cells, cols in _hier_rows(text.splitlines(), 'Instance'):
        w = _wanted(path, wanted)
        if w is None or w in done:
            continue
        done.add(w)
        for col, key in UTIL_HIER_COLUMNS.items():
            if col in cols and cols[col] < len(cells):
                out['%s.%s' % (w, key)] = cells[cols[col]]
    return out


# ---------------------------------------------------------------------------
# report_power
# ---------------------------------------------------------------------------
POWER_SUMMARY = {
    'Total On-Chip Power': 'power_total_w', 'Dynamic': 'power_dynamic_w',
    'Device Static': 'power_static_w', 'Junction Temperature': 'junction_temp_c',
    'Confidence Level': 'power_confidence',
}
UNIT_SCALE = {'W': 1.0, 'mW': 1e-3, 'uW': 1e-6}


def _watts(value, unit):
    v = _num(value)
    if v is None or unit not in UNIT_SCALE:
        return value
    return '%.6g' % (v * UNIT_SCALE[unit])


def parse_power(text, wanted=()):
    out = {}
    # Section titles appear twice, in the table of contents and as headings: take the last.
    j = text.rfind('1.1 On-Chip Components')
    summary = text[:j] if j >= 0 else text
    i = summary.rfind('1. Summary')
    summary = summary[i:] if i >= 0 else summary
    for line in summary.splitlines():
        if not line.startswith('|'):
            continue
        cells = _cells(line)
        if len(cells) < 2:
            continue
        m = re.match(r'^(.*?)\s*\((\w+)\)$', cells[0])
        label, unit = (m.group(1), m.group(2)) if m else (cells[0], '')
        key = POWER_SUMMARY.get(label)
        if key is None or key in out:
            continue
        out[key] = _watts(cells[1], unit) if key.endswith('_w') else cells[1]
    if wanted:
        k = text.rfind('3.1 By Hierarchy')           # the last one: the first is the table of contents
        if k >= 0:
            unit, done = 'W', set()
            for path, cells, cols in _hier_rows(text[k:].splitlines(), 'Name'):
                pcol = next((c for c in cols if c.startswith('Power')), None)
                if pcol:
                    m = re.search(r'\((\w+)\)', pcol)
                    unit = m.group(1) if m else 'W'
                w = _wanted(path, wanted)
                if w is None or w in done or pcol is None:
                    continue
                done.add(w)
                out['%s.power_w' % w] = _watts(cells[cols[pcol]], unit)
    return out


# ---------------------------------------------------------------------------
# The synthesis log
# ---------------------------------------------------------------------------
SESSION_RE = re.compile(r'# Start of session at:\s*(.+)')


def parse_vds(text):
    """{'session': 'Mon Jan 05 09:00:00 2026' | None, 'blocks': [(module, {param: value})]}
    with blocks in elaboration order, one per `synthesizing module` line."""
    m = SESSION_RE.search(text)
    blocks, cur = [], None
    for line in text.splitlines():
        m2 = re.search(r"synthesizing module '([\w$]+)'", line)
        if m2 and 'done synthesizing' not in line:
            cur = (re.sub(r'__parameterized\d+$', '', m2.group(1)), {})
            blocks.append(cur)
            continue
        m2 = re.match(r'\s*Parameter (\w+) bound to: (\S+)', line)
        if m2 and cur is not None:
            cur[1].setdefault(m2.group(1), m2.group(2))
    return {'session': m.group(1).strip() if m else None, 'blocks': blocks}


def find_vds(run_dir):
    hits = sorted(glob.glob(os.path.join(glob.escape(str(run_dir)), '*.vds')))
    return hits[0] if hits else None


# ---------------------------------------------------------------------------
def collect(run_dir, hierarchical=()):
    """Every metric the reports in run_dir hold (missing reports are simply absent)."""
    out = {}
    readers = [('timing_summary.rpt', parse_timing_summary),
               ('utilization.rpt', parse_utilization),
               ('utilization_hier.rpt', lambda t: parse_utilization_hier(t, hierarchical)),
               ('power.rpt', lambda t: parse_power(t, hierarchical))]
    for name, parse in readers:
        path = os.path.join(run_dir, name)
        if os.path.exists(path):
            out.update(parse(read_text(path)))
    return out
