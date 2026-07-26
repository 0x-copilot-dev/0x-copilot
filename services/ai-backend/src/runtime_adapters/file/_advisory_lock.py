"""Portable process-wide byte-range locks for file-backed runtime state.

The file backend is a supported desktop deployment target on both POSIX and
Windows.  Keep platform-only imports inside the lock operations: importing the
runtime factory while artifact effects are disabled must never require a POSIX
module on Windows.
"""

from __future__ import annotations

import errno
import os
import time


_WINDOWS_RETRY_SECONDS = 0.05


def acquire_exclusive(fd: int) -> None:
    """Acquire an exclusive lock covering the first byte of ``fd``.

    ``flock`` and ``msvcrt.locking`` both coordinate between independent
    processes.  The Windows API locks a byte range, so ensure that byte exists
    before attempting a non-blocking lock and retry until it becomes available.
    Callers already hold their in-process lock, which avoids needless local
    contention and makes this blocking loop safe.
    """

    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
            os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                time.sleep(_WINDOWS_RETRY_SECONDS)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def try_acquire_exclusive(fd: int) -> bool:
    """Try to acquire the exclusive byte-range lock without waiting.

    Cleanup uses this for a per-tenant pass lock: a successor must skip a
    tenant still being reclaimed by a paused worker instead of blocking the
    entire fair rotation or running destructive work concurrently.
    """

    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
            os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def release_exclusive(fd: int) -> None:
    """Release a lock acquired by :func:`acquire_exclusive`."""

    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


__all__ = ("acquire_exclusive", "release_exclusive", "try_acquire_exclusive")
