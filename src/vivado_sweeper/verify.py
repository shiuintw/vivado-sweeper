"""Does the synthesized design match the .vh it was built from?

The synthesis log is the only reliable record of what was built: it lists every module
synthesized and the value each parameter was bound to. [verify] in vsweep.toml names what to
check for each define:

    DEPTH     = "fifo.DEPTH"   fifo's DEPTH must be bound to `define DEPTH's value
    USE_CACHE = "cache"        module cache is synthesized exactly when `define USE_CACHE is on
"""
import os
import re

from .reports import parse_vds
from .vh import fmt

LITERAL_RE = re.compile(r"^(\d*)'[sS]?([bodhBODH])([0-9a-fA-F_xXzZ?]+)$")


def normalize(value):
    """A comparable form of a define or parameter value: Verilog literals and numbers become
    numbers (32'h10 == 16 == 16.000000), strings lose their quotes."""
    v = str(value).strip().strip('"')
    m = LITERAL_RE.match(v)
    if m:
        digits = m.group(3).replace('_', '')
        if re.search(r'[xXzZ?]', digits):
            return v.lower()
        return int(digits, {'b': 2, 'o': 8, 'd': 10, 'h': 16}[m.group(2).lower()])
    try:
        return float(v.replace('_', ''))
    except ValueError:
        return v


def same_value(a, b):
    return normalize(a) == normalize(b)


def check_build(vds_text, state, verify_map):
    """Problems (empty = matches) between a synthesis log and a define state."""
    problems = []
    blocks = parse_vds(vds_text)['blocks']
    present = {mod for mod, _ in blocks}
    for name, target in verify_map.items():
        if name not in state:
            problems.append('verify.%s: no `define %s in the .vh files' % (name, name))
            continue
        value = state[name]
        if '.' in target:
            mod, param = target.split('.', 1)
            if value is False:
                continue                      # off: the parameter falls back to whatever default
            if value is True:
                problems.append('verify.%s = "%s": %s is a flag; name a module ("%s") to check it'
                                % (name, target, name, mod))
                continue
            if mod not in present:
                problems.append('%s: module %s was not synthesized, so %s cannot be checked' % (name, mod, target))
                continue
            seen = [p[param] for b, p in blocks if b == mod and param in p]
            if not seen:
                problems.append('%s: the synthesis log binds no %s' % (name, target))
            elif not all(same_value(v, value) for v in seen):
                problems.append('%s = %s but %s was bound to %s' % (name, value, target, ', '.join(sorted(set(seen)))))
        else:
            on = value is not False
            if on and target not in present:
                problems.append('%s is %s but module %s was not synthesized' % (name, fmt(value), target))
            elif not on and target in present:
                problems.append('%s is off but module %s was synthesized' % (name, target))
    return problems


def check_fresh(vds_text, before_session):
    """A problem if the synthesis log is the one that was there before the build."""
    session = parse_vds(vds_text)['session']
    if session is None:
        return ['the synthesis log has no "Start of session" line']
    if before_session is not None and session == before_session:
        return ['the synthesis log is from an earlier session (%s): synthesis did not re-run' % session]
    return []


def check_stale(vds_path, vh_paths):
    """For `vsweep check`: a problem if a .vh changed after the synthesis log was written."""
    vds_m = os.path.getmtime(vds_path)
    newer = [p for p in vh_paths if os.path.getmtime(p) > vds_m + 1]
    return ['%s changed after the last synthesis (stale netlist)' % p for p in newer]
