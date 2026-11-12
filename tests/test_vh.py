import pytest

from vivado_sweeper.vh import VhError, VhSet, apply_settings, describe_changes, parse_defines

TEXT = (
    '// config\r\n'
    '`define DEPTH 16   // fifo depth\r\n'
    '  // `define USE_CACHE\r\n'
    '`define FAST\r\n'
    "//`define CLK_MHZ 100\r\n"
)


def test_parse():
    assert parse_defines(TEXT) == {'DEPTH': '16', 'USE_CACHE': False, 'FAST': True, 'CLK_MHZ': False}


def test_apply_keeps_everything_else_byte_for_byte():
    out = apply_settings(TEXT, {'DEPTH': '32', 'USE_CACHE': True, 'FAST': False})
    assert out == (
        '// config\r\n'
        '`define DEPTH 32   // fifo depth\r\n'
        '  `define USE_CACHE\r\n'
        '//`define FAST\r\n'
        "//`define CLK_MHZ 100\r\n"
    )


def test_toggle_value_define_keeps_its_value_and_setting_turns_it_on():
    assert "`define CLK_MHZ 100\r\n" in apply_settings(TEXT, {'CLK_MHZ': True})
    assert "`define CLK_MHZ 8'd200\r\n" in apply_settings(TEXT, {'CLK_MHZ': "8'd200"})
    assert '//`define DEPTH 16   // fifo depth' in apply_settings(TEXT, {'DEPTH': False})


def test_no_settings_is_identity():
    assert apply_settings(TEXT, {}) == TEXT


def test_errors():
    with pytest.raises(VhError, match='no `define for: NOPE'):
        apply_settings(TEXT, {'NOPE': True})
    with pytest.raises(VhError, match='is a flag'):
        apply_settings(TEXT, {'FAST': '3'})
    with pytest.raises(VhError, match='one token'):
        apply_settings(TEXT, {'DEPTH': '1 + 2'})
    with pytest.raises(VhError, match='appears twice'):
        parse_defines('`define A 1\n`define A 2\n')


def test_describe_changes():
    old = parse_defines(TEXT)
    new = parse_defines(apply_settings(TEXT, {'DEPTH': '32', 'FAST': False}))
    assert describe_changes(old, new) == ['DEPTH: 16 -> 32', 'FAST: on -> off']


def test_vhset_multiple_files(tmp_path):
    a, b = tmp_path / 'a.vh', tmp_path / 'b.vh'
    a.write_bytes(b'`define A 1\n')
    b.write_bytes(b'`define B\n\xff\xfe not utf-8\n')
    s = VhSet([a, b])
    assert s.state == {'A': '1', 'B': True}
    out = s.render({'B': False})
    assert out[str(a)] == a.read_bytes()
    assert out[str(b)] == b'//`define B\n\xff\xfe not utf-8\n'      # undecodable bytes survive
    assert s.unknown({'AA': 1}) == [('AA', ' (did you mean A?)')]
    (tmp_path / 'c.vh').write_bytes(b'`define A 2\n')
    with pytest.raises(VhError, match='both'):
        VhSet([a, tmp_path / 'c.vh'])
