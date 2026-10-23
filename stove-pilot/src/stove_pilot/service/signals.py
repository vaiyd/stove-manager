"""Graceful-stop signal handling shared by the server entrypoints."""

import signal
from types import FrameType


def _raise_interrupt(signum: int, frame: FrameType | None) -> None:
    """Turn SIGTERM/SIGBREAK into the ``KeyboardInterrupt`` that Waitress shuts down on.

    :param signum: Received signal number.
    :param frame: Interrupted frame (unused).
    :raises KeyboardInterrupt: Always.
    """
    raise KeyboardInterrupt


def install_signal_handlers() -> None:
    """Route SIGTERM (and SIGBREAK on Windows) to a graceful stop; SIGINT already interrupts."""
    for name in ("SIGTERM", "SIGBREAK"):
        number = getattr(signal, name, None)
        if number is not None:
            signal.signal(number, _raise_interrupt)
