"""Exclusive lock file that refuses a second API instance on the same data folder."""

import sys
from pathlib import Path
from typing import BinaryIO

from stove_pilot.common.exceptions import ConfigurationError

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class InstanceLock:
    """Process-wide exclusive lock on a file (released by the OS if the process dies)."""

    def __init__(self, path: Path) -> None:
        """Remember the lock file location.

        :param path: Lock file path; parent directories are created on ``acquire``.
        """
        self._path = path
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        """Take the lock without waiting.

        :raises ConfigurationError: Another process holds the lock, or the file is unusable.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+b")
        try:
            self._lock(handle)
        except OSError:
            handle.close()
            raise ConfigurationError(
                f"Another stove-pilot-api instance already holds {self._path}; stop it first"
            ) from None
        self._handle = handle

    def release(self) -> None:
        """Release the lock if held."""
        if self._handle is None:
            return
        try:
            self._unlock(self._handle)
        finally:
            self._handle.close()
            self._handle = None

    @staticmethod
    def _lock(handle: BinaryIO) -> None:
        """Lock the first byte of the file, non-blocking.

        :param handle: Open lock file.
        :raises OSError: The lock is held elsewhere.
        """
        handle.seek(0)
        if sys.platform == "win32":
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(handle: BinaryIO) -> None:
        """Unlock the file.

        :param handle: Open lock file.
        """
        handle.seek(0)
        if sys.platform == "win32":
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
