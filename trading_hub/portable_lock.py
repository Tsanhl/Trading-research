"""Small cross-platform lock bridge; file close releases the lock."""
try:
    from fcntl import flock, LOCK_EX, LOCK_NB, LOCK_UN
except ImportError:  # Windows
    import msvcrt
    import os
    LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8
    def flock(file, flags):
        fd = file if isinstance(file, int) else file.fileno()
        os.lseek(fd, 0, os.SEEK_SET)
        mode = msvcrt.LK_UNLCK if flags & LOCK_UN else (msvcrt.LK_NBLCK if flags & LOCK_NB else msvcrt.LK_LOCK)
        try:
            msvcrt.locking(fd, mode, 1)
        except OSError as exc:
            raise BlockingIOError("Research cycle already running") from exc
