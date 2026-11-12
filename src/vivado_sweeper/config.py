"""vsweep.toml: where Vivado is, which project, which .vh files, and how to build.

`root` is relative to the directory holding vsweep.toml; every other path (xpr, vh,
output.dir, reports.extra_tcl) is relative to root. Hook commands run with root as cwd.
"""
import glob
import json
import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .vivado import from_windows

CONFIG_NAME = 'vsweep.toml'
STOP_AFTER = ('synth', 'impl', 'bitstream')
KEEP_KINDS = ('bit', 'ltx', 'dcp')


class ConfigError(Exception):
    pass


@dataclass
class Config:
    path: Path                                   # this vsweep.toml
    vivado: str                                  # executable: .../bin/vivado or /mnt/c/.../vivado.bat
    jobs: int
    root: Path
    xpr: Path
    vh: list
    synth_run: str = 'synth_1'
    impl_run: str = 'impl_1'
    stop_after: str = 'bitstream'
    vivado_cwd: Path | None = None               # Windows Vivado: a directory on a Windows drive
    timeout_hours: float = 0.0                   # per build; 0 = no limit
    output_dir: Path | None = None
    keep: list = field(default_factory=lambda: ['bit', 'ltx'])
    hierarchical: list = field(default_factory=list)
    extra_tcl: Path | None = None
    verify: dict = field(default_factory=dict)   # define -> "module.PARAM" or "module"
    pre_build: str = ''
    post_build: str = ''

    @property
    def project_name(self):
        return self.xpr.stem

    @property
    def runs_dir(self):
        """<project>.runs next to the .xpr (the project's default run directory)."""
        return self.xpr.parent / (self.xpr.stem + '.runs')


def find_config(start=None):
    """The nearest vsweep.toml in start (default cwd) or one of its parents."""
    d = Path(start or os.getcwd()).resolve()
    for p in [d] + list(d.parents):
        if (p / CONFIG_NAME).is_file():
            return p / CONFIG_NAME
    return None


def _abs(base, value):
    return (Path(base) / Path(value).expanduser()).resolve() if value else None


def load_raw(path):
    try:
        with open(path, 'rb') as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError('%s: %s' % (path, e))


def build(path, data):
    """A Config from vsweep.toml's parsed data (paths resolved; nothing checked on disk)."""
    path = Path(path).resolve()
    viv, proj = data.get('vivado', {}), data.get('project', {})
    out, rep, hooks = data.get('output', {}), data.get('reports', {}), data.get('hooks', {})
    if not viv.get('path'):
        raise ConfigError('%s: [vivado] path is not set' % path)
    if not proj.get('xpr') or not proj.get('vh'):
        raise ConfigError('%s: [project] needs xpr and vh' % path)
    root = _abs(path.parent, proj.get('root', '.'))
    vh = proj['vh'] if isinstance(proj['vh'], list) else [proj['vh']]
    try:
        return Config(
            path=path,
            vivado=from_windows(os.path.expanduser(str(viv['path']))),
            jobs=int(viv.get('jobs', os.cpu_count() or 1)),
            root=root,
            xpr=_abs(root, proj['xpr']),
            vh=[_abs(root, v) for v in vh],
            synth_run=str(proj.get('synth_run', 'synth_1')),
            impl_run=str(proj.get('impl_run', 'impl_1')),
            stop_after=str(proj.get('stop_after', 'bitstream')),
            vivado_cwd=_abs(root, viv['cwd']) if viv.get('cwd') else None,
            timeout_hours=float(viv.get('timeout_hours', 0) or 0),
            output_dir=_abs(root, out.get('dir', 'vsweep_runs')),
            keep=[str(k) for k in out.get('keep', ['bit', 'ltx'])],
            hierarchical=[str(h) for h in rep.get('hierarchical', [])],
            extra_tcl=_abs(root, rep['extra_tcl']) if rep.get('extra_tcl') else None,
            verify={str(k): str(v) for k, v in data.get('verify', {}).items()},
            pre_build=str(hooks.get('pre_build', '') or ''),
            post_build=str(hooks.get('post_build', '') or ''),
        )
    except (TypeError, ValueError) as e:
        raise ConfigError('%s: %s' % (path, e))


def load(path=None):
    path = Path(path) if path else find_config()
    if path is None:
        raise ConfigError('no %s here or in any parent directory; run `vsweep configure` first' % CONFIG_NAME)
    cfg = build(path, load_raw(path))
    problems = check(cfg)
    if problems:
        raise ConfigError('%s:\n  %s' % (cfg.path, '\n  '.join(problems)))
    return cfg


def check(cfg, files=True):
    """Problems with cfg; files=False skips the existence checks."""
    problems = []
    if cfg.stop_after not in STOP_AFTER:
        problems.append('stop_after = %r: must be one of %s' % (cfg.stop_after, ', '.join(STOP_AFTER)))
    if cfg.jobs < 1:
        problems.append('jobs = %d: must be at least 1' % cfg.jobs)
    bad = [k for k in cfg.keep if k not in KEEP_KINDS]
    if bad:
        problems.append('output.keep: unknown %s (known: %s)' % (', '.join(bad), ', '.join(KEEP_KINDS)))
    for name, target in cfg.verify.items():
        if not target or target.count('.') > 1:
            problems.append('verify.%s = %r: expected "module.PARAMETER" or "module"' % (name, target))
    if not files:
        return problems
    if not (os.path.isfile(cfg.vivado) or shutil.which(cfg.vivado)):
        problems.append('vivado: no such executable: %s' % cfg.vivado)
    if not cfg.xpr.is_file():
        problems.append('xpr: no such file: %s' % cfg.xpr)
    for v in cfg.vh:
        if not v.is_file():
            problems.append('vh: no such file: %s' % v)
    if cfg.extra_tcl and not cfg.extra_tcl.is_file():
        problems.append('reports.extra_tcl: no such file: %s' % cfg.extra_tcl)
    return problems


# ---------------------------------------------------------------------------
# Writing (vsweep configure)
# ---------------------------------------------------------------------------
def _s(value):
    return json.dumps(str(value), ensure_ascii=False)   # a JSON string is a valid TOML basic string


def _list(values):
    return '[' + ', '.join(_s(v) for v in values) + ']'


def _rel(path, base):
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return str(path)


def render(cfg):
    """vsweep.toml text for cfg (paths written relative, as load() reads them)."""
    cdir = cfg.path.parent
    lines = [
        '# vivado-sweeper configuration. Paths: root is relative to this file, everything else to root.',
        '',
        '[vivado]',
        '# Linux Vivado (.../bin/vivado), or from WSL the Windows one (/mnt/c/Xilinx/<ver>/Vivado/bin/vivado.bat)',
        'path = %s' % _s(cfg.vivado),
        'jobs = %d                       # launch_runs -jobs (split between workers with --parallel)' % cfg.jobs,
    ]
    if cfg.vivado_cwd:
        lines.append('cwd = %s' % _s(_rel(cfg.vivado_cwd, cfg.root)))
    else:
        lines.append('# cwd = "..."                 # Windows Vivado only: where it starts (must be on a Windows drive)')
    lines += [
        'timeout_hours = %g              # per build; 0 = no limit' % cfg.timeout_hours,
        '',
        '[project]',
        'root = %s' % _s(_rel(cfg.root, cdir)),
        'xpr = %s' % _s(_rel(cfg.xpr, cfg.root)),
        'vh = %s' % _list(_rel(v, cfg.root) for v in cfg.vh),
        'synth_run = %s' % _s(cfg.synth_run),
        'impl_run = %s' % _s(cfg.impl_run),
        'stop_after = %s           # synth | impl | bitstream' % _s(cfg.stop_after),
        '',
        '[output]',
        'dir = %s' % _s(_rel(cfg.output_dir, cfg.root)),
        'keep = %s          # build products copied into each run directory: bit, ltx, dcp' % _list(cfg.keep),
        '',
        '[reports]',
        '# instances whose utilization and power get columns of their own (leaf name or a/b/c path)',
        'hierarchical = %s' % _list(cfg.hierarchical),
        'extra_tcl = %s                  # sourced after the built-in reports, with $run_dir set'
        % _s(_rel(cfg.extra_tcl, cfg.root) if cfg.extra_tcl else ''),
        '',
        '[verify]',
        '# Checked against the synthesis log after every build:',
        '#   DEPTH = "fifo.DEPTH"     the value of `define DEPTH must be what fifo\'s DEPTH was bound to',
        '#   USE_CACHE = "cache"      module cache must be synthesized exactly when `define USE_CACHE is on',
    ]
    lines += ['%s = %s' % (k, _s(v)) for k, v in cfg.verify.items()]
    lines += [
        '',
        '[hooks]',
        '# Shell commands (run in root), or a .tcl file (run by Vivado in batch mode). They get',
        '# VSWEEP_RUN_DIR, VSWEEP_RUN_NAME, VSWEEP_BIT, ... in the environment (::vsweep(...) in Tcl),',
        '# and may write $VSWEEP_RUN_DIR/metrics.json or print "VSWEEP_METRIC key=value" to add columns.',
        'pre_build = %s' % _s(cfg.pre_build),
        'post_build = %s' % _s(cfg.post_build),
        '',
    ]
    return '\n'.join(lines)


def write(cfg):
    text = render(cfg)
    with open(cfg.path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    return text


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
VIVADO_GLOBS = [
    '/tools/Xilinx/Vivado/*/bin/vivado', '/tools/Xilinx/*/Vivado/bin/vivado',
    '/opt/Xilinx/Vivado/*/bin/vivado', '/opt/Xilinx/*/Vivado/bin/vivado',
    '/tools/AMD/*/Vivado/bin/vivado', '/opt/AMD/*/Vivado/bin/vivado',
    '/mnt/?/Xilinx/Vivado/*/bin/vivado.bat', '/mnt/?/Xilinx/*/Vivado/bin/vivado.bat',
    '/mnt/?/AMD/*/Vivado/bin/vivado.bat',
]


def find_vivado():
    """Candidate Vivado executables, newest first: PATH, then the usual install locations."""
    found = []
    on_path = shutil.which('vivado')
    if on_path:
        found.append(on_path)
    hits = sorted({h for g in VIVADO_GLOBS for h in glob.glob(g)}, reverse=True)
    found += [h for h in hits if h not in found]
    return found


SKIP_DIRS = {'.git', '.Xil', '.vsweep', 'vsweep_runs', 'node_modules', '.venv'}


def find_files(root, suffixes, max_depth=6, skip_project_dirs=True):
    """Files under root ending with one of suffixes, skipping VCS, output and Vivado-generated dirs."""
    root = Path(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not (
            skip_project_dirs and d.endswith(('.runs', '.cache', '.sim', '.hw', '.gen', '.ip_user_files')))
            and depth < max_depth)
        out += [Path(dirpath) / f for f in sorted(filenames) if f.endswith(tuple(suffixes))]
    return out
