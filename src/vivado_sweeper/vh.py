"""Reading and editing `define lines in Verilog header (.vh) files.

A define is in one of three states:

    `define NAME VALUE      -> 'VALUE' (a string, exactly as written)
    `define NAME            -> True    (a flag that is on)
    // `define NAME [VALUE] -> False   (commented out)

Settings use the same three kinds of value: True turns a define on (uncommenting it,
and keeping its value if it has one), False comments it out, and a string sets its
value (and turns it on). Every other byte of the file is left exactly as it was,
including line endings.
"""
import difflib
import re

DEFINE_RE = re.compile(r'^(?P<indent>[ \t]*)(?P<off>//[ \t]*)?`define[ \t]+(?P<name>\w+)'
                       r'(?:(?P<sp>[ \t]+)(?P<val>[^\s/]+))?(?P<rest>.*)$')
VALUE_RE = re.compile(r'^[^\s/]+$')     # what DEFINE_RE can read back as a value


class VhError(Exception):
    pass


def fmt(value):
    return 'on' if value is True else 'off' if value is False else str(value)


def decode(data):
    # surrogateescape: any byte sequence survives a decode/encode round trip unchanged
    return data.decode('utf-8', 'surrogateescape')


def encode(text):
    return text.encode('utf-8', 'surrogateescape')


def parse_defines(text, source='<vh>'):
    """{name: True | False | value string}, in file order."""
    state, first = {}, {}
    for lineno, line in enumerate(text.splitlines(), 1):
        m = DEFINE_RE.match(line)
        if not m:
            continue
        name = m.group('name')
        if name in state:
            raise VhError('%s:%d: `define %s appears twice (first on line %d); refusing to guess '
                          'which one counts' % (source, lineno, name, first[name]))
        first[name] = lineno
        if m.group('off'):
            state[name] = False
        else:
            state[name] = m.group('val') if m.group('val') is not None else True
    return state


def check_value(name, value):
    if isinstance(value, bool):
        return
    if not isinstance(value, str) or not VALUE_RE.match(value):
        raise VhError('%s = %r: a value must be one token with no whitespace and no "/"' % (name, value))


def apply_settings(text, settings, source='<vh>'):
    """text with settings applied. Every key must already have a `define line in text."""
    state = parse_defines(text, source)
    missing = [k for k in settings if k not in state]
    if missing:
        raise VhError('%s has no `define for: %s' % (source, ', '.join(missing)))
    for name, value in settings.items():
        check_value(name, value)
    out = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip('\r\n')
        eol = line[len(body):]
        m = DEFINE_RE.match(body)
        if not m or m.group('name') not in settings:
            out.append(line)
            continue
        name, want = m.group('name'), settings[m.group('name')]
        head = m.group('indent') + ('//' if want is False else '') + '`define ' + name
        if isinstance(want, bool):
            if m.group('val') is not None:              # toggling a value define keeps its value
                head += m.group('sp') + m.group('val')
        else:
            if m.group('val') is None:
                raise VhError('%s: `define %s is a flag (it has no value); use on/off, not %r'
                              % (source, name, want))
            head += m.group('sp') + want
        out.append(head + m.group('rest') + eol)
    return ''.join(out)


def describe_changes(old, new):
    """['NAME: old -> new', ...] between two define states, in the order of `new`."""
    return ['%s: %s -> %s' % (k, fmt(old.get(k)), fmt(v)) for k, v in new.items() if old.get(k) != v]


def suggest(name, known):
    close = difflib.get_close_matches(name, known, n=3, cutoff=0.6)
    return ' (did you mean %s?)' % ', '.join(close) if close else ''


class VhSet:
    """One or more .vh files treated as a single namespace of defines.

    The files' bytes are read once, here, and everything else is computed from them, so
    a sweep's runs are always rendered from the files as they were when it started."""

    def __init__(self, paths):
        self.paths = [str(p) for p in paths]
        self.original = {}
        self.text = {}
        self.owner = {}                                   # define name -> path
        self.state = {}                                   # define name -> value, all files
        self.has_value = {}                               # define name -> it carries a value
        self.line_of = {}                                 # define name -> line number in its file
        for path in self.paths:
            with open(path, 'rb') as f:
                data = f.read()
            self.original[path] = data
            self.text[path] = decode(data)
            for name, value in parse_defines(self.text[path], path).items():
                if name in self.owner:
                    raise VhError('`define %s is in both %s and %s; a swept name must be unique'
                                  % (name, self.owner[name], path))
                self.owner[name] = path
                self.state[name] = value
            for lineno, line in enumerate(self.text[path].splitlines(), 1):
                m = DEFINE_RE.match(line)
                if m:
                    self.has_value[m.group('name')] = m.group('val') is not None
                    self.line_of[m.group('name')] = lineno

    def unknown(self, settings):
        """[(name, hint)] for settings with no `define in any file."""
        return [(k, suggest(k, list(self.state))) for k in settings if k not in self.state]

    def render(self, settings):
        """{path: bytes} for every file, with settings applied."""
        per_file = {p: {} for p in self.paths}
        for name, value in settings.items():
            if name not in self.owner:
                raise VhError('no `define %s in %s%s' % (name, ', '.join(self.paths),
                                                         suggest(name, list(self.state))))
            per_file[self.owner[name]][name] = value
        return {p: encode(apply_settings(self.text[p], per_file[p], p)) if per_file[p] else self.original[p]
                for p in self.paths}

    def state_after(self, settings):
        """The define state the files would have with settings applied (rendered and re-read)."""
        state = {}
        for path, data in self.render(settings).items():
            state.update(parse_defines(decode(data), path))
        return state
