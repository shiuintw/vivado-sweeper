import pytest

from vivado_sweeper.planner import PlanError, describe, make_plan, select
from vivado_sweeper.sweepfile import load_text
from vivado_sweeper.vh import VhSet


@pytest.fixture
def vhset(tmp_path):
    p = tmp_path / 'cfg.vh'
    p.write_text('`define DEPTH 16\n`define WIDTH 8\n// `define FAST\n`define MODE\n')
    return VhSet([p])


def test_plan(vhset):
    sweep = load_text('base {FAST=on}\nb: {}\nd: {DEPTH=32}\nw: {WIDTH=16, MODE=off}\nalt < b: {WIDTH=4}')
    planned, warnings = make_plan(sweep, vhset)
    assert [p.dirname for p in planned] == ['01_b', '02_d', '03_w', '04_alt']
    assert planned[1].changes == ['DEPTH: 16 -> 32', 'FAST: off -> on']
    assert planned[1].reference == 'b' and planned[1].delta == ['DEPTH: 16 -> 32']
    assert planned[3].reference == 'b' and planned[3].delta == ['WIDTH: 8 -> 4']
    assert warnings == ['d -> w moves 2 defines (WIDTH: 8 -> 16, MODE: on -> off)']
    assert len({p.digest for p in planned}) == 4
    assert 'vs file' in describe(planned, warnings, vhset, sweep.base)


def test_plan_errors_point_at_lines(vhset):
    with pytest.raises(PlanError) as e:
        make_plan(load_text('a: {DEPHT=1}\nb: {MODE=3}'), vhset)
    assert e.value.problems[0].startswith('<sweep>:1: a: no `define DEPHT') and 'did you mean DEPTH' in e.value.problems[0]
    assert '<sweep>:2: b: `define MODE is a flag' in e.value.problems[1]


def test_select(vhset):
    planned, _ = make_plan(load_text('a: {} g: {DEPTH=[1,2]}'), vhset)
    assert [p.name for p in select(planned, ['g_*'])] == ['g_DEPTH1', 'g_DEPTH2']
    with pytest.raises(PlanError, match='matches no run'):
        select(planned, ['zzz'])


def test_grid_points_do_not_warn(vhset):
    _, warnings = make_plan(load_text('a: {} g < a: {DEPTH=[1,2], WIDTH=[3,4]}'), vhset)
    assert warnings == []
