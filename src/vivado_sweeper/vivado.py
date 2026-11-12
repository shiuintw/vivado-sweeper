"""Starting Vivado in batch mode, from Linux or from WSL.

Two cases, chosen by the executable's path:

  Linux Vivado (.../bin/vivado)      run directly, in its own process group.
  Windows Vivado from WSL            /mnt/c/.../vivado.bat, or any .bat/.exe. WSL cannot run a
                                     .bat itself, so it goes through cmd.exe /c; every path handed
                                     to it is converted (/mnt/c/x -> C:/x, /home/x ->
                                     //wsl.localhost/<distro>/home/x), and its working directory
                                     must be on a Windows drive -- cmd.exe cannot start in a UNC
                                     directory and silently falls back to C:\\Windows.

Every Tcl script vsweep runs prints "VSWEEP_PID [pid]" first, so an interrupted build can be
killed by process id (taskkill on Windows), never by name: the user's own Vivado GUI is left alone.
"""
import os
import re
import shutil
import signal
import subprocess
import threading
import time

WIN_DRIVE_RE = re.compile(r'^/mnt/([a-zA-Z])(?=/|$)')
MARKER_RE = re.compile(r'^VSWEEP_(\w+)\s?(.*)$')


class VivadoError(Exception):
    pass


def is_windows_vivado(path):
    return str(path).lower().endswith(('.bat', '.exe', '.cmd')) or bool(WIN_DRIVE_RE.match(str(path)))


def from_windows(path):
    """C:\\x\\y or C:/x/y -> /mnt/c/x/y; anything else unchanged."""
    m = re.match(r'^([A-Za-z]):[\\/](.*)$', str(path))
    return '/mnt/%s/%s' % (m.group(1).lower(), m.group(2).replace('\\', '/')) if m else str(path)


def on_windows_drive(path):
    return bool(WIN_DRIVE_RE.match(os.path.abspath(str(path))))


def to_windows(path):
    """A WSL path as Windows sees it, with forward slashes (what Tcl wants)."""
    path = os.path.abspath(str(path))
    m = WIN_DRIVE_RE.match(path)
    if m:
        return '%s:%s' % (m.group(1).upper(), path[m.end():] or '/')
    existing, rest = path, ''
    while existing != '/' and not os.path.exists(existing):        # wslpath wants a path that exists
        existing, tail = os.path.split(existing)
        rest = '/' + tail + rest
    win = None
    if shutil.which('wslpath'):
        r = subprocess.run(['wslpath', '-w', existing], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            win = r.stdout.strip()
    if win is None:
        distro = os.environ.get('WSL_DISTRO_NAME')
        if not distro:
            raise VivadoError('cannot express %s as a Windows path (no wslpath, no WSL_DISTRO_NAME)' % path)
        win = '\\\\wsl.localhost\\%s%s' % (distro, existing.replace('/', '\\'))
    return (win.rstrip('\\') + rest.replace('/', '\\')).replace('\\', '/')


class Procs:
    """Every Vivado process this sweep has running, so an interrupt can stop them all."""

    def __init__(self):
        self.lock = threading.Lock()
        self.active = {}                              # Popen -> [windows, windows pid or None]

    def add(self, proc, windows):
        with self.lock:
            self.active[proc] = [windows, None]

    def set_win_pid(self, proc, pid):
        with self.lock:
            if proc in self.active:
                self.active[proc][1] = pid

    def remove(self, proc):
        with self.lock:
            self.active.pop(proc, None)

    def kill(self, proc):
        with self.lock:
            windows, win_pid = self.active.get(proc, [False, None])
        if windows and win_pid:
            subprocess.run(['taskkill.exe', '/T', '/F', '/PID', str(win_pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def kill_all(self):
        with self.lock:
            procs = list(self.active)
        for p in procs:
            self.kill(p)


class Result:
    def __init__(self):
        self.rc = None
        self.markers = []                             # [(KIND, text)] from VSWEEP_* lines, in order
        self.timed_out = False
        self.seconds = 0.0

    def has(self, kind):
        return any(k == kind for k, _ in self.markers)

    def texts(self, kind):
        return [t for k, t in self.markers if k == kind]

    @property
    def ok(self):
        return self.rc == 0 and self.has('DONE') and not self.timed_out


class Vivado:
    def __init__(self, exe, cwd=None, procs=None):
        self.exe = str(exe)
        self.windows = is_windows_vivado(self.exe)
        self.cwd = str(cwd) if cwd else None
        self.procs = procs or Procs()

    def path(self, p):
        """p as Vivado will open it."""
        return to_windows(p) if self.windows else os.path.abspath(str(p))

    def check_cwd(self, cwd):
        if self.windows and not on_windows_drive(cwd):
            raise VivadoError(
                'Windows Vivado must start in a directory on a Windows drive (/mnt/<drive>/...), '
                'not %s: cmd.exe cannot start in a \\\\wsl.localhost directory. Put the project on a '
                'Windows drive, or set [vivado] cwd in vsweep.toml.' % cwd)

    def command(self, tcl, log, tclargs=()):
        args = ['-mode', 'batch', '-notrace', '-nojournal', '-log', self.path(log), '-source', self.path(tcl)]
        if tclargs:
            args += ['-tclargs'] + [str(a) for a in tclargs]
        if self.windows:
            return ['cmd.exe', '/c', to_windows(self.exe).replace('/', '\\')] + args
        return [self.exe] + args

    def run(self, tcl, console_log, vivado_log, cwd=None, timeout=None, on_marker=None, tclargs=()):
        """Run one Tcl script to completion. stdout goes to console_log; VSWEEP_* lines are
        collected (and passed to on_marker(kind, text) as they arrive)."""
        cwd = str(cwd or self.cwd or os.path.dirname(os.path.abspath(str(tcl))))
        self.check_cwd(cwd)
        cmd = self.command(tcl, vivado_log, tclargs)
        res = Result()
        t0 = time.time()
        with open(console_log, 'wb') as log:
            log.write(('# ' + ' '.join(cmd) + '\n# cwd: ' + cwd + '\n').encode())
            try:
                proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True)
            except OSError as e:
                raise VivadoError('cannot start %s: %s' % (cmd[0], e))
            self.procs.add(proc, self.windows)
            timer = None
            if timeout:
                def expire():
                    res.timed_out = True
                    self.procs.kill(proc)
                timer = threading.Timer(timeout, expire)
                timer.daemon = True
                timer.start()
            try:
                for raw in proc.stdout:
                    log.write(raw)
                    log.flush()
                    m = MARKER_RE.match(raw.decode('utf-8', 'replace').rstrip('\r\n'))
                    if not m:
                        continue
                    kind, text = m.group(1), m.group(2).strip()
                    res.markers.append((kind, text))
                    if kind == 'PID' and text.isdigit():
                        self.procs.set_win_pid(proc, int(text))
                    if on_marker:
                        on_marker(kind, text)
                res.rc = proc.wait()
            finally:
                if timer:
                    timer.cancel()
                if proc.poll() is None:
                    self.procs.kill(proc)
                self.procs.remove(proc)
        res.seconds = time.time() - t0
        return res
