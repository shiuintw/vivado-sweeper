from pathlib import Path

from vivado_sweeper import reports
from vivado_sweeper.verify import check_build, normalize

FIX = Path(__file__).resolve().parent / 'fixtures'


def fixture(name, **values):
    text = (FIX / name).read_text()
    for k, val in values.items():
        text = text.replace('@%s@' % k, str(val))
    return text


def test_timing_summary_reads_the_design_row_not_the_clock_table():
    t = reports.parse_timing_summary(fixture('timing_summary.rpt', WNS='-0.250', TNS='-3.100', FAIL=7,
                                             MET='Timing constraints are not met.'))
    assert t == {'wns': '-0.250', 'tns': '-3.100', 'tns_failing': '7', 'whs': '0.112', 'ths': '0.000',
                 'ths_failing': '0', 'wpws': '4.500', 'timing_met': 'no'}


def test_utilization():
    u = reports.parse_utilization(fixture('utilization.rpt', LUT=1234, LUTPCT='1.95', FF=60))
    assert u['lut'] == '1234' and u['lut_pct'] == '1.95' and u['ff'] == '60'
    assert u['bram'] == '1.5' and u['dsp'] == '2' and u['lutram'] == '0'


def test_utilization_ultrascale_names():
    text = ('+------+\n| Site Type | Used | Fixed | Prohibited | Available | Util% |\n'
            '| CLB LUTs* | 900 | 0 | 0 | 1000 | 90.00 |\n| CLB Registers | 50 | 0 | 0 | 2000 | 2.50 |\n')
    assert reports.parse_utilization(text) == {'lut': '900', 'lut_pct': '90.00', 'ff': '50', 'ff_pct': '2.50'}


def test_hierarchy_by_leaf_or_path():
    text = fixture('utilization_hier.rpt', LUT=100, FF=60, FIFOLUT=80)
    h = reports.parse_utilization_hier(text, ['u_ram', 'top/u_alu', 'missing'])
    assert h['u_ram.lut'] == '3' and h['u_ram.ramb36'] == '1'
    assert h['top/u_alu.dsp'] == '2' and h['top/u_alu.ff'] == '10'
    assert not any(k.startswith('missing') for k in h)
    assert reports.parse_utilization_hier(text, ['top/u_ram']) == {}           # u_ram is under u_fifo


def test_power_in_watts():
    p = reports.parse_power(fixture('power.rpt', PTOT='0.512', PDYN='0.415'), ['u_fifo', 'u_fifo/u_ram'])
    assert p['power_total_w'] == '0.512' and p['power_dynamic_w'] == '0.415'   # not On-Chip's "Dynamic"
    assert p['power_static_w'] == '0.097' and p['power_confidence'] == 'Low'
    assert p['u_fifo.power_w'] == '0.00125' and p['u_fifo/u_ram.power_w'] == '0.0005'
    mw = '1. Summary\n----------\n| Total On-Chip Power (mW) | 512.250 |\n| Dynamic (mW) | 400.5 |\n'
    assert reports.parse_power(mw) == {'power_total_w': '0.51225', 'power_dynamic_w': '0.4005'}


VDS = '''# Start of session at: Mon Jan 05 09:00:00 2026
INFO: [Synth 8-6157] synthesizing module 'fifo__parameterized0' [f.v:1]
\tParameter DEPTH bound to: 32 - type: integer
\tParameter MASK bound to: 16'b0000000011111111
\tParameter RATIO bound to: 2.500000 - type: double
\tParameter NAME bound to: "abc" - type: string
INFO: [Synth 8-6155] done synthesizing module 'fifo__parameterized0' (0#1) [f.v:1]
INFO: [Synth 8-6157] synthesizing module 'cache' [c.v:1]
'''


def test_vds_and_verify():
    v = reports.parse_vds(VDS)
    assert v['session'] == 'Mon Jan 05 09:00:00 2026'
    assert v['blocks'][0] == ('fifo', {'DEPTH': '32', 'MASK': "16'b0000000011111111",
                                       'RATIO': '2.500000', 'NAME': '"abc"'})
    assert normalize("16'h00ff") == normalize("16'b0000_0000_1111_1111") == 255
    vmap = {'DEPTH': 'fifo.DEPTH', 'MASK': 'fifo.MASK', 'RATIO': 'fifo.RATIO', 'NAME': 'fifo.NAME',
            'USE_CACHE': 'cache', 'USE_DMA': 'dma'}
    state = {'DEPTH': '32', 'MASK': "16'h00ff", 'RATIO': '2.5', 'NAME': '"abc"', 'USE_CACHE': True, 'USE_DMA': False}
    assert check_build(VDS, state, vmap) == []
    bad = dict(state, DEPTH='16', USE_CACHE=False, USE_DMA=True)
    assert check_build(VDS, bad, vmap) == ['DEPTH = 16 but fifo.DEPTH was bound to 32',
                                           'USE_CACHE is off but module cache was synthesized',
                                           'USE_DMA is on but module dma was not synthesized']
