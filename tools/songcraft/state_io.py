#!/usr/bin/env python3
"""state_io.py — tiny cross-process guard for the builder's shared JSON state
(backlog f8a1fe93: build_state/backlog writes were truncate-then-write with no
lock; a Denmoku job worker and a CLI/tmux walk writing at once could tear a
file and strand a build).

Two calls, stdlib only:

    locked_read(path)            -> str   (file text, under a SHARED flock)
    locked_write(path, text_or_obj)       (EXCLUSIVE flock + atomic replace)

Writes go to a same-directory temp file (fsync'd), then os.replace — a reader
can never see a half-written file even without taking the shared lock. The
flock itself lives on a sidecar lock file under builder/cache/locks/ (NOT next
to the target: builds/ is git-tracked and os.replace swaps the target's inode,
which would strand a lock taken on the file itself). Lock files are keyed by
the resolved target path, so every process contends on the same one.

locked_write accepts either the exact text to write (callers keep their own
json.dumps formatting — byte-stable diffs) or a JSON-serializable object
(dumped indent=2, ensure_ascii=False, trailing newline).
"""
import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent            # tools/songcraft
LOCKS = HERE / 'builder' / 'cache' / 'locks'      # builder/cache is gitignored


def _lock_file(path):
    p = Path(path)
    try:
        rp = str(p.resolve())
    except OSError:
        rp = str(p.absolute())
    return LOCKS / (hashlib.sha1(rp.encode('utf-8')).hexdigest()[:16]
                    + '-' + p.name + '.lock')


@contextmanager
def _flock(path, mode):
    LOCKS.mkdir(parents=True, exist_ok=True)
    lf = open(_lock_file(path), 'a')
    try:
        fcntl.flock(lf.fileno(), mode)
        yield
    finally:
        fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
        lf.close()


def locked_read(path):
    """Text of <path>, read under a shared lock (waits out an in-flight
    writer). Raises FileNotFoundError like Path.read_text when absent."""
    with _flock(path, fcntl.LOCK_SH):
        return Path(path).read_text()


def locked_write(path, text_or_obj):
    """Write <path> atomically under an exclusive lock: same-dir temp file,
    flush + fsync, os.replace. A crash mid-write leaves the old file intact;
    concurrent writers serialize instead of interleaving."""
    p = Path(path)
    text = (text_or_obj if isinstance(text_or_obj, str)
            else json.dumps(text_or_obj, ensure_ascii=False, indent=2) + '\n')
    with _flock(p, fcntl.LOCK_EX):
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + '.',
                                   suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
