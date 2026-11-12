"""vsweep: parameter sweeps over `define values in a Vivado project.

    vsweep configure --vivado PATH --vh src/config.vh     write vsweep.toml (once per project)
    vsweep defines                                       list the defines a sweep can set
    vsweep plan --file sweep.vsw                         show what every run changes; touches nothing
    vsweep run  --file sweep.vsw [--parallel 2]          build every run, collect results.csv
    vsweep reparse vsweep_runs/<dir>                     rebuild results.csv from saved reports
    vsweep check                                         does the last build match the .vh files?
    vsweep restore                                       put the .vh files back after a crash
"""
import argparse
import datetime
import os
import sys
from pathlib import Path

from . import __version__, config, guard, planner, reports, runner, sweepfile, verify
from .config import ConfigError
from .vh import VhError, VhSet, fmt
from .vivado import VivadoError, is_windows_vivado, on_windows_drive
from .workspace import WorkspaceError


class UsageError(Exception):
    pass


def _interactive(args):
    return not getattr(args, 'yes', False) and sys.stdin.isatty()


def _ask(prompt, default=None):
    suffix = ' [%s]' % default if default not in (None, '') else ''
    try:
        answer = input('%s%s: ' % (prompt, suffix)).strip()
    except EOFError:
        answer = ''
    return answer or (default if default is not None else '')


def _choose(prompt, options, allow_many=False):
    for i, o in enumerate(options, 1):
        print('  %d) %s' % (i, o))
    raw = _ask(prompt + (' (numbers separated by commas, or a path)' if allow_many else ' (number or path)'),
               '1' if options else None)
    picked = []
    for part in [p.strip() for p in raw.split(',')] if allow_many else [raw.strip()]:
        if part.isdigit() and 1 <= int(part) <= len(options):
            picked.append(str(options[int(part) - 1]))
        elif part:
            picked.append(part)
    return picked if allow_many else (picked[0] if picked else '')


# ---------------------------------------------------------------------------
# configure
# ---------------------------------------------------------------------------
def cmd_configure(args):
    path = Path(args.config or config.CONFIG_NAME).resolve()
    data = config.load_raw(path) if path.exists() else {}
    viv, proj = data.setdefault('vivado', {}), data.setdefault('project', {})
    out = data.setdefault('output', {})
    if path.exists():
        print('updating %s' % path)

    root = Path(args.root or proj.get('root') or '.')
    root = (path.parent / root).resolve() if not root.is_absolute() else root
    if not root.is_dir():
        raise UsageError('--root %s is not a directory' % root)
    proj['root'] = os.path.relpath(root, path.parent)

    vivado = args.vivado or viv.get('path')
    if not vivado:
        found = config.find_vivado()
        if _interactive(args):
            print('Vivado executable:' if found else 'No Vivado found on PATH or in the usual places.')
            vivado = _choose('vivado', found) if found else _ask('path to vivado (or vivado.bat)')
        elif found:
            vivado = found[0]
            print('vivado: %s (found)' % vivado)
    if not vivado:
        raise UsageError('no Vivado: pass --vivado /path/to/bin/vivado (from WSL: /mnt/c/Xilinx/<ver>/Vivado/bin/vivado.bat)')
    viv['path'] = vivado
    if args.jobs:
        viv['jobs'] = args.jobs
    viv.setdefault('jobs', os.cpu_count() or 1)
    if args.vivado_cwd:
        viv['cwd'] = args.vivado_cwd

    xpr = args.xpr or proj.get('xpr')
    if not xpr:
        found = config.find_files(root, ['.xpr'])
        if len(found) == 1:
            xpr = str(found[0])
            print('xpr: %s (found)' % os.path.relpath(xpr, root))
        elif found and _interactive(args):
            xpr = _choose('project', [os.path.relpath(f, root) for f in found])
        elif found:
            raise UsageError('several .xpr under %s (%s); pass --xpr' % (root, ', '.join(os.path.relpath(f, root) for f in found)))
        else:
            raise UsageError('no .xpr under %s; pass --xpr or --root' % root)
    proj['xpr'] = os.path.relpath((root / xpr).resolve(), root)

    vh = [v for arg in (args.vh or []) for v in arg.split(',') if v] or proj.get('vh')
    if not vh:
        found = [os.path.relpath(f, root) for f in config.find_files(root, ['.vh', '.svh'])]
        if found and _interactive(args):
            print('Header files with the defines to sweep:')
            vh = _choose('vh', found, allow_many=True)
        else:
            raise UsageError('which .vh file holds the defines? pass --vh (candidates: %s)'
                             % (', '.join(found[:10]) or 'none found'))
    if isinstance(vh, str):
        vh = [vh]
    proj['vh'] = [os.path.relpath((root / v).resolve(), root) for v in vh]
    for key in ('synth_run', 'impl_run', 'stop_after'):
        if getattr(args, key):
            proj[key] = getattr(args, key)
    if args.output:
        out['dir'] = args.output

    cfg = config.build(path, data)
    problems = config.check(cfg)
    if problems:
        raise ConfigError('\n  '.join(['the configuration has problems:'] + problems))
    vhset = VhSet(cfg.vh)
    config.write(cfg)
    print('wrote %s' % path)
    print('  vivado   %s%s' % (cfg.vivado, '  (Windows Vivado, via WSL)' if is_windows_vivado(cfg.vivado) else ''))
    print('  jobs     %d' % cfg.jobs)
    print('  project  %s' % cfg.xpr)
    for v in cfg.vh:
        n = sum(1 for k, owner in vhset.owner.items() if owner == str(v))
        print('  vh       %s  (%d defines)' % (v, n))
    if is_windows_vivado(cfg.vivado) and not cfg.vivado_cwd and not on_windows_drive(cfg.root):
        print('warning: Windows Vivado needs a working directory on a Windows drive, and %s is not one;'
              ' move the project to /mnt/<drive>/... or set [vivado] cwd' % cfg.root)
    print('next: `vsweep defines` to see what can be swept, then write a sweep file and `vsweep plan -f <file>`')
    return 0


# ---------------------------------------------------------------------------
def _load(args):
    return config.load(args.config)


def _plan(args, cfg):
    sweep = sweepfile.load(args.file)
    vhset = VhSet(cfg.vh)
    planned, warnings = planner.make_plan(sweep, vhset)
    selected = planner.select(planned, args.only)
    if args.strict and warnings:
        raise planner.PlanError(['--strict: ' + w for w in warnings])
    return sweep, vhset, planned, selected, warnings


def cmd_defines(args):
    cfg = _load(args)
    vhset = VhSet(cfg.vh)
    for path in vhset.paths:
        print(path)
        for name, owner in vhset.owner.items():
            if owner == path:
                kind = 'value' if vhset.has_value[name] else 'flag'
                print('  %-32s %-6s %s' % (name, kind, fmt(vhset.state[name])))
    return 0


def cmd_plan(args):
    cfg = _load(args)
    sweep, vhset, planned, selected, warnings = _plan(args, cfg)
    print('%s: %d run(s)%s' % (args.file, len(planned), ', %d selected' % len(selected) if args.only else ''))
    print(planner.describe(selected, warnings, vhset, sweep.base))
    return 0


def cmd_run(args):
    if args.dry_run:
        return cmd_plan(args)
    cfg = _load(args)
    sweep, vhset, planned, selected, warnings = _plan(args, cfg)
    for w in warnings:
        print('warning: ' + w)
    if args.resume:
        out_dir = Path(args.resume).resolve()
        if not (out_dir / 'plan.txt').exists():
            raise UsageError('%s is not a vsweep output directory' % out_dir)
    else:
        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        out_dir = cfg.output_dir / ('%s_%s' % (stamp, Path(args.file).stem))
        n = 1
        while out_dir.exists():
            n += 1
            out_dir = cfg.output_dir / ('%s_%s_%d' % (stamp, Path(args.file).stem, n))
    with open(args.file, encoding='utf-8') as f:
        sweep_text = f.read()
    print('%s: %d run(s)%s, stop after %s, vivado %s' % (
        args.file, len(selected), ' of %d' % len(planned) if len(selected) != len(planned) else '',
        cfg.stop_after, cfg.vivado))
    r = runner.Runner(cfg, selected, out_dir, sweep_text=sweep_text,
                      plan_text=planner.describe(planned, warnings, vhset, sweep.base),
                      parallel=args.parallel, resume=bool(args.resume), stop_on_fail=args.stop_on_fail,
                      keep_workspaces=args.keep_workspaces, total=len(planned))
    return r.run()


def cmd_reparse(args):
    try:
        cfg = _load(args)
    except ConfigError:
        cfg = None
        print('note: no usable vsweep.toml; reparsing without [reports] hierarchical or [verify]')
    n, path = runner.reparse(cfg, args.dir)
    print('%d run(s) reparsed; results: %s' % (n, path))
    return 0


def cmd_check(args):
    cfg = _load(args)
    vhset = VhSet(cfg.vh)
    synth_dir = cfg.runs_dir / cfg.synth_run
    vds = reports.find_vds(synth_dir) if synth_dir.is_dir() else None
    if not vds:
        raise UsageError('no synthesis log in %s; build the project first' % synth_dir)
    problems = verify.check_stale(vds, cfg.vh) + verify.check_build(reports.read_text(vds), vhset.state, cfg.verify)
    if not cfg.verify:
        print('note: [verify] in vsweep.toml is empty, so only staleness is checked')
    print('\n'.join(problems) if problems else 'the last build matches %s' % ', '.join(map(str, cfg.vh)))
    return 1 if problems else 0


def cmd_restore(args):
    cfg = _load(args)
    done = guard.restore_from_lock(cfg, force=args.force)
    if done is None:
        print('nothing to restore: no %s' % guard.lock_path(cfg))
        return 0
    for path, what in done:
        print('%s: %s' % (path, what))
    return 0


# ---------------------------------------------------------------------------
def make_parser():
    ap = argparse.ArgumentParser(prog='vsweep', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--version', action='version', version='vsweep ' + __version__)
    ap.add_argument('--config', metavar='TOML', help='vsweep.toml to use (default: the nearest one)')
    sub = ap.add_subparsers(dest='command', required=True, metavar='command')

    c = sub.add_parser('configure', help='write vsweep.toml')
    c.add_argument('--vivado', help='Vivado executable (.../bin/vivado, or /mnt/c/.../vivado.bat from WSL)')
    c.add_argument('--jobs', type=int, help='CPUs per build (launch_runs -jobs); default: all')
    c.add_argument('--root', help='project root (default: .)')
    c.add_argument('--xpr', help='the .xpr, relative to root (default: the only one under root)')
    c.add_argument('--vh', action='append', help='a .vh file with the defines (repeatable, or comma-separated)')
    c.add_argument('--synth-run', help='synthesis run (default synth_1)')
    c.add_argument('--impl-run', help='implementation run (default impl_1)')
    c.add_argument('--stop-after', choices=config.STOP_AFTER, help='last step of each build (default bitstream)')
    c.add_argument('--output', help='where sweeps write their results (default vsweep_runs)')
    c.add_argument('--vivado-cwd', help='Windows Vivado only: a directory on a Windows drive to start in')
    c.add_argument('-y', '--yes', action='store_true', help='never ask; use what is found')
    c.set_defaults(func=cmd_configure)

    d = sub.add_parser('defines', help='list the defines in the .vh files')
    d.set_defaults(func=cmd_defines)

    for name, func, text in (('plan', cmd_plan, 'show what each run changes; touch nothing'),
                             ('run', cmd_run, 'build every run and collect results')):
        p = sub.add_parser(name, help=text)
        p.add_argument('-f', '--file', required=True, help='the sweep file')
        p.add_argument('--only', action='append', metavar='NAME',
                       help='only this run (repeatable; shell patterns like "grid_*" work)')
        p.add_argument('--strict', action='store_true', help='fail when a run moves more than one define')
        p.set_defaults(func=func)
        if name == 'run':
            p.add_argument('--parallel', type=int, default=1, metavar='K',
                           help='build K runs at once, each in its own copy of the project')
            p.add_argument('--resume', metavar='DIR', help='continue the sweep in DIR, skipping finished runs')
            p.add_argument('--stop-on-fail', action='store_true', help='start no new run after one fails')
            p.add_argument('--keep-workspaces', action='store_true', help='keep the --parallel copies afterwards')
            p.add_argument('--dry-run', action='store_true', help='same as `vsweep plan`')

    r = sub.add_parser('reparse', help='rebuild results.csv of a sweep from its saved reports')
    r.add_argument('dir')
    r.set_defaults(func=cmd_reparse)

    k = sub.add_parser('check', help='does the last build match the .vh files?')
    k.set_defaults(func=cmd_check)

    s = sub.add_parser('restore', help='put the .vh files back after a sweep was killed')
    s.add_argument('--force', action='store_true', help='even if the sweep holding the lock still runs')
    s.set_defaults(func=cmd_restore)
    return ap


def main(argv=None):
    # Line-buffered even into a file or a pipe: a sweep left running under nohup must show
    # its progress in the log as it happens, not when Python's buffer fills.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(line_buffering=True)
    args = make_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, VhError, sweepfile.SweepError, guard.GuardError, VivadoError, WorkspaceError,
            UsageError) as e:
        print('vsweep: %s' % e, file=sys.stderr)
        return 2
    except planner.PlanError as e:
        for p in e.problems:
            print('vsweep: %s' % p, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
