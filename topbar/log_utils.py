from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

LOGGER_NAME = "topbar"


def resolve_log_path() -> Path:
    override = os.environ.get("PYTPO_TOPBAR_LOG_PATH", "").strip()
    if override:
        return Path(os.path.expanduser(override))

    state_home = os.environ.get("XDG_STATE_HOME", "").strip()
    if state_home:
        base_dir = Path(os.path.expanduser(state_home))
    else:
        base_dir = Path.home() / ".local" / "state"
    return base_dir / "pytpo" / "topbar.log"


def configure_logging(*, debug: bool = False) -> tuple[logging.Logger, Path]:
    logger = logging.getLogger(LOGGER_NAME)
    level_name = os.environ.get("PYTPO_TOPBAR_LOG_LEVEL", "DEBUG" if debug else "INFO").upper()
    level = getattr(logging, level_name, logging.DEBUG if debug else logging.INFO)
    log_path = resolve_log_path()

    if not getattr(logger, "_pytpo_topbar_configured", False):
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(logging.Formatter("[%(name)s] %(levelname)s %(message)s"))
        logger.addHandler(stream_handler)

        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_path,
                maxBytes=1_000_000,
                backupCount=5,
                encoding="utf-8",
            )
        except Exception as exc:
            stream_handler.handle(
                logging.makeLogRecord(
                    {
                        "name": LOGGER_NAME,
                        "levelno": logging.ERROR,
                        "levelname": "ERROR",
                        "msg": "Failed to initialize log file %s: %r",
                        "args": (str(log_path), exc),
                    }
                )
            )
        else:
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s [%(process)d] %(levelname)s %(name)s: %(message)s")
            )
            logger.addHandler(file_handler)

        logger._pytpo_topbar_configured = True

    logger.setLevel(level)
    logger.propagate = False
    return logger, log_path
