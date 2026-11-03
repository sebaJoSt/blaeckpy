"""What blaeckpy prints on the terminal: its log messages, colour-coded by level, and the banner
begin() shows. Colour goes only to a terminal, and never with NO_COLOR set.

The handler sits on the "blaeckpy" logger and steps aside once the script configures logging
itself, so a message is never printed twice.
"""

from __future__ import annotations

import logging
import os
import sys

_LEVEL_COLORS = {
    logging.DEBUG: "\033[36m",       # cyan
    logging.INFO: "\033[32m",        # green
    logging.WARNING: "\033[33m",     # yellow
    logging.ERROR: "\033[31m",       # red
    logging.CRITICAL: "\033[1;31m",  # bold red
}
BOLD = "\033[1m"
LINK = "\033[94;4m"  # bright blue, underlined
RESET = "\033[0m"


def _enable_windows_ansi() -> None:
    """The classic Windows console shows ANSI codes only once asked to. Windows Terminal and
    Loggbok's script terminal show them anyway."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-12)  # stderr
        mode = wintypes.DWORD()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass


def use_color(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not (hasattr(stream, "isatty") and stream.isatty()):
        return False
    if sys.platform == "win32":
        _enable_windows_ansi()
    return True


class _Formatter(logging.Formatter):
    """The message in its level's colour. A record with ``styled`` (from :func:`styled`) brings
    its own codes, used in place of the plain message."""

    def __init__(self, color: bool) -> None:
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        text = record.getMessage()
        if self.color:
            styled_text = getattr(record, "styled", None)
            text = (styled_text if styled_text is not None
                    else _LEVEL_COLORS.get(record.levelno, "") + text) + RESET
        if record.exc_info:
            text += "\n" + self.formatException(record.exc_info)
        return text


class ConsoleHandler(logging.StreamHandler):
    """Prints on stderr until the root logger has a handler: then the script configured
    logging, and blaeckpy's messages reach it through propagation instead."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)
        self.setFormatter(_Formatter(use_color(sys.stderr)))

    def emit(self, record: logging.LogRecord) -> None:
        if logging.getLogger().handlers:
            return
        # Whatever stderr is now: a script or a test may have swapped it since.
        if self.stream is not sys.stderr:
            self.setStream(sys.stderr)
            self.setFormatter(_Formatter(use_color(sys.stderr)))
        super().emit(record)


def styled(codes: str) -> dict:
    """``extra=`` for a log call whose message has bold text or a link: ``codes`` is the message
    with ANSI codes, shown on a colour terminal; everywhere else gets the plain message."""
    return {"styled": codes}


def install(logger: logging.Logger) -> None:
    """The handler on blaeckpy's logger, unless one is there already."""
    if any(isinstance(h, ConsoleHandler) for h in logger.handlers):
        return
    logger.addHandler(ConsoleHandler())
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)
