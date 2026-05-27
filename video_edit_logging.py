#!/usr/bin/env python3
from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Iterator


LOG_FILE_NAME = "video_edit_api.log"
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5

_request_id: ContextVar[str] = ContextVar("video_edit_request_id", default="-")
_job_id: ContextVar[str] = ContextVar("video_edit_job_id", default="-")
_MANAGED_HANDLER = "_video_edit_managed_handler"


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = _request_id.get()
        if not hasattr(record, "job_id"):
            record.job_id = _job_id.get()
        return True


def configure_logging(
    repo_root: Path | str,
    *,
    level: int = logging.INFO,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> Path:
    logs_dir = Path(repo_root).resolve() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / LOG_FILE_NAME

    logger = logging.getLogger("video_edit")
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        if getattr(handler, _MANAGED_HANDLER, False):
            logger.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s pid=%(process)d thread=%(threadName)s "
        "component=%(name)s request_id=%(request_id)s job_id=%(job_id)s %(message)s"
    )

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    _setup_handler(file_handler, formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    _setup_handler(stream_handler, formatter)
    logger.addHandler(stream_handler)

    logger.info("video edit logging configured", extra={"job_id": "-", "request_id": "-"})
    return log_path


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"video_edit.{name}")


@contextmanager
def log_context(*, request_id: str | None = None, job_id: str | None = None) -> Iterator[None]:
    request_token = _request_id.set(request_id) if request_id is not None else None
    job_token = _job_id.set(job_id) if job_id is not None else None
    try:
        yield
    finally:
        if job_token is not None:
            _job_id.reset(job_token)
        if request_token is not None:
            _request_id.reset(request_token)


def _setup_handler(handler: logging.Handler, formatter: logging.Formatter) -> None:
    setattr(handler, _MANAGED_HANDLER, True)
    handler.setFormatter(formatter)
    handler.addFilter(_ContextFilter())
