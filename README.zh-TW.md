# vivado-sweeper

[English](README.md)

Vivado 專案的參數掃描工具。在 sweep 檔裡寫好要跑哪些組態，`vsweep` 會依序把每一組寫進
你的 `` `define `` 標頭檔，用 batch mode 建置，確認 netlist 確實是那一組，再把 timing、
utilization、power 收進同一份 CSV。跑完之後，標頭檔會還原成**一個 byte 都不差**的原樣。

```
base { STAGES=4 }
baseline: {}
wide:     {WIDTH=32}            # 繼承 baseline
mult:     {USE_MULT=on}         # 繼承 wide：32 bit，加上乘法
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

（`examples/blinky` 跑在 Arty A7-100T 上：從 WSL 呼叫 Windows 版 Vivado 2025.1。）

- **鏈式掃描**：每個 run 都繼承上一個 run 的設定，也可以指定要繼承哪個 run，或回到
  `base`。grid 展開時會排成每一步只改一個 define 的順序。
- **每個 run 只開一次 Vivado session**：建置完直接在同一個 session 裡產出所有報告。
- **結果會被驗證**：synthesis log 必須顯示建置時用的就是你要的值（`[verify]`），否則這個
  run 會被標成 `verify_failed`。
- **平行建置**（`--parallel K`）：每個 worker 在自己的 project 副本裡建置。
- **Hook**：在每次建置前後執行（燒板子、跑 benchmark、讀 UART），並可把自己的數據加成
  結果的欄位。
- **安全**：每個 project 一把 lock；Ctrl-C、SIGTERM 都會還原檔案；被 `kill -9` 之後用
  `vsweep restore` 復原；中斷的 sweep 可以用 `--resume` 接著跑。
- 支援 Linux 上的 Vivado，也支援**從 WSL 呼叫 Windows 的 Vivado**。純 Python ≥ 3.11，沒有任何
  外部相依。

## 安裝

```sh
pip install git+https://github.com/shiuintw/vivado-sweeper.git
vsweep --version
```

如果要改程式：

```sh
git clone https://github.com/shiuintw/vivado-sweeper.git
cd vivado-sweeper && pip install -e .
```

## 快速開始

```sh
cd my_project                       # 放原始碼的目錄，.xpr 在它底下某處
vsweep configure --vivado /tools/Xilinx/Vivado/2024.2/bin/vivado --vh src/config.vh
vsweep defines                      # 列出可以掃的 define
$EDITOR sweep.vsw
vsweep plan -f sweep.vsw            # 每個 run 會改什麼；不碰任何檔案
vsweep run  -f sweep.vsw            # 開始建置；結果在 vsweep_runs/<時間>_sweep/results.csv
```

如果 project root 底下只有一個 `.xpr`，`configure` 會自動找到它；它推不出來的設定會問你
（加 `-y` 就完全不問）。設定會寫進 `vsweep.toml`，之後可以直接手動改。

### Linux

```toml
[vivado]
path = "/tools/Xilinx/Vivado/2024.2/bin/vivado"
jobs = 16
```

### WSL + Windows 版 Vivado

把 `path` 指向 `/mnt/c` 底下的 `vivado.bat`。vsweep 會透過 `cmd.exe /c` 執行它，並轉換所有
交給 Vivado 的路徑（`/mnt/c/x` → `C:/x`，`/home/x` → `//wsl.localhost/<distro>/home/x`）。

```toml
[vivado]
path = "/mnt/c/Xilinx/2025.1/Vivado/bin/vivado.bat"     # 直接寫 C:\Xilinx\... 也可以
jobs = 16
```

請把 **project 放在 Windows 磁碟上**（`/mnt/c/...`）：Windows 版 Vivado 透過
`\\wsl.localhost` 開 project 很慢，而且 `cmd.exe` 無法在 Linux 目錄裡啟動。如果 project
一定要放在 Linux 那一側，就把 `[vivado] cwd` 設成任何一個 Windows 磁碟上的目錄。
中斷 sweep 時，vsweep 會用 process id 終止 Windows 上的 `vivado.exe`（`taskkill /T /PID`），
不會動到你開著的 Vivado GUI。

## Sweep 檔

```
# 註解可以用 # 或 //
base { DEBUG=off, DEPTH=16 }            # 套用到每一個 run，base 本身不會被建置
baseline: {}                             # run 1：只有 base
deep:     {DEPTH=32}                     # run 2：繼承 baseline
wide:     {WIDTH=64}                     # run 3：繼承 deep（DEPTH=32 仍然有效）
alt < baseline: {WIDTH=128}              # 指定繼承某個 run，而不是上一個
fresh < base:   {X=1}                    # 從 base 重新開始
grid: {DEPTH=[16,32], MODE=[on,off]}     # 4 個 run：grid_DEPTH16_MODEon、grid_DEPTH16_MODEoff、...
{A=1} {B=2} {C=3, A=3}                   # 名稱可以省略：run08、run09、run10
```

| 寫法 | `` `define `` 那一行會變成 |
|------|---------------------------|
| `X=on`、`X=true` | `` `define X ``（取消註解；原本有值就保留） |
| `X=off`、`X=false` | `` //`define X ... ``（註解掉） |
| `X=32`、`X=16'hBEEF`、`X="str"`、`X=word` | `` `define X <值> ``，照你寫的原樣寫入（並打開） |

- **繼承**：一個 run 從 parent 的設定出發，再改它 block 裡寫的那些。parent 預設是上一個
  run，可以用 `name < parent:` 指定；`< base` 表示從 `base` 開始。繼承的只有設定，每個
  run 都是獨立的一次建置。
- **Grid**：值寫成 list 就會展開成所有組合。展開順序保證相鄰兩點只差一個 define（snake/Gray
  順序，第一個 key 變得最慢），每一點都繼承 grid 的 parent。grid 之後的 run 繼承 grid
  的最後一點。
- **一次只動一個變數**：如果一個 run 相對於比較對象（有指定 parent 就是 parent，否則是上
  一個 run）改了不只一個 define，`plan` 會發 warning；加 `--strict` 會直接當成錯誤。grid
  的點不受此限。
- **對照標頭檔檢查**：名稱打錯（`DEPHT` → "did you mean DEPTH?"）、對 flag 設值、run 名稱重複，
  都會在建置任何東西之前就停下來，並指出行號。
- 值只能是一個 token：不能有空白，也不能有 `/`（`` `define `` 那一行只能容納這樣的值，vsweep
  也才讀得回來）。每個 define 都必須已經存在於標頭檔裡（開著或註解掉都可以）；vsweep 從不
  新增或刪除任何一行。

## 一個 run 做了什麼

1. 把這個 run 的標頭檔寫進 project（`--parallel` 時寫進副本）
2. 執行 `pre_build` hook（如果有）
3. 一次 Vivado batch session：`open_project` → `reset_run synth_1` → `launch_runs impl_1
   -to_step write_bitstream -jobs N` → `open_run` → `report_timing_summary`、
   `report_utilization`、`report_power`（以及 `extra_tcl`）
4. 檢查 synthesis log：必須是新的 session（代表建置真的有跑），而且 `[verify]` 的每一項都要相符
5. 只有建置結果是 `ok` 時才執行 `post_build` hook
6. 寫出 `done.json`，並根據目前所有 run 重新產生 `results.csv`

`stop_after = "synth"` 只報告合成後的設計（適合快速掃面積），`"impl"` 做到 `route_design`
為止，`"bitstream"`（預設）會產生 bitstream。

### 輸出

```
vsweep_runs/20261112_190501_sweep/
    results.csv  summary.txt  plan.txt  sweep.vsw  vsweep.toml  originals/
    01_baseline/
        settings.json  vh/config.vh                      這次建置用的確切內容
        build.tcl  build.log  vivado.log                 Vivado session
        top.vds  synth_runme.log  impl_runme.log
        timing_summary.rpt  utilization.rpt  power.rpt   top.bit  top.ltx
        done.json
```

`results.csv` 每個 run 一列。欄位依序是：
- `index, name, parent, status`
- sweep 動到的每一個 define 的值
- `wns tns whs ths timing_met`
- `lut ff bram uram dsp io`（各自附一欄 `_pct`）
- `power_total_w power_dynamic_w power_static_w junction_temp_c`
- `[reports] hierarchical` 的每一項各一組 `<instance>.<metric>` 欄位
- 每一個 hook 數據
- `synth_s impl_s seconds`
- 相對原始檔的改動、problems、run 目錄

狀態有：`ok`、`build_failed`、`verify_failed`、`timeout`、`pre_build_failed`、
`post_build_failed`、`interrupted`、`error`。某個 run 失敗不會讓整個 sweep 停下來，除非加
`--stop-on-fail`。

## 驗證建置出來的東西

synthesis log（`.vds`）記錄了每一個被合成的 module，以及每個 parameter 實際綁定的值。不管
檔案上寫了什麼，它才是「真正建置了什麼」的紀錄。你可以告訴 vsweep 要比對哪些項目：

```toml
[verify]
DEPTH     = "fifo.DEPTH"    # fifo 的 DEPTH parameter 必須綁定成 `define DEPTH 的值
USE_CACHE = "cache"         # USE_CACHE 打開時 module cache 必須有被合成，關掉時必須沒有
```

數字會用數值比較（`16'h00ff` = `255` = `255.000000`）。`vsweep check` 可以在 sweep 之外，
對 project 最後一次的建置做同樣的檢查。

## Hook

```toml
[hooks]
pre_build  = ""                           # shell 指令，在 project root 執行……
post_build = "python3 scripts/run_on_board.py"   # ……或一個 .tcl 檔，交給 Vivado source
```

shell hook 會拿到這些環境變數：`VSWEEP_RUN_NAME`、`VSWEEP_RUN_INDEX`、`VSWEEP_RUN_DIR`、
`VSWEEP_WORKSPACE`、`VSWEEP_XPR`、`VSWEEP_SETTINGS_JSON`、`VSWEEP_BIT`、`VSWEEP_LTX`、
`VSWEEP_VIVADO`。Tcl hook 拿到的是同一組資訊，放在 `$::vsweep(run_dir)`、`$::vsweep(bit)`
等變數裡（路徑是 Vivado 看到的樣子）。hook 要新增 `results.csv` 欄位，可以印出
`VSWEEP_METRIC name=value`，或把一個 JSON object 寫到 `$VSWEEP_RUN_DIR/metrics.json`。
exit code 不是 0 會讓這個 run 失敗。

一個燒板子並從 UART 讀分數的 post-build hook：

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

## 平行建置

```sh
vsweep run -f sweep.vsw --parallel 3
```

一個 Vivado project 只有一組 `synth_1`/`impl_1`，所以每個 worker 都會在
`<root>/.vsweep/ws/wN` 拿到一份自己的 project root 副本：
- 不複製：頂層的 run、cache、`.git`、輸出目錄
- 會複製：IP run，所以 IP 不必重新合成

每份副本用 `jobs / K` 個執行緒建置，原本的 project 完全不會被修改。結束後副本會被刪除
（加 `--keep-workspaces` 可以保留）。

前提是 `.xpr` 用相對於自己的路徑引用檔案（`$PPRDIR/...`；從 project 目錄裡加入的檔案，
Vivado 預設就是這樣存的），而且它用到的東西都在 root 裡面。vsweep 會先檢查 `.xpr`，遇到
下面兩種情況會拒絕執行，並指出是哪個檔案的問題：
- 副本最後會用原始的標頭檔建置（標頭檔或它所在的 include 目錄是用絕對路徑引用的）
- 副本會找不到某個原始碼（相對路徑跑出了 root）

## 出問題的時候

- **Ctrl-C / SIGTERM / SIGHUP**：Vivado 會被停掉，標頭檔會被還原，已經跑完的 run 都留在
  `results.csv` 裡。
- **kill -9、斷電**：下一次 `vsweep run` 會拒絕啟動；先執行 `vsweep restore` 把原始檔放回去
  （原始檔也備份在 `<output>/originals/`）。
- **接著跑**：`vsweep run -f sweep.vsw --resume vsweep_runs/<dir>` 會跳過所有已經 `ok`、
  而且輸入相同（用內容 hash 比對）的 run，只建置剩下的。
- **重新讀報告**：改了 `[reports] hierarchical`、`[verify]`，或 parser 修正之後，執行
  `vsweep reparse vsweep_runs/<dir>`。
- **只跑部分 run**：`--only deep --only 'grid_*'`（它們的設定仍然是從檔案裡前面的 run
  繼承來的）。
- sweep 會建置同一個 project，開始前請先在 Vivado GUI 裡把它關掉。

## vsweep.toml

```toml
[vivado]
path = "/tools/Xilinx/Vivado/2024.2/bin/vivado"
jobs = 16                 # launch_runs -jobs；--parallel 時平均分給每個 worker
timeout_hours = 0         # 每次建置的時限；0 = 不限
# cwd = "..."             # 只有 Windows 版 Vivado 需要：它的啟動目錄（必須在 Windows 磁碟上）

[project]
root = "."                # 相對於這個檔案；以下所有路徑都相對於 root
xpr = "proj/blinky.xpr"
vh = ["src/config.vh"]    # 可以有多個檔案；同一個 define 名稱不能在多個檔案裡重複
synth_run = "synth_1"
impl_run = "impl_1"
stop_after = "bitstream"  # synth | impl | bitstream

[output]
dir = "vsweep_runs"
keep = ["bit", "ltx"]     # 要複製進每個 run 目錄的建置產物：bit、ltx、dcp

[reports]
hierarchical = ["u_core", "u_core/u_cache"]   # 要另外列出 util/power 欄位的 instance
extra_tcl = ""            # 你自己的報告，在內建報告之後 source（可以用 $run_dir）

[verify]
# DEPTH = "fifo.DEPTH"

[hooks]
pre_build = ""
post_build = ""
```

## 範例

`examples/blinky` 是一個很小的 Arty A7-100T 設計，它的大小由三個 define 決定：

```sh
cd examples/blinky
vivado -mode batch -source create_project.tcl        # 產生 proj/blinky.xpr
vsweep configure --vivado "$(which vivado)" --vh src/config.vh --stop-after impl -y
vsweep run -f sweep.vsw
```

## 限制

- 只支援 project mode（`.xpr`），目前還不支援 non-project 的 Tcl 流程。
- 只掃 `` `define `` 的值，（目前）還不能掃 Vivado 的 strategy 或 directive。
- `vsweep` 本身在 Linux/WSL 上執行，不支援原生 Windows Python。

## 開發

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/python -m pytest
```

測試不需要 Vivado：`tests/fake_vivado.py` 會代替它，讀取產生出來的 Tcl，並寫出 Vivado 格式的報告。

## 授權

MIT
