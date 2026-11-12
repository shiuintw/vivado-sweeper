"""Where a build happens: the project itself, or (with --parallel) a private copy of it.

A Vivado project has one synth_1/impl_1, so two builds at once need two projects. A clone
copies project root, minus the top-level synthesis/implementation runs and the caches, into
<root>/.vsweep/ws/wN. Out-of-context IP runs are copied too, so IP is not synthesized again.

The copy only isolates the .vh edits if the .xpr reaches its sources through paths relative
to itself ($PPRDIR/...) that stay inside root -- check_clonable() makes sure of that.
"""
import os
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

from .vivado import from_windows


class WorkspaceError(Exception):
    pass


class Workspace:
    def __init__(self, cfg, root=None, name='main'):
        self.cfg = cfg
        self.root = Path(root) if root else cfg.root
        self.name = name
        self.is_clone = root is not None

    def map(self, path):
        """A path of the main project, in this workspace."""
        path = Path(path)
        if not self.is_clone:
            return path
        return self.root / path.relative_to(self.cfg.root)

    @property
    def xpr(self):
        return self.map(self.cfg.xpr)

    @property
    def synth_dir(self):
        return self.map(self.cfg.runs_dir) / self.cfg.synth_run


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _xpr_paths(xpr):
    """Every path-like attribute value in the .xpr, except the project's own Path."""
    try:
        tree = ET.parse(xpr)
    except (ET.ParseError, OSError) as e:
        raise WorkspaceError('cannot read %s: %s' % (xpr, e))
    for el in tree.iter():
        if el.tag == 'Project':
            continue
        for attr, value in el.attrib.items():
            for part in re.split(r'[;|]', value):
                part = part.strip()
                if part.startswith('$P') or re.match(r'^(/|[A-Za-z]:[\\/])', part):
                    yield el.tag, attr, part


def check_clonable(cfg):
    """(errors, warnings) for copying the project; errors mean --parallel cannot work."""
    errors, warnings = [], []
    for p in [cfg.xpr] + list(cfg.vh):
        if not _inside(p, cfg.root):
            errors.append('%s is outside project root %s; a copy of root would not contain it' % (p, cfg.root))
    if errors:
        return errors, warnings
    xdir = cfg.xpr.parent
    roots = {'$PPRDIR': xdir, '$PSRCDIR': xdir / (cfg.project_name + '.srcs'),
             '$PGENDIR': xdir / (cfg.project_name + '.gen'), '$PRUNDIR': cfg.runs_dir,
             '$PCACHEDIR': xdir / (cfg.project_name + '.cache'),
             '$PIPUSERFILESDIR': xdir / (cfg.project_name + '.ip_user_files')}
    vh = {str(Path(v).resolve()) for v in cfg.vh}
    seen = set()
    for tag, attr, value in _xpr_paths(cfg.xpr):
        m = re.match(r'^(\$\w+)(.*)$', value)
        if m:
            if m.group(1) not in roots:
                continue
            resolved = os.path.normpath(str(roots[m.group(1)]) + m.group(2).replace('\\', '/'))
            if not _inside(resolved, cfg.root) and resolved not in seen:
                seen.add(resolved)
                errors.append('the .xpr uses %s (%s), outside project root: a copy would look for it at '
                              'the same relative place and not find it. Make root a directory that '
                              'contains it.' % (value, resolved))
            continue
        local = os.path.normpath(from_windows(value.replace('\\', '/')))
        if local in seen or not _inside(local, cfg.root):
            continue
        seen.add(local)
        resolved = Path(local).resolve()
        if str(resolved) in vh:
            errors.append('the .xpr names %s by absolute path, so every copy would build with the '
                          'original file. Re-add it to the project by a relative path.' % value)
        elif any(_inside(v, resolved) for v in vh) and resolved.is_dir():
            errors.append('the .xpr names the directory %s by absolute path (an include directory?), so '
                          'every copy would `include the original .vh from it. Make it relative to the '
                          'project.' % value)
        else:
            warnings.append('the .xpr names %s by absolute path: every copy reads the original '
                            '(fine for a file vsweep never edits)' % value)
    return errors, warnings


def ignore_for(cfg):
    root, runs = cfg.root.resolve(), cfg.runs_dir.resolve()
    skip_paths = {root / '.vsweep', (cfg.output_dir or root / 'vsweep_runs').resolve()}
    skip_runs = {cfg.synth_run, cfg.impl_run}

    def ignore(dirpath, names):
        d = Path(dirpath).resolve()
        out = set()
        for n in names:
            p = d / n
            if p in skip_paths or n in ('.git', '.Xil'):
                out.add(n)
            elif n.endswith(('.cache', '.sim')) and p.is_dir():
                out.add(n)
            elif d == runs and n in skip_runs:
                out.add(n)
            elif d == root and (n.endswith('.jou') or re.match(r'^vivado(_pid\d+)?\.(log|str)$', n)):
                out.add(n)
        return out
    return ignore


def size_of(cfg):
    """Bytes a clone would copy."""
    ignore, total = ignore_for(cfg), 0
    for dirpath, dirnames, filenames in os.walk(cfg.root):
        skipped = ignore(dirpath, dirnames + filenames)
        dirnames[:] = [d for d in dirnames if d not in skipped]
        for f in filenames:
            if f not in skipped:
                try:
                    total += os.lstat(os.path.join(dirpath, f)).st_size
                except OSError:
                    pass
    return total


def clone(cfg, index):
    dest = cfg.root / '.vsweep' / 'ws' / ('w%d' % index)
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(cfg.root, dest, symlinks=True, ignore=ignore_for(cfg), copy_function=shutil.copy2)
    return Workspace(cfg, dest, 'w%d' % index)


def remove(ws):
    if ws.is_clone and ws.root.exists():
        shutil.rmtree(ws.root, ignore_errors=True)


def remove_all(cfg):
    """Delete <root>/.vsweep/ws: every copy, finished or not (only vsweep writes there)."""
    shutil.rmtree(cfg.root / '.vsweep' / 'ws', ignore_errors=True)


__all__ = ['Workspace', 'WorkspaceError', 'check_clonable', 'clone', 'remove', 'remove_all', 'size_of']
