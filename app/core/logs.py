"""Three human-readable logs, written as plain sentences (no HTTP request lines):

  logs/activity.log  everything the engine does (including the milestones)
  logs/main.log      only the important milestones: start, stream up/down, history filled, hourly summary
  logs/error.log     only failures, with the reason and the stack trace
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

_activity = logging.getLogger("engine.activity")
_main = logging.getLogger("engine.main")
_error = logging.getLogger("engine.error")
_throttle: dict[str, float] = {}
_done = False


_IST = timezone(timedelta(hours=5, minutes=30), "IST")


class SentenceFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        dt = datetime.fromtimestamp(record.created, tz=_IST)
        stamp = dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{int(record.msecs):03d}"
        line = f"{stamp} IST | {record.levelname:<7} | {record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(log_dir: str, level: str = "INFO") -> None:
    global _done
    if _done:
        return
    _done = True
    fmt = SentenceFormatter()
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(level)

    def file_handler(name: str, lvl: int):
        try:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            h = RotatingFileHandler(str(Path(log_dir) / name), maxBytes=20 * 1024 * 1024, backupCount=10, encoding="utf-8")
            h.setLevel(lvl)
            h.setFormatter(fmt)
            return h
        except Exception as exc:  # never crash because a log file cannot be opened
            print(f"WARNING: cannot write {name} in {log_dir}: {exc}", file=sys.stderr)
            return None

    lvl = getattr(logging, level.upper(), logging.INFO)
    activity_h, main_h, error_h = file_handler("activity.log", lvl), file_handler("main.log", logging.INFO), file_handler("error.log", logging.ERROR)

    def wire(logger: logging.Logger, handlers, level_=logging.INFO):
        logger.handlers.clear()
        logger.setLevel(level_)
        logger.propagate = False
        for h in handlers:
            if h is not None:
                logger.addHandler(h)

    wire(_activity, [activity_h, console], lvl)
    wire(_main, [main_h, activity_h, console])
    wire(_error, [error_h, console], logging.ERROR)

    root = logging.getLogger()  # third-party libraries: warnings and errors only; errors also go to error.log
    root.handlers.clear()
    root.setLevel(logging.WARNING)
    if error_h is not None:
        root.addHandler(error_h)
    wc = logging.StreamHandler(sys.stdout)
    wc.setFormatter(fmt)
    wc.setLevel(logging.WARNING)
    root.addHandler(wc)


def activity(msg: str) -> None:
    _activity.info(msg)


def warn(msg: str) -> None:
    _activity.warning(msg)


def milestone(msg: str) -> None:
    _main.info(msg)


def error(msg: str, exc: BaseException | None = None) -> None:
    if exc is not None:
        _error.error(msg, exc_info=exc)
    else:
        _error.error(msg)


def error_throttled(key: str, msg: str, exc: BaseException | None = None, interval: float = 60.0) -> None:
    now = time.monotonic()
    if now - _throttle.get(key, -1e9) >= interval:
        _throttle[key] = now
        error(msg, exc)
