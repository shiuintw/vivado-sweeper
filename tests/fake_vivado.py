#!/usr/bin/env python3
"""A stand-in for `vivado -mode batch -source X.tcl`, for tests without Vivado.

It understands the two kinds of script vsweep writes:

  build scripts   (`set vs(...)` header): "synthesizes" the project by reading the .vh files the
                  .xpr lists, writes a .vds with a `Parameter NAME bound to: VALUE` line for every
                  value define (module `top`) and a `synthesizing module 'mod_<flag>'` line for
                  every flag that is on, then the reports, from tests/fixtures, with numbers
                  derived from DEPTH (WNS = 2 - DEPTH/16, LUT = 10 * DEPTH).
  hook wrappers   prints any `puts "VSWEEP_METRIC ..."` line of the hook it sources.

Defines that steer it: FAKE_FAIL (flag on -> synthesis fails), FAKE_SLEEP n (seconds to take),
FAKE_WRONG (flag on -> DEPTH is bound to the wrong value).
"""
import os
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')
DEFINE_RE = re.compile(r'^\s*(//\s*)?`define\s+(\w+)(?:[ \t]+([^\s/]+))?')


def arg(flag):
    a = sys.argv
    return a[a.index(flag) + 1] if flag in a else None


def tcl_vars(text):
    out = {}
    for m in re.finditer(r'^set vs\((\w+)\) (.*)$', text, re.M):
        v = m.group(2).strip()
        if v.startswith('[list'):
            v = re.findall(r'\{([^}]*)\}|(\S+)', v[5:-1])
            v = [a or b for a, b in v]
        elif v.startswith('{') and v.endswith('}'):
            v = v[1:-1]
        out[m.group(1)] = v
    return out


def defines(xpr):
    state = {}
    xdir = os.path.dirname(xpr)
    for el in ET.parse(xpr).iter('File'):
        path = el.get('Path', '').replace('$PPRDIR', xdir)
        if path.endswith('.vh') and os.path.exists(path):
            for line in open(path, encoding='utf-8', errors='replace'):
                m = DEFINE_RE.match(line)
                if m:
                    state[m.group(2)] = False if m.group(1) else (m.group(3) if m.group(3) else True)
    return state


def fill(name, out, **values):
    text = open(os.path.join(FIXTURES, name)).read()
    for k, v in values.items():
        text = text.replace('@%s@' % k, str(v))
    open(os.path.join(out, name), 'w').write(text)


def hook(text):
    m = re.search(r'source \{([^}]*)\}', text)
    if m and os.path.exists(m.group(1)):
        for line in open(m.group(1)):
            mm = re.match(r'\s*puts "(VSWEEP_METRIC [^"]*)"', line)
            if mm:
                print(mm.group(1))
            if line.strip() == 'error boom':
                print('VSWEEP_FAIL boom')
                return 1
    print('VSWEEP_DONE')
    return 0


def main():
    tcl = arg('-source')
    log = arg('-log')
    text = open(tcl).read()
    print('VSWEEP_PID %d' % os.getpid(), flush=True)
    if log:
        open(log, 'w').write('fake vivado log\n')
    if 'set vs(xpr)' not in text:
        return hook(text)
    vs = tcl_vars(text)
    xpr, out = vs['xpr'], vs['out']
    proj = os.path.splitext(os.path.basename(xpr))[0]
    state = defines(xpr)
    synth_dir = os.path.join(os.path.dirname(xpr), proj + '.runs', vs['synth_run'])
    impl_dir = os.path.join(os.path.dirname(xpr), proj + '.runs', vs['impl_run'])
    os.makedirs(synth_dir, exist_ok=True)
    print('VSWEEP_STEP synthesis', flush=True)
    sleep = state.get('FAKE_SLEEP')
    if isinstance(sleep, str):
        time.sleep(float(sleep))
    vds = os.path.join(synth_dir, 'top.vds')
    old = None
    if os.path.exists(vds):
        m = re.search(r'# Start of session at: (.+)', open(vds).read())
        old = m.group(1) if m else None
    while time.strftime('%a %b %d %H:%M:%S %Y') == old:    # a real session takes longer than a second
        time.sleep(0.05)
    lines = ['#-----------------------------------------------------------',
             '# Vivado v2025.1 (64-bit)',
             '# Start of session at: %s' % time.strftime('%a %b %d %H:%M:%S %Y'),
             "INFO: [Synth 8-6157] synthesizing module 'top' [top.v:1]"]
    for name, value in state.items():
        if isinstance(value, str):
            if name == 'DEPTH' and state.get('FAKE_WRONG') is True:
                value = str(int(value) + 1)
            lines.append('\tParameter %s bound to: %s - type: integer ' % (name, value))
    for name, value in state.items():
        if value is True:
            lines.append("INFO: [Synth 8-6157] synthesizing module 'mod_%s' [m.v:1]" % name.lower())
            lines.append("INFO: [Synth 8-6155] done synthesizing module 'mod_%s' (0#1) [m.v:1]" % name.lower())
    lines.append("INFO: [Synth 8-6155] done synthesizing module 'top' (0#1) [top.v:1]")
    open(vds, 'w').write('\n'.join(lines) + '\n')
    open(os.path.join(synth_dir, 'runme.log'), 'w').write('synth runme\n')
    shutil.copy2(vds, out)
    shutil.copy2(os.path.join(synth_dir, 'runme.log'), os.path.join(out, 'synth_runme.log'))
    if state.get('FAKE_FAIL') is True:
        print('VSWEEP_STATUS synth synth_design ERROR')
        print('VSWEEP_FAIL synthesis did not complete: synth_design ERROR (see synth_runme.log)')
        return 1
    print('VSWEEP_STATUS synth synth_design Complete!')
    print('VSWEEP_TIME synth 3')
    if vs['stop_after'] != 'synth':
        print('VSWEEP_STEP implementation', flush=True)
        os.makedirs(impl_dir, exist_ok=True)
        for ext in ('bit', 'ltx'):
            open(os.path.join(impl_dir, 'top.' + ext), 'w').write(ext)
            if ext in vs.get('keep', []):
                shutil.copy2(os.path.join(impl_dir, 'top.' + ext), out)
        print('VSWEEP_TIME impl 5')
    print('VSWEEP_STEP reports', flush=True)
    depth = int(state.get('DEPTH', '16')) if isinstance(state.get('DEPTH'), str) else 16
    wns = 2 - depth / 16
    fill('timing_summary.rpt', out, WNS='%.3f' % wns, TNS='%.3f' % min(0, wns * 10), FAIL=0 if wns >= 0 else 10,
         MET='All user specified timing constraints are met.' if wns >= 0 else 'Timing constraints are not met.')
    fill('utilization.rpt', out, LUT=10 * depth, LUTPCT='%.2f' % (10 * depth / 634), FF=60)
    fill('power.rpt', out, PTOT='%.3f' % (0.1 + depth / 1000), PDYN='%.3f' % (depth / 1000))
    if vs.get('hier'):
        fill('utilization_hier.rpt', out, LUT=10 * depth, FF=60, FIFOLUT=8 * depth)
    print('VSWEEP_DONE')
    return 0


if __name__ == '__main__':
    sys.exit(main())
