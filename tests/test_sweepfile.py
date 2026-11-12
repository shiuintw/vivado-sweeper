import pytest

from vivado_sweeper.sweepfile import SweepError, load_text, snake


def names(sweep):
    return [r.name for r in sweep.runs]


def test_chain_inheritance_as_in_the_readme():
    s = load_text('{a=x} {b=y} {c=z, a=z}')
    assert [r.settings for r in s.runs] == [{'a': 'x'}, {'a': 'x', 'b': 'y'}, {'a': 'z', 'b': 'y', 'c': 'z'}]
    assert names(s) == ['run01', 'run02', 'run03']
    assert [r.parent for r in s.runs] == ['base', 'run01', 'run02']


def test_base_named_runs_explicit_parent():
    s = load_text('''
        # comment
        base { DEBUG=off, DEPTH=16 }   // another comment
        baseline: {}
        deep:     {DEPTH=32}
        wide:     {WIDTH=64}
        alt < baseline: {WIDTH=128}
        fresh < base {X=1}
        next: {Y=2}
    ''')
    st = {r.name: r.settings for r in s.runs}
    assert st['baseline'] == {'DEBUG': False, 'DEPTH': '16'}
    assert st['wide'] == {'DEBUG': False, 'DEPTH': '32', 'WIDTH': '64'}
    assert st['alt'] == {'DEBUG': False, 'DEPTH': '16', 'WIDTH': '128'}
    assert st['fresh'] == {'DEBUG': False, 'DEPTH': '16', 'X': '1'}
    assert st['next'] == {'DEBUG': False, 'DEPTH': '16', 'X': '1', 'Y': '2'}   # chain continues from fresh
    assert s.runs[3].parent == 'baseline' and s.runs[3].explicit_parent


def test_values():
    s = load_text('''r: {A=on, B=OFF, C=true, D=false, E=16'hBEEF, F="str", G=2.5, H=-3, I=word}''')
    assert s.runs[0].settings == {'A': True, 'B': False, 'C': True, 'D': False, 'E': "16'hBEEF",
                                  'F': '"str"', 'G': '2.5', 'H': '-3', 'I': 'word'}


def test_newlines_separate_items():
    s = load_text('r: {\n  A=1\n  B=2,\n\n  C=3\n}\n')
    assert s.runs[0].settings == {'A': '1', 'B': '2', 'C': '3'}


def test_grid_snake_order_and_names():
    s = load_text('pre: {X=0}\ng: {DEPTH=[16,32], MODE=[on,off], K=1}\nafter: {Z=9}')
    grid = s.runs[1:5]
    assert [r.name for r in grid] == ['g_DEPTH16_MODEon', 'g_DEPTH16_MODEoff', 'g_DEPTH32_MODEoff', 'g_DEPTH32_MODEon']
    for r in grid:
        assert r.parent == 'pre' and r.settings['K'] == '1' and r.settings['X'] == '0'
    for a, b in zip(grid, grid[1:]):
        assert sum(a.settings[k] != b.settings[k] for k in a.settings) == 1
    assert s.runs[5].parent == 'g_DEPTH32_MODEon'


def test_snake_three_axes_moves_one_key_per_step():
    combos = snake([('a', [1, 2, 3]), ('b', [1, 2]), ('c', [1, 2, 3])])
    assert len(combos) == 18 and len({tuple(c.items()) for c in combos}) == 18
    for x, y in zip(combos, combos[1:]):
        assert sum(x[k] != y[k] for k in x) == 1


def test_unnamed_grid():
    s = load_text("{A=1} {B=[1,2]}")
    assert names(s) == ['run01', 'run02_B1', 'run02_B2']


@pytest.mark.parametrize('text, msg', [
    ('{A=1', 'missing'),
    ('{A=1 B=2}', "expected ',' or a new line"),
    ('{A=}', 'expected a value'),
    ('{A=1, A=2}', 'set twice'),
    ('x < nope: {}', 'no run named nope'),
    ('base {A=1} base {B=1} {}', 'base is defined twice'),
    ('{} base {A=1}', 'before the first run'),
    ('base {A=[1,2]} {}', 'cannot hold a grid'),
    ('a: {} a: {}', 'already taken'),
    ('g: {A=[1,2]} x < g: {}', 'is a grid of 2 runs'),
    ('{A=[]}', 'empty list'),
    ('{A="x y"}', 'whitespace'),
    ('base {A=1}', 'no runs'),
    ('{A=1/2}', "expected ','"),
])
def test_errors(text, msg):
    with pytest.raises(SweepError, match=msg):
        load_text(text)


def test_error_reports_line():
    with pytest.raises(SweepError, match=r'<sweep>:3:'):
        load_text('a: {}\n\nb: {X=}\n')
