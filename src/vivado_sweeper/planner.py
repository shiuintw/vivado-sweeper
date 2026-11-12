"""From a parsed sweep and the .vh files to the exact bytes each run builds with."""
import fnmatch
import hashlib
from dataclasses import dataclass

from .vh import VhError, decode, describe_changes, fmt, parse_defines


class PlanError(Exception):
    def __init__(self, problems):
        super().__init__('\n'.join(problems))
        self.problems = problems


@dataclass
class Planned:
    run: object                     # sweepfile.Run
    dirname: str                    # NN_name
    rendered: dict                  # vh path -> bytes
    state: dict                     # every define after this run's settings
    changes: list                   # vs the files as they are
    reference: str | None           # the run (or 'base') `delta` is measured against
    delta: list                     # vs reference
    digest: str                     # sha256 of the rendered files: identifies the build input

    @property
    def name(self):
        return self.run.name


def _validate(sweep, vhset):
    problems, seen = [], set()

    def check(key, value, line, where):
        if (key, line) in seen:
            return
        seen.add((key, line))
        if key not in vhset.state:
            problems.append('%s:%d: %s: no `define %s in %s%s' % (
                sweep.source, line, where, key, ', '.join(vhset.paths), dict(vhset.unknown([key]))[key]))
        elif isinstance(value, str) and not vhset.has_value[key]:
            problems.append('%s:%d: %s: `define %s is a flag (it has no value); use on/off, not %s'
                            % (sweep.source, line, where, key, value))

    for k, v in sweep.base.items():
        check(k, v, sweep.base_lines[k], 'base')
    for r in sweep.runs:
        for k, v in r.overrides.items():
            check(k, v, r.key_lines[k], r.name)
    return problems


def make_plan(sweep, vhset):
    """[Planned] for every run in the sweep, and warnings. Raises PlanError on bad keys/values."""
    problems = _validate(sweep, vhset)
    if problems:
        raise PlanError(problems)
    width = max(2, len(str(len(sweep.runs))))
    out, by_name, warnings = [], {}, []
    base_state = None
    for r in sweep.runs:
        try:
            rendered = vhset.render(r.settings)
        except VhError as e:
            raise PlanError(['%s:%d: %s: %s' % (sweep.source, r.line, r.name, e)])
        state = {}
        for path, data in rendered.items():
            state.update(parse_defines(decode(data), path))
        # What this run is an experiment against: the run it names, or else the one before it.
        if r.explicit_parent and r.grid_pos == 0:
            if r.parent == 'base':
                if base_state is None:
                    base_state = vhset.state_after(sweep.base)
                ref_name, ref_state = 'base', base_state
            else:
                ref_name, ref_state = r.parent, by_name[r.parent].state
        elif out:
            ref_name, ref_state = out[-1].name, out[-1].state
        else:
            ref_name, ref_state = None, None
        delta = describe_changes(ref_state, state) if ref_state is not None else []
        # One variable per step. A grid is exempt: its points are ordered to move one key at a
        # time, and its first point moves every axis away from the parent by design.
        if ref_name not in (None, 'base') and len(delta) > 1 and not r.grid:
            warnings.append('%s -> %s moves %d defines (%s)' % (ref_name, r.name, len(delta), ', '.join(delta)))
        h = hashlib.sha256()
        for path in sorted(rendered):
            h.update(path.encode() + b'\0' + rendered[path] + b'\0')
        p = Planned(run=r, dirname='%0*d_%s' % (width, r.index, r.name), rendered=rendered, state=state,
                    changes=describe_changes(vhset.state, state), reference=ref_name, delta=delta,
                    digest=h.hexdigest())
        out.append(p)
        by_name[r.name] = p
    return out, warnings


def select(planned, only):
    """The runs named by --only (exact names or shell patterns), in sweep order."""
    if not only:
        return list(planned)
    names = [p.name for p in planned]
    missing = [o for o in only if not any(fnmatch.fnmatchcase(n, o) for n in names)]
    if missing:
        raise PlanError(['--only %s matches no run; runs: %s' % (', '.join(missing), ', '.join(names))])
    return [p for p in planned if any(fnmatch.fnmatchcase(p.name, o) for o in only)]


def describe(planned, warnings, vhset, base=None):
    lines = []
    if base:
        lines.append('base: ' + ', '.join('%s=%s' % (k, fmt(v)) for k, v in base.items()))
    for p in planned:
        lines.append('%-28s <- %s' % (p.dirname, p.run.parent))
        lines.append('    vs file: %s' % (', '.join(p.changes) or '(no change)'))
        if p.reference:
            lines.append('    vs %s: %s' % (p.reference, ', '.join(p.delta) or '(identical)'))
    for w in warnings:
        lines.append('warning: ' + w)
    return '\n'.join(lines)
