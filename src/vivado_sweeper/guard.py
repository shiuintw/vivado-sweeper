"""Putting the .vh files back, even after a crash.

Before a sweep touches any file it copies the originals into <out>/originals/ and writes
<root>/.vsweep/restore.json naming them. That file is also the project's lock: while it exists
no other sweep starts. A normal exit, an exception, Ctrl-C and SIGTERM all restore the files
byte for byte and remove it; after a kill -9 or a power cut, `vsweep restore` does the same
from what it names.
"""
import datetime
import json
import os
import socket
from pathlib import Path


class GuardError(Exception):
    pass


def lock_path(cfg):
    return cfg.root / '.vsweep' / 'restore.json'


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def read_lock(cfg):
    path = lock_path(cfg)
    if not path.exists():
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise GuardError('%s is unreadable (%s); inspect it, then delete it' % (path, e))


def lock_holder_alive(lock):
    return lock.get('host') == socket.gethostname() and _alive(int(lock.get('pid', 0)))


class Guard:
    def __init__(self, cfg, out_dir):
        self.cfg, self.out_dir = cfg, Path(out_dir)
        self.files = {}                               # path -> original bytes
        self.held = False

    def acquire(self, paths):
        """Back up paths (the files this sweep may edit) and take the project lock."""
        lock = read_lock(self.cfg)
        if lock is not None:
            if lock_holder_alive(lock):
                raise GuardError('another sweep (pid %s, output %s) is running on this project'
                                 % (lock.get('pid'), lock.get('out_dir')))
            raise GuardError('a previous sweep (pid %s, output %s) did not finish cleanly, so its .vh edits '
                             'may still be in place. Run `vsweep restore` first.'
                             % (lock.get('pid'), lock.get('out_dir')))
        backup_dir = self.out_dir / 'originals'
        backup_dir.mkdir(parents=True, exist_ok=True)
        entries = []
        for i, p in enumerate(paths):
            p = Path(p)
            data = p.read_bytes()
            self.files[str(p)] = data
            b = backup_dir / ('%02d_%s' % (i, p.name))
            b.write_bytes(data)
            entries.append({'path': str(p), 'backup': str(b)})
        path = lock_path(self.cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'pid': os.getpid(), 'host': socket.gethostname(),
                       'started': datetime.datetime.now().isoformat(timespec='seconds'),
                       'out_dir': str(self.out_dir), 'files': entries}, f, indent=1)
        os.replace(tmp, path)
        self.held = True

    def restore(self):
        """Put every backed-up file back if it changed; return the paths restored."""
        restored = []
        for path, data in self.files.items():
            try:
                current = Path(path).read_bytes()
            except OSError:
                current = None
            if current != data:
                Path(path).write_bytes(data)
                restored.append(path)
        return restored

    def release(self):
        if self.held:
            try:
                lock_path(self.cfg).unlink()
            except FileNotFoundError:
                pass
            self.held = False


def restore_from_lock(cfg, force=False):
    """`vsweep restore`: [(path, what happened)] from restore.json, then remove it."""
    lock = read_lock(cfg)
    if lock is None:
        return None
    if lock_holder_alive(lock) and not force:
        raise GuardError('the sweep that holds the lock (pid %s) is still running; stop it, or pass --force'
                         % lock.get('pid'))
    done = []
    for e in lock.get('files', []):
        path, backup = Path(e['path']), Path(e['backup'])
        if not backup.exists():
            done.append((str(path), 'BACKUP MISSING (%s): left as it is' % backup))
            continue
        data = backup.read_bytes()
        if path.exists() and path.read_bytes() == data:
            done.append((str(path), 'already original'))
        else:
            path.write_bytes(data)
            done.append((str(path), 'restored'))
    if not any(what.startswith('BACKUP MISSING') for _, what in done):
        lock_path(cfg).unlink()
    return done
