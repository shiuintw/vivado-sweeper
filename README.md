# vivado-sweeper

[繁體中文](README.zh-TW.md)

Parameter sweeps for Vivado projects. You describe the configurations in a small sweep file;
`vsweep` writes each one into your `` `define `` header, builds the project in batch mode,
checks that the netlist really is that configuration, and collects timing, utilization and
power into one CSV. When it finishes, your header file is put back **byte for byte**.

```
base { STAGES=4 }
baseline: {}
wide:     {WIDTH=32}            # inherits baseline
mult:     {USE_MULT=on}         # inherits wide: 32 bits and multiply
grid < baseline: {WIDTH=[8,24], STAGES=[2,8]}
```

```
$ vsweep run -f sweep.vsw --only baseline --only wide --only mult
sweep.vsw: 3 run(s) of 7, stop after impl, vivado /mnt/c/Xilinx/2025.1/Vivado/bin/vivado.bat
[1/7] 01_baseline  (no change)
    open_project  (3s)
    synthesis  (8s)
    implementation (to route_design)  (50s)
    reports  (1m45s)
    ok  WNS 7.414  LUT 52 (0.08%)  FF 116  BRAM 0  DSP 0  P 0.111 W  [1m55s]
[2/7] 02_wide  WIDTH: 16 -> 32
    ...
    ok  WNS 6.697  LUT 100 (0.16%)  FF 228  BRAM 0  DSP 0  P 0.112 W  [2m00s]
[3/7] 03_mult  USE_MULT: off -> on
    ...
    ok  WNS -0.562  LUT 160 (0.25%)  FF 266  BRAM 0  DSP 12  P 0.132 W  [2m15s]
restored .../blinky/src/config.vh (byte-identical)

run          status  WNS     LUT  FF   BRAM  DSP  P(W)   time
01_baseline  ok      7.414   52   116  0     0    0.111  1m55s
02_wide      ok      6.697   100  228  0     0    0.112  2m00s
03_mult      ok      -0.562  160  266  0     12   0.132  2m15s
```

(`examples/blinky` on an Arty A7-100T: Windows Vivado 2025.1, driven from WSL.)

- **Chained sweeps**: each run inherits the run before it, or a run you name, or `base`; grids
  expand in an order where each step changes one define.
- **One Vivado session per run**: build, then all reports from the same session.
- **Checked results**: the synthesis log has to show that the build used the values that
  were asked for (`[verify]`), otherwise the run is marked `verify_failed`.
- **Parallel builds** (`--parallel K`) in private copies of the project.
- **Hooks** before/after each build (program a board, run a benchmark, read a UART) that can
  add their own columns to the results.
- **Safe**: a lock per project, Ctrl-C/SIGTERM restore the files, `vsweep restore` recovers
  after a `kill -9`, `--resume` continues an interrupted sweep.
- Linux Vivado, or **Windows Vivado driven from WSL**. Pure Python ≥ 3.11 and nothing else.

## Install

```sh
pip install git+https://github.com/shiuintw/vivado-sweeper.git
vsweep --version
```

or, to work on it:

```sh
git clone https://github.com/shiuintw/vivado-sweeper.git
cd vivado-sweeper && pip install -e .
```

## Quick start

```sh
cd my_project                       # the directory with your sources and the .xpr somewhere below
vsweep configure --vivado /tools/Xilinx/Vivado/2024.2/bin/vivado --vh src/config.vh
vsweep defines                      # what can be swept
$EDITOR sweep.vsw
vsweep plan -f sweep.vsw            # what each run changes; touches nothing
vsweep run  -f sweep.vsw            # builds; results in vsweep_runs/<time>_sweep/results.csv
```

`configure` finds the `.xpr` if there is exactly one under the project root, and asks for
anything it cannot work out (pass `-y` to never ask). It writes `vsweep.toml`, which you can edit.

### Linux

```toml
[vivado]
path = "/tools/Xilinx/Vivado/2024.2/bin/vivado"
jobs = 16
```

### WSL with Windows Vivado

Point `path` at `vivado.bat` through `/mnt/c`. vsweep runs it with `cmd.exe /c` and converts
every path it hands to Vivado (`/mnt/c/x` → `C:/x`, `/home/x` → `//wsl.localhost/<distro>/home/x`).

```toml
[vivado]
path = "/mnt/c/Xilinx/2025.1/Vivado/bin/vivado.bat"     # C:\Xilinx\... works too
jobs = 16
```

Keep the **project on a Windows drive** (`/mnt/c/...`): Windows Vivado opening a project over
`\\wsl.localhost` is slow, and `cmd.exe` cannot start in a Linux directory. If the project
has to live on the Linux side, set `[vivado] cwd` to any directory on a Windows drive.
Interrupting a sweep kills the Windows `vivado.exe` by process id (`taskkill /T /PID`), and
leaves any Vivado GUI you have open alone.

## The sweep file

```
# comments: # or //
base { DEBUG=off, DEPTH=16 }            # applied to every run; not a run itself
baseline: {}                             # run 1: base only
deep:     {DEPTH=32}                     # run 2: inherits baseline
wide:     {WIDTH=64}                     # run 3: inherits deep (DEPTH=32 still holds)
alt < baseline: {WIDTH=128}              # inherit a named run instead of the previous one
fresh < base:   {X=1}                    # start over from base
grid: {DEPTH=[16,32], MODE=[on,off]}     # 4 runs: grid_DEPTH16_MODEon, grid_DEPTH16_MODEoff, ...
{A=1} {B=2} {C=3, A=3}                   # names are optional: run08, run09, run10
```

| written          | the `` `define `` line becomes                      |
|------------------|-----------------------------------------------------|
| `X=on`, `X=true` | `` `define X `` (uncommented; a value it had is kept) |
| `X=off`, `X=false` | `` //`define X ... `` (commented out)             |
| `X=32`, `X=16'hBEEF`, `X="str"`, `X=word` | `` `define X <value> ``, written exactly as given (and turned on) |

- **Inheritance**: a run starts from the settings of its parent and changes what its block
  says. The parent is the previous run, unless you name it with `name < parent:`; `< base`
  starts from `base`. Only the settings are inherited; every run is an independent build.
- **Grids**: a list value makes a grid of every combination. Points are ordered so that
  consecutive points differ in exactly one define (a snake/Gray order; the first key moves
  slowest), and each one inherits the grid's parent. The run after a grid inherits its last point.
- **One variable per step**: `plan` warns when a run moves more than one define relative to
  what it is compared against (its named parent, otherwise the previous run); `--strict` makes
  that an error. Grid points are exempt.
- **Checked against your header**: an unknown name (`DEPHT` → "did you mean DEPTH?"), a value
  on a flag, or a duplicate run name stops the sweep before anything is built, with the line
  number.
- A value is a single token: no spaces and no `/` (that is what a `` `define `` line can hold
  and vsweep can read back). Every define must already exist in the header, enabled or
  commented out; vsweep never adds or removes lines.

## What a run does

1. writes the run's header files into the project (or its copy, with `--parallel`)
2. `pre_build` hook, if any
3. one Vivado batch session: `open_project` → `reset_run synth_1` → `launch_runs impl_1
   -to_step write_bitstream -jobs N` → `open_run` → `report_timing_summary`,
   `report_utilization`, `report_power` (and `extra_tcl`)
4. checks the synthesis log: a new session (the build really ran) and every `[verify]` entry
5. `post_build` hook, only if the build is `ok`
6. `done.json`, and `results.csv` rewritten from all runs so far

`stop_after = "synth"` reports on the synthesized design (quick area sweeps),
`"impl"` stops after `route_design`, and `"bitstream"` (the default) writes a bitstream.

### Output

```
vsweep_runs/20261112_190501_sweep/
    results.csv  summary.txt  plan.txt  sweep.vsw  vsweep.toml  originals/
    01_baseline/
        settings.json  vh/config.vh                      exactly what was built
        build.tcl  build.log  vivado.log                 the Vivado session
        top.vds  synth_runme.log  impl_runme.log
        timing_summary.rpt  utilization.rpt  power.rpt   top.bit  top.ltx
        done.json
```

`results.csv` has one row per run: `index, name, parent, status`, the value of every define
the sweep touches, then `wns tns whs ths timing_met`, `lut ff bram uram dsp io` (each with a
`_pct`), `power_total_w power_dynamic_w power_static_w junction_temp_c`, one
`<instance>.<metric>` column per `[reports] hierarchical` entry, every hook metric,
`synth_s impl_s seconds`, the changes against the original file, problems, and the run directory.

Statuses: `ok`, `build_failed`, `verify_failed`, `timeout`, `pre_build_failed`,
`post_build_failed`, `interrupted`, `error`. A failed run does not stop the sweep unless you
pass `--stop-on-fail`.

## Verifying what was built

The synthesis log (`.vds`) records every module synthesized and every parameter value it was
bound to; it shows what was actually built, whatever the files say. Tell vsweep what to compare:

```toml
[verify]
DEPTH     = "fifo.DEPTH"    # fifo's DEPTH parameter must be bound to `define DEPTH's value
USE_CACHE = "cache"         # module cache is synthesized exactly when USE_CACHE is on
```

Values compare as numbers when they are numbers (`16'h00ff` = `255` = `255.000000`).
`vsweep check` runs the same check on the project's last build, outside a sweep.

## Hooks

```toml
[hooks]
pre_build  = ""                           # shell command, run in the project root ...
post_build = "python3 scripts/run_on_board.py"   # ... or a .tcl file, sourced by Vivado
```

A shell hook gets `VSWEEP_RUN_NAME`, `VSWEEP_RUN_INDEX`, `VSWEEP_RUN_DIR`, `VSWEEP_WORKSPACE`,
`VSWEEP_XPR`, `VSWEEP_SETTINGS_JSON`, `VSWEEP_BIT`, `VSWEEP_LTX`, `VSWEEP_VIVADO`. A Tcl hook
gets the same in `$::vsweep(run_dir)`, `$::vsweep(bit)`, ... (paths as Vivado sees them).
A hook adds columns to `results.csv` by printing `VSWEEP_METRIC name=value` or by writing a
JSON object to `$VSWEEP_RUN_DIR/metrics.json`. A non-zero exit fails the run.

A post-build hook that programs the board and reads a score off the UART:

```python
#!/usr/bin/env python3
import os, re, subprocess, serial                        # pyserial
run_dir, bit = os.environ['VSWEEP_RUN_DIR'], os.environ['VSWEEP_BIT']
with serial.Serial('/dev/ttyUSB1', 115200, timeout=600) as uart:
    subprocess.run([os.environ['VSWEEP_VIVADO'], '-mode', 'batch', '-source', 'program.tcl',
                    '-tclargs', bit], check=True, cwd=run_dir)
    line = uart.read_until(b'Score:').decode() + uart.readline().decode()
print('VSWEEP_METRIC score=%s' % re.search(r'Score:\s*(\S+)', line).group(1))
```

## Parallel builds

```sh
vsweep run -f sweep.vsw --parallel 3
```

A Vivado project has one `synth_1`/`impl_1`, so every worker gets its own copy of the project
root in `<root>/.vsweep/ws/wN` (without the top-level runs, caches, `.git` and output; IP
runs are copied, so IP is not synthesized again). Each copy builds with `jobs / K` threads.
The project itself is never edited. The copies are deleted at the end (`--keep-workspaces`
keeps them).

This works when the `.xpr` refers to its files by paths relative to itself (`$PPRDIR/...`, which
is what Vivado does for files added from inside the project directory) and everything it
uses is inside root. vsweep checks the `.xpr` first and refuses, saying which file is the
problem, if a copy would end up building with the original header (an absolute path to it or
to its include directory) or would not find a source (a relative path that leaves root).

## When things go wrong

- **Ctrl-C / SIGTERM / SIGHUP**: Vivado is stopped, the header files are restored, and the runs
  so far stay in `results.csv`.
- **kill -9, power cut**: the next `vsweep run` refuses to start; `vsweep restore` puts the
  originals back (they are also in `<output>/originals/`).
- **Continue a sweep**: `vsweep run -f sweep.vsw --resume vsweep_runs/<dir>` skips every run
  that finished `ok` with the same input (compared by content hash) and builds the rest.
- **Re-read reports** after changing `[reports] hierarchical`, `[verify]` or after a parser fix:
  `vsweep reparse vsweep_runs/<dir>`.
- **Only some runs**: `--only deep --only 'grid_*'` (they still inherit their settings from the
  runs before them in the file).
- Close the project in the Vivado GUI before a sweep, which builds the same project.

## vsweep.toml

```toml
[vivado]
path = "/tools/Xilinx/Vivado/2024.2/bin/vivado"
jobs = 16                 # launch_runs -jobs; split between workers with --parallel
timeout_hours = 0         # per build; 0 = none
# cwd = "..."             # Windows Vivado only: where it starts (on a Windows drive)

[project]
root = "."                # relative to this file; everything below is relative to root
xpr = "proj/blinky.xpr"
vh = ["src/config.vh"]    # several files are fine; a define name must be unique across them
synth_run = "synth_1"
impl_run = "impl_1"
stop_after = "bitstream"  # synth | impl | bitstream

[output]
dir = "vsweep_runs"
keep = ["bit", "ltx"]     # build products copied into each run directory: bit, ltx, dcp

[reports]
hierarchical = ["u_core", "u_core/u_cache"]   # instances with util/power columns of their own
extra_tcl = ""            # your own reports, sourced after the built-in ones ($run_dir is set)

[verify]
# DEPTH = "fifo.DEPTH"

[hooks]
pre_build = ""
post_build = ""
```

## Example

`examples/blinky` is a tiny Arty A7-100T design whose size follows three defines:

```sh
cd examples/blinky
vivado -mode batch -source create_project.tcl        # creates proj/blinky.xpr
vsweep configure --vivado "$(which vivado)" --vh src/config.vh --stop-after impl -y
vsweep run -f sweep.vsw
```

## Limitations

- Project mode (`.xpr`) only; non-project Tcl flows are not supported yet.
- Sweeps `` `define `` values only, not Vivado strategies or directives (yet).
- `vsweep` runs on Linux/WSL. Native Windows Python is not supported.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest
```

The tests need no Vivado: `tests/fake_vivado.py` stands in for it, reading the generated Tcl
and writing reports in Vivado's format.

## License

MIT
