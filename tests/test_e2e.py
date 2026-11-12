"""The whole flow against tests/fake_vivado.py: configure, plan, run, hooks, resume, parallel,
interrupts, restore, reparse."""
import csv
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from vivado_sweeper import cli, guard

HERE = Path(__file__).resolve().parent
FAKE = HERE / 'fake_vivado.py'
SRC = HERE.parent / 'src'

VH = (b'// project configuration\r\n'
      b'`define DEPTH 16\r\n'
      b'`define WIDTH 8\r\n'
      b'// `define ENABLE_X\r\n'
      b'// `define FAKE_FAIL\r\n'
      b'// `define FAKE_WRONG\r\n'
      b'// `define FAKE_SLEEP 0\r\n')

XPR = '''<?xml version="1.0" encoding="UTF-8"?>
<Project Product="Vivado" Version="7" Minor="70" Path="{xpr}">
  <FileSets>
    <FileSet Name="sources_1" Type="DesignSrcs">
      <File Path="$PPRDIR/../src/config.vh"/>
      <File Path="$PPRDIR/../src/top.v"/>
      {extra}
    </FileSet>
  </FileSets>
</Project>
'''


def make_project(root, extra=''):
    (root / 'src').mkdir(parents=True)
    (root / 'hw').mkdir()
    (root / 'src' / 'config.vh').write_bytes(VH)
    (root / 'src' / 'top.v').write_text('module top; endmodule\n')
    (root / 'hw' / 'proj.xpr').write_text(XPR.format(xpr=root / 'hw' / 'proj.xpr', extra=extra))
    return root


@pytest.fixture
def project(tmp_path, monkeypatch):
    os.chmod(FAKE, 0o755)
    root = make_project(tmp_path / 'proj')
    monkeypatch.chdir(root)
    assert cli.main(['configure', '--vivado', str(FAKE), '--vh', 'src/config.vh', '--jobs', '4', '-y']) == 0
    return root


def sweep(root, text, name='sweep.vsw'):
    (root / name).write_text(text)
    return name


def results(root):
    out = sorted((root / 'vsweep_runs').iterdir())[-1]
    with open(out / 'results.csv', newline='') as f:
        return out, list(csv.DictReader(f))


def set_config(root, section, **values):
    text = (root / 'vsweep.toml').read_text()
    lines = text.splitlines()
    i = lines.index('[%s]' % section)
    for k, v in values.items():
        lines = [l for l in lines if not l.startswith(k + ' =')]
        lines.insert(i + 1, '%s = %s' % (k, json.dumps(v)))
    (root / 'vsweep.toml').write_text('\n'.join(lines) + '\n')


def vsweep_proc(root, *args):
    env = dict(os.environ, PYTHONPATH=str(SRC))
    return subprocess.Popen([sys.executable, '-m', 'vivado_sweeper.cli'] + list(args), cwd=root, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def wait_for(cond, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
def test_configure(project):
    text = (project / 'vsweep.toml').read_text()
    assert 'xpr = "hw/proj.xpr"' in text and 'vh = ["src/config.vh"]' in text and 'jobs = 4' in text


def test_run_end_to_end(project, capsys):
    name = sweep(project, '''
        base { WIDTH=16 }
        baseline: {}
        deep: {DEPTH=32}
        g < baseline: {DEPTH=[8,48], ENABLE_X=[on,off]}
    ''')
    assert cli.main(['run', '-f', name]) == 0
    out, rows = results(project)
    assert [r['name'] for r in rows] == ['baseline', 'deep', 'g_DEPTH8_ENABLE_Xon', 'g_DEPTH8_ENABLE_Xoff',
                                         'g_DEPTH48_ENABLE_Xoff', 'g_DEPTH48_ENABLE_Xon']
    assert all(r['status'] == 'ok' for r in rows)
    assert [r['DEPTH'] for r in rows] == ['16', '32', '8', '8', '48', '48']
    assert [r['WIDTH'] for r in rows] == ['16'] * 6
    assert [r['wns'] for r in rows] == ['1.000', '0.000', '1.500', '1.500', '-1.000', '-1.000']
    assert rows[4]['timing_met'] == 'no' and rows[0]['timing_met'] == 'yes'
    assert rows[1]['lut'] == '320' and rows[1]['power_total_w'] == '0.132' and rows[1]['dsp'] == '2'
    assert rows[0]['power_dynamic_w'] == '0.016'                    # the summary, not On-Chip Components
    # the project is exactly as before, and unlocked
    assert (project / 'src' / 'config.vh').read_bytes() == VH
    assert not guard.lock_path(type('C', (), {'root': project})()).exists()
    # each run directory holds what it was built from
    d = out / '02_deep'
    assert b'`define DEPTH 32\r\n' in (d / 'vh' / 'config.vh').read_bytes()
    assert {'build.tcl', 'build.log', 'top.vds', 'timing_summary.rpt', 'top.bit', 'done.json'} <= {p.name for p in d.iterdir()}
    assert json.loads((d / 'done.json').read_text())['delta'] == ['DEPTH: 16 -> 32']
    assert (out / 'originals' / '00_config.vh').read_bytes() == VH
    printed = capsys.readouterr().out
    assert 'restored' in printed and 'WNS 0.000' in printed and '[6/6]' in printed


def test_plan_touches_nothing(project, capsys):
    name = sweep(project, 'a: {DEPTH=8}\nb: {DEPTH=9, WIDTH=1}')
    assert cli.main(['plan', '-f', name]) == 0
    printed = capsys.readouterr().out
    assert 'vs a: DEPTH: 8 -> 9, WIDTH: 8 -> 1' in printed and 'warning: a -> b moves 2 defines' in printed
    assert not (project / 'vsweep_runs').exists()
    assert cli.main(['plan', '-f', name, '--strict']) == 2
    assert cli.main(['plan', '-f', sweep(project, '{DEPHT=1}', 'bad.vsw')]) == 2
    assert 'did you mean DEPTH' in capsys.readouterr().err


def test_failures_and_only(project):
    name = sweep(project, 'a: {}\nbad: {FAKE_FAIL=on}\nc: {FAKE_FAIL=off, DEPTH=8}')
    assert cli.main(['run', '-f', name]) == 1
    _, rows = results(project)
    assert [r['status'] for r in rows] == ['ok', 'build_failed', 'ok']
    assert 'synthesis did not complete' in rows[1]['problems']
    assert cli.main(['run', '-f', name, '--stop-on-fail']) == 1
    _, rows = results(project)
    assert [r['status'] for r in rows] == ['ok', 'build_failed']
    assert cli.main(['run', '-f', name, '--only', 'c']) == 0
    _, rows = results(project)
    assert [(r['index'], r['name'], r['DEPTH']) for r in rows] == [('3', 'c', '8')]
    assert (project / 'src' / 'config.vh').read_bytes() == VH


def test_verify(project):
    set_config(project, 'verify', DEPTH='top.DEPTH', ENABLE_X='mod_enable_x')
    name = sweep(project, 'a: {ENABLE_X=on}\nb: {ENABLE_X=off}\nc: {FAKE_WRONG=on}')
    assert cli.main(['run', '-f', name]) == 1
    _, rows = results(project)
    assert [r['status'] for r in rows] == ['ok', 'ok', 'verify_failed']
    assert 'DEPTH = 16 but top.DEPTH was bound to 17' in rows[2]['problems']


def test_check_reports_mismatch(project, capsys):
    set_config(project, 'verify', DEPTH='top.DEPTH')
    assert cli.main(['run', '-f', sweep(project, 'a: {DEPTH=40}')]) == 0
    capsys.readouterr()
    assert cli.main(['check']) == 1         # the .vh is back at 16, the last build used 40
    assert 'DEPTH = 16 but top.DEPTH was bound to 40' in capsys.readouterr().out


def test_hooks(project):
    (project / 'post.sh').write_text('#!/bin/sh\n'
                                     'echo "VSWEEP_METRIC score=$(( $VSWEEP_RUN_INDEX * 10 ))"\n'
                                     'echo "{\\"bitname\\": \\"$(basename "$VSWEEP_BIT")\\"}" > "$VSWEEP_RUN_DIR/metrics.json"\n')
    (project / 'pre.tcl').write_text('puts "VSWEEP_METRIC pre=yes"\n')
    set_config(project, 'hooks', post_build='sh post.sh', pre_build='pre.tcl')
    assert cli.main(['run', '-f', sweep(project, 'a: {}\nb: {DEPTH=8}')]) == 0
    _, rows = results(project)
    assert [(r['score'], r['bitname'], r['pre']) for r in rows] == [('10', 'top.bit', 'yes'), ('20', 'top.bit', 'yes')]
    set_config(project, 'hooks', post_build='exit 3', pre_build='')
    assert cli.main(['run', '-f', sweep(project, 'a: {}')]) == 1
    _, rows = results(project)
    assert rows[0]['status'] == 'post_build_failed' and 'exited 3' in rows[0]['problems']


def test_resume(project, capsys):
    name = sweep(project, 'a: {}\nb: {FAKE_FAIL=on}\nc: {DEPTH=8}')
    cli.main(['run', '-f', name])
    out, _ = results(project)
    a_done = (out / '01_a' / 'done.json').stat().st_mtime
    sweep(project, 'a: {}\nb: {FAKE_FAIL=off}\nc: {DEPTH=8}')          # fix b, resume
    capsys.readouterr()
    assert cli.main(['run', '-f', name, '--resume', str(out)]) == 0
    printed = capsys.readouterr().out
    # c inherited FAKE_FAIL=on from b, so it failed too: only a is skipped
    assert '[01_a] done in an earlier run, skipped' in printed and '[03_c] done' not in printed
    assert (out / '01_a' / 'done.json').stat().st_mtime == a_done
    with open(out / 'results.csv', newline='') as f:
        assert [r['status'] for r in csv.DictReader(f)] == ['ok', 'ok', 'ok']


def test_parallel(project):
    (project / 'hw' / 'proj.runs' / 'ip_synth_1').mkdir(parents=True)
    (project / 'hw' / 'proj.runs' / 'ip_synth_1' / 'ip.dcp').write_text('ip')
    name = sweep(project, 'g: {DEPTH=[8,16,24,32], ENABLE_X=[on,off]}')
    before = (project / 'src' / 'config.vh').stat().st_mtime_ns
    assert cli.main(['run', '-f', name, '--parallel', '3', '--keep-workspaces']) == 0
    _, rows = results(project)
    assert len(rows) == 8 and all(r['status'] == 'ok' for r in rows)
    assert sorted(r['wns'] for r in rows) == sorted(['1.500', '1.500', '1.000', '1.000', '0.500', '0.500', '0.000', '0.000'])
    assert (project / 'src' / 'config.vh').stat().st_mtime_ns == before      # never touched
    ws = project / '.vsweep' / 'ws'
    assert sorted(p.name for p in ws.iterdir()) == ['w0', 'w1', 'w2']
    assert (ws / 'w0' / 'hw' / 'proj.runs' / 'ip_synth_1' / 'ip.dcp').exists()   # IP runs are copied
    assert not (ws / 'w0' / 'vsweep_runs').exists()
    workers = {json.loads(p.read_text())['workspace'] for p in sorted(project.glob('vsweep_runs/*/*/done.json'))}
    assert len(workers) > 1
    assert cli.main(['run', '-f', name, '--parallel', '2', '--only', 'g_DEPTH8_*']) == 0
    assert not (project / '.vsweep' / 'ws').exists()                          # removed by default


def test_parallel_refuses_unclonable_project(tmp_path, monkeypatch, capsys):
    os.chmod(FAKE, 0o755)
    root = make_project(tmp_path / 'proj', extra='<File Path="$PPRDIR/../../outside/lib.v"/>')
    monkeypatch.chdir(root)
    cli.main(['configure', '--vivado', str(FAKE), '--vh', 'src/config.vh', '-y'])
    assert cli.main(['run', '-f', sweep(root, 'a: {}'), '--parallel', '2']) == 2
    assert 'outside project root' in capsys.readouterr().err
    assert not guard.lock_path(type('C', (), {'root': root})()).exists()


def test_sigterm_restores(project):
    name = sweep(project, 'slow: {FAKE_SLEEP=30, DEPTH=99}\nnever: {}')
    p = vsweep_proc(project, 'run', '-f', name)
    vh = project / 'src' / 'config.vh'
    assert wait_for(lambda: b'DEPTH 99' in vh.read_bytes() and list(project.glob('vsweep_runs/*/01_slow/build.log')))
    time.sleep(0.5)
    p.send_signal(signal.SIGTERM)
    out, _ = p.communicate(timeout=30)
    assert p.returncode == 130, out
    assert vh.read_bytes() == VH
    assert 'interrupted (SIGTERM)' in out
    _, rows = results(project)
    assert [r['status'] for r in rows] == ['interrupted']
    pid = int([l for l in (sorted(project.glob('vsweep_runs/*/01_slow/build.log'))[0]).read_text().splitlines()
               if l.startswith('VSWEEP_PID')][0].split()[1])
    assert wait_for(lambda: not os.path.exists('/proc/%d' % pid) or
                    open('/proc/%d/stat' % pid).read().split()[2] == 'Z', 10)


def test_kill9_then_restore(project, capsys):
    name = sweep(project, 'slow: {FAKE_SLEEP=30, DEPTH=99}')
    p = vsweep_proc(project, 'run', '-f', name)
    vh = project / 'src' / 'config.vh'
    assert wait_for(lambda: b'DEPTH 99' in vh.read_bytes())
    p.kill()
    p.wait()
    capsys.readouterr()
    assert cli.main(['run', '-f', name]) == 2
    assert 'did not finish cleanly' in capsys.readouterr().err
    assert cli.main(['restore']) == 0
    assert 'restored' in capsys.readouterr().out
    assert vh.read_bytes() == VH
    assert cli.main(['restore']) == 0 and 'nothing to restore' in capsys.readouterr().out
    for pid_line in project.glob('vsweep_runs/*/01_slow/build.log'):          # the orphaned fake Vivado
        for l in pid_line.read_text().splitlines():
            if l.startswith('VSWEEP_PID'):
                try:
                    os.kill(int(l.split()[1]), signal.SIGKILL)
                except ProcessLookupError:
                    pass


def test_reparse(project):
    assert cli.main(['run', '-f', sweep(project, 'a: {DEPTH=8}')]) == 0
    out, rows = results(project)
    assert 'u_fifo.lut' not in rows[0]
    set_config(project, 'reports', hierarchical=['u_fifo', 'top/u_alu'])
    assert cli.main(['run', '-f', sweep(project, 'a: {DEPTH=8}')]) == 0
    out, rows = results(project)
    assert rows[0]['u_fifo.lut'] == '64' and rows[0]['top/u_alu.dsp'] == '2' and rows[0]['u_fifo.power_w'] == '0.00125'
    (out / 'results.csv').unlink()
    assert cli.main(['reparse', str(out)]) == 0
    with open(out / 'results.csv', newline='') as f:
        assert next(csv.DictReader(f))['u_fifo.lut'] == '64'


def test_defines(project, capsys):
    assert cli.main(['defines']) == 0
    printed = capsys.readouterr().out
    assert 'DEPTH' in printed and 'flag' in printed and 'value' in printed


def test_missing_hierarchical_instance_warns(project, capsys):
    set_config(project, 'reports', hierarchical=['u_fifo', 'u_gone'])
    assert cli.main(['run', '-f', sweep(project, 'a: {}')]) == 0
    assert 'warning: no instance u_gone in the hierarchical reports' in capsys.readouterr().out
