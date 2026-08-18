"""Tek örnek kilidi.

İki Jarvis aynı anda çalışırsa ikisi de mikrofonu dinler ve ikisi de
yanıtı seslendirir; kullanıcı her şeyi çift duyar. Bu kilit ikinci örneği
başlamadan durdurur ve hangi sürecin çalıştığını söyler.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


class SingleInstance:
    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self.pid_path = lock_path.with_suffix(".pid")
        self._fd: int | None = None

    def running_pid(self) -> int | None:
        try:
            return int(self.pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR)

        try:
            self._lock(fd)
        except OSError as exc:
            os.close(fd)
            pid = self.running_pid()
            detail = f" (süreç {pid})" if pid else ""
            raise AlreadyRunning(
                f"Jarvis zaten çalışıyor{detail}. İki örnek aynı anda dinlerse "
                f"her şeyi çift duyarsın. Önce diğerini kapat "
                f"(o terminalde Ctrl+C, ya da: Stop-Process -Id {pid or '<PID>'})."
            ) from exc

        self._fd = fd
        self.pid_path.write_text(str(os.getpid()), encoding="utf-8")

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            self._unlock(self._fd)
        except OSError:
            pass
        os.close(self._fd)
        self._fd = None
        try:
            self.pid_path.unlink()
        except OSError:
            pass

    # --- platforma özgü kilitleme ---

    @staticmethod
    def _lock(fd: int) -> None:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(fd: int) -> None:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)

    def __enter__(self) -> "SingleInstance":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
