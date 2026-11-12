"""The sweep file: which define settings each run builds with.

    # comments start with # or //
    base { DEBUG=off, DEPTH=16 }           # applied to every run; not a run itself
    baseline: {}                            # first run: inherits base
    deep:     {DEPTH=32}                    # inherits the previous run (baseline)
    wide:     {WIDTH=64}                    # DEPTH=32 still holds
    alt < baseline: {WIDTH=128}             # inherit a named run instead; `< base` starts over
    grid:     {DEPTH=[16,32], MODE=[on,off]}  # cartesian product, in snake order
    {A=1} {B=2} {C=3, A=3}                  # names are optional: run07, run08, ...

Values: on / true turn a `define on, off / false comment it out, anything else (a number,
16'hBEEF, "string", a bare word) is written as the define's value, exactly as given.
A value is one token: no whitespace, no "/" (that is what a .vh `define line can hold).

A grid's points are ordered so that consecutive points differ in exactly one key; they are
named <name>_<KEY><value>..., and the run after a grid inherits its last point.
"""
import re
from dataclasses import dataclass, field

NAME_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_.\-]*')
KEY_RE = re.compile(r'[A-Za-z_]\w*')
BARE_RE = re.compile(r'[^\s,{}\[\]#"/]+')
SAFE_RE = re.compile(r'[^A-Za-z0-9_.\-]')


class SweepError(Exception):
    pass


@dataclass
class Block:
    line: int
    name: str | None = None
    parent: str | None = None
    is_base: bool = False
    items: dict = field(default_factory=dict)        # key -> value, or list of values (grid axis)
    key_lines: dict = field(default_factory=dict)    # key -> line it was written on


@dataclass
class Run:
    index: int                                       # 1-based position in the sweep
    name: str
    parent: str                                      # 'base' or the name of the run it inherits
    overrides: dict                                  # what this run's block (or grid point) sets
    settings: dict                                   # base + every ancestor + overrides
    line: int
    key_lines: dict                                  # key -> line, for every key in settings
    explicit_parent: bool = False                    # written `name < parent:`
    grid_pos: int = 0                                # position inside its grid block (0 = first or none)
    grid: bool = False                               # one point of a grid block


@dataclass
class Sweep:
    source: str
    base: dict
    base_lines: dict
    runs: list


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
class _Scanner:
    def __init__(self, text, source):
        self.s, self.i, self.line, self.source = text, 0, 1, source

    def error(self, msg, line=None):
        raise SweepError('%s:%d: %s' % (self.source, line or self.line, msg))

    def peek(self):
        return self.s[self.i] if self.i < len(self.s) else ''

    def advance(self, n=1):
        chunk = self.s[self.i:self.i + n]
        self.line += chunk.count('\n')
        self.i += len(chunk)
        return chunk

    def skip(self, newlines=True):
        """Skip blanks and comments; newlines too unless newlines=False."""
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in ' \t\r' or (newlines and c == '\n'):
                self.advance()
            elif c == '#' or self.s.startswith('//', self.i):
                j = self.s.find('\n', self.i)
                self.advance((len(self.s) if j < 0 else j) - self.i)
            else:
                break

    def match(self, regex):
        m = regex.match(self.s, self.i)
        if not m:
            return None
        self.advance(len(m.group(0)))
        return m.group(0)

    def expect(self, ch, what):
        if self.peek() != ch:
            got = repr(self.peek()) if self.peek() else 'end of file'
            self.error('expected %s, got %s' % (what, got))
        self.advance()

    # value := '[' scalar (',' scalar)* ']' | scalar
    def value(self):
        if self.peek() != '[':
            return self.scalar()
        line = self.line
        self.advance()
        values = []
        while True:
            self.skip()
            if self.peek() == ']':
                break
            values.append(self.scalar())
            self.skip()
            if self.peek() == ',':
                self.advance()
            elif self.peek() != ']':
                self.error("expected ',' or ']' in a list")
        self.advance()
        if not values:
            self.error('empty list: a grid axis needs at least one value', line)
        return values

    def scalar(self):
        if self.peek() == '"':
            j = self.s.find('"', self.i + 1)
            nl = self.s.find('\n', self.i + 1)
            if j < 0 or (0 <= nl < j):
                self.error('unterminated string')
            token = self.advance(j + 1 - self.i)
        else:
            token = self.match(BARE_RE)
            if token is None:
                got = repr(self.peek()) if self.peek() else 'end of file'
                self.error('expected a value, got %s' % got)
        if re.search(r'\s', token):
            self.error('%s: a value cannot contain whitespace (a .vh `define reads its value as one token)'
                       % token)
        low = token.lower()
        if low in ('on', 'true'):
            return True
        if low in ('off', 'false'):
            return False
        return token

    def block(self, b):
        self.expect('{', "'{'")
        while True:
            self.skip()
            while self.peek() == ',':
                self.advance()
                self.skip()
            if self.peek() == '}':
                self.advance()
                return b
            if not self.peek():
                self.error("unterminated block: missing '}'", b.line)
            line = self.line
            key = self.match(KEY_RE)
            if key is None:
                self.error('expected a define name, got %r' % self.peek())
            self.skip(newlines=False)
            self.expect('=', "'=' after %s" % key)
            self.skip(newlines=False)
            value = self.value()
            if key in b.items:
                self.error('%s is set twice in one block (first on line %d)' % (key, b.key_lines[key]), line)
            b.items[key], b.key_lines[key] = value, line
            self.skip(newlines=False)
            if self.peek() not in (',', '\n', '}', ''):
                self.error("expected ',' or a new line after %s=..., got %r" % (key, self.peek()))

    # block-stmt := 'base' ':'? block | NAME? ('<' NAME)? ':'? block
    def statements(self):
        blocks = []
        while True:
            self.skip()
            if not self.peek():
                return blocks
            b = Block(line=self.line)
            if self.peek() not in '{<':
                b.name = self.match(NAME_RE)
                if b.name is None:
                    self.error("expected a run name, '<' or '{', got %r" % self.peek())
                b.is_base = b.name == 'base'
                if b.is_base:
                    b.name = None
                self.skip()
            if self.peek() == '<':
                if b.is_base:
                    self.error('base cannot inherit from anything')
                self.advance()
                self.skip()
                b.parent = self.match(NAME_RE)
                if b.parent is None:
                    self.error("expected a run name (or 'base') after '<'")
                self.skip()
            if self.peek() == ':':
                self.advance()
                self.skip()
            blocks.append(self.block(b))


def parse(text, source='<sweep>'):
    return _Scanner(text, source).statements()


# ---------------------------------------------------------------------------
# Expansion: blocks -> runs
# ---------------------------------------------------------------------------
def snake(axes):
    """Every combination of axes [(key, [values])], ordered so that consecutive combinations
    differ in exactly one key (a reflected mixed-radix Gray code; the first axis moves slowest)."""
    if not axes:
        return [{}]
    (key, values), rest = axes[0], snake(axes[1:])
    out = []
    for i, v in enumerate(values):
        for combo in (rest if i % 2 == 0 else rest[::-1]):
            out.append({key: v, **combo})
    return out


def label(value):
    return 'on' if value is True else 'off' if value is False else SAFE_RE.sub('', str(value))


def expand(blocks, source='<sweep>'):
    base, base_lines = {}, {}
    runs, by_name = [], {}
    grids = {}                                      # grid block name -> its point names
    seen_base = False
    for b in blocks:
        where = '%s:%d' % (source, b.line)
        if b.is_base:
            if seen_base:
                raise SweepError('%s: base is defined twice' % where)
            if runs:
                raise SweepError('%s: base must come before the first run' % where)
            lists = [k for k, v in b.items.items() if isinstance(v, list)]
            if lists:
                raise SweepError('%s: base cannot hold a grid (%s); put the list in a run' % (where, ', '.join(lists)))
            base, base_lines, seen_base = dict(b.items), dict(b.key_lines), True
            continue

        if b.parent is None:
            parent = runs[-1] if runs else None
        elif b.parent == 'base':
            parent = None
        elif b.parent in by_name:
            parent = by_name[b.parent]
        elif b.parent in grids:
            raise SweepError('%s: %s is a grid of %d runs; inherit one of them: %s'
                             % (where, b.parent, len(grids[b.parent]), ', '.join(grids[b.parent])))
        else:
            raise SweepError('%s: no run named %s before this line' % (where, b.parent))
        p_settings = parent.settings if parent else base
        p_lines = parent.key_lines if parent else base_lines
        p_name = parent.name if parent else 'base'

        axes = [(k, v) for k, v in b.items.items() if isinstance(v, list)]
        fixed = {k: v for k, v in b.items.items() if not isinstance(v, list)}
        points = snake(axes)
        names = []
        stem = b.name or 'run%02d' % (len(runs) + 1)      # an unnamed grid is named after its first index
        for pos, point in enumerate(points):
            index = len(runs) + 1
            if axes:
                suffix = '_'.join('%s%s' % (k, label(point[k])) for k, _ in axes)
                name = '%s_%s' % (stem, suffix)
            else:
                name = b.name or 'run%02d' % index
            if name in by_name or name in grids or name == 'base':
                raise SweepError('%s: run name %s is already taken' % (where, name))
            overrides = {k: b.items[k] if k in fixed else point[k] for k in b.items}
            run = Run(index=index, name=name, parent=p_name, overrides=overrides,
                      settings={**p_settings, **overrides}, line=b.line,
                      key_lines={**p_lines, **b.key_lines},
                      explicit_parent=b.parent is not None, grid_pos=pos, grid=bool(axes))
            runs.append(run)
            by_name[name] = run
            names.append(name)
            # inside a grid every point inherits the block's parent, not the previous point
        if axes and b.name:
            grids[b.name] = names
    if not runs:
        raise SweepError('%s: no runs (a sweep needs at least one {...} block besides base)' % source)
    return Sweep(source=source, base=base, base_lines=base_lines, runs=runs)


def load_text(text, source='<sweep>'):
    return expand(parse(text, source), source)


def load(path):
    with open(path, encoding='utf-8') as f:
        return load_text(f.read(), str(path))
