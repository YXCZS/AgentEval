"""Structured observability helpers.

The self-hosted deployment has no external SaaS error tracker (Sentry, etc.),
so we emit structured, JSON-friendly log records for every unhandled API error
and every failed Celery task. These records are the single, machine-readable
source that an operator can tail or ship to any aggregator (Loki, ELK, a sidecar
scraper). Keeping the shape consistent makes alerting on "HTTP 5xx rate" or
"worker task failures" straightforward regardless of the log backend.
"""

from __future__ import annotations

import logging
import sys

_LOGGER_NAME = "agent_eval.observability"

# A single shared logger. Handlers are configured once in configure() so the
# app and the worker both emit to the same shape without duplicating setup.
_logger: logging.Logger | None = None


def configure(level: int = logging.INFO) -> logging.Logger:
    """Configure and return the shared observability logger (idempotent)."""
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )
        logger.addHandler(handler)

    _logger = logger
    return logger


def get_logger() -> logging.Logger:
    """Return the shared logger, configuring it lazily if necessary."""
    if _logger is None:
        return configure()
    return _logger


def record_api_error(
    method: str,
    path: str,
    status_code: int,
    detail: str,
    *,
    exc: BaseException | None = None,
) -> None:
    """Emit one structured record for an unhandled API error."""
    logger = get_logger()
    if exc is not None:
        logger.error(
            "api_error method=%s path=%s status=%s detail=%s exc=%s:%s",
            method,
            path,
            status_code,
            detail,
            type(exc).__name__,
            exc,
        )
    else:
        logger.error(
            "api_error method=%s path=%s status=%s detail=%s",
            method,
            path,
            status_code,
            detail,
        )


def record_task_failure(task_name: str, task_id: str, exc: BaseException) -> None:
    """Emit one structured record for a failed Celery task."""
    logger = get_logger()
    logger.error(
        "worker_task_failed task=%s task_id=%s exc=%s:%s",
        task_name,
        task_id,
        type(exc).__name__,
        exc,
    )
