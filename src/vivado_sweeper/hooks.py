"""pre_build / post_build hooks: anything a sweep should do around each build.

A hook is a shell command (run in the workspace root) or a .tcl file (sourced by Vivado in
batch mode). It learns about the run from

    shell:  VSWEEP_RUN_NAME, VSWEEP_RUN_INDEX, VSWEEP_RUN_DIR, VSWEEP_WORKSPACE, VSWEEP_XPR,
            VSWEEP_SETTINGS_JSON, VSWEEP_BIT, VSWEEP_LTX, VSWEEP_VIVADO, VSWEEP_HOOK
    Tcl:    $::vsweep(run_name), $::vsweep(run_dir), ... (the same names, lower case, no prefix)

and adds columns to results.csv by printing "VSWEEP_METRIC key=value" lines or by writing
$VSWEEP_RUN_DIR/metrics.json (a flat JSON object). A non-zero exit fails the run.
"""
import json
import os
import re
import shlex
import subprocess
import time

from .vivado import MARKER_RE

METRIC_RE = re.compile(r'^\s*([A-Za-z_][\w.\-]*)\s*=\s*(.*?)\s*$')


class HookResult:
    def __init__(self, ok, metrics, message='', seconds=0.0):
        self.ok, self.metrics, self.message, self.seconds = ok, metrics, message, seconds


def _is_tcl(command, cwd):
    parts = shlex.split(command) if command.strip() else []
    return len(parts) == 1 and parts[0].endswith('.tcl'), (os.path.join(cwd, parts[0]) if parts else '')


def _tcl_brace(s):
    if any(c in s for c in '{}\\'):
        raise ValueError('cannot pass %r to Tcl in braces' % s)
    return '{' + s + '}'


def parse_metric(text):
    m = METRIC_RE.match(text)
    return (m.group(1), m.group(2)) if m else None


def merge_metrics(run_dir, metrics):
    """Merge metrics into run_dir/metrics.json (so `vsweep reparse` still has them); return all."""
    path = os.path.join(run_dir, 'metrics.json')
    current = {}
    if os.path.exists(path):
        try:
            with open(path, encoding='utf-8') as f:
                current = json.load(f)
        except (OSError, ValueError):
            current = {}
        if not isinstance(current, dict):
            current = {}
    current.update(metrics)
    if current:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(current, f, indent=1, sort_keys=True)
    return {str(k): v for k, v in current.items()}


def run(kind, command, context, cwd, run_dir, vivado, timeout=None):
    """Run one hook; context is {name: value} (VSWEEP_ prefix added for the shell)."""
    t0 = time.time()
    is_tcl, tcl_path = _is_tcl(command, cwd)
    metrics = {}
    if is_tcl:
        if not os.path.isfile(tcl_path):
            return HookResult(False, {}, '%s hook: no such Tcl file %s' % (kind, tcl_path))
        values = {k: (vivado.path(v) if k in PATH_KEYS and v else v) for k, v in context.items()}
        wrapper = os.path.join(run_dir, '%s_hook.tcl' % kind)
        with open(wrapper, 'w', newline='\n') as f:
            f.write('puts "VSWEEP_PID [pid]"\n')
            f.write('array set ::vsweep [list %s]\n' % ' '.join(
                '%s %s' % (k.lower(), _tcl_brace(str(v))) for k, v in values.items()))
            f.write('if {[catch {source %s} err]} { puts "VSWEEP_FAIL $err"; exit 1 }\n'
                    % _tcl_brace(vivado.path(tcl_path)))
            f.write('puts "VSWEEP_DONE"\nexit 0\n')
        res = vivado.run(wrapper, os.path.join(run_dir, '%s.log' % kind),
                         os.path.join(run_dir, '%s.vivado.log' % kind), cwd=vivado.cwd or cwd, timeout=timeout)
        for text in res.texts('METRIC'):
            kv = parse_metric(text)
            if kv:
                metrics[kv[0]] = kv[1]
        ok = res.ok
        message = '' if ok else '%s hook failed: %s' % (kind, '; '.join(res.texts('FAIL')) or 'exit %s' % res.rc)
    else:
        env = dict(os.environ)
        env.update({'VSWEEP_' + k: str(v) for k, v in context.items()})
        env['VSWEEP_HOOK'] = kind
        log_path = os.path.join(run_dir, '%s.log' % kind)
        with open(log_path, 'wb') as log:
            proc = subprocess.Popen(command, shell=True, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
            vivado.procs.add(proc, False)
            try:
                for raw in proc.stdout:
                    log.write(raw)
                    log.flush()
                    m = MARKER_RE.match(raw.decode('utf-8', 'replace').rstrip('\r\n'))
                    if m and m.group(1) == 'METRIC':
                        kv = parse_metric(m.group(2))
                        if kv:
                            metrics[kv[0]] = kv[1]
                try:
                    rc = proc.wait(timeout)
                except subprocess.TimeoutExpired:
                    vivado.procs.kill(proc)
                    rc = 'timeout'
            finally:
                if proc.poll() is None:
                    vivado.procs.kill(proc)
                vivado.procs.remove(proc)
        ok = rc == 0
        message = '' if ok else '%s hook exited %s (see %s.log)' % (kind, rc, kind)
    all_metrics = merge_metrics(run_dir, metrics)
    return HookResult(ok, all_metrics, message, time.time() - t0)


PATH_KEYS = {'RUN_DIR', 'WORKSPACE', 'XPR', 'SETTINGS_JSON', 'BIT', 'LTX'}
