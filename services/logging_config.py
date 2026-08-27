"""Structured JSON logging. One line per event on stdout, which Docker captures
and a log shipper (Grafana Alloy) forwards off-box.

Never log secrets or health data here: no Garmin credentials, no JWTs, no full
prompt bodies. Log identifiers, counts, and durations instead. Anything that
could carry personal data goes at DEBUG, which is off by default in production.
"""

import json
import logging
import os
import sys
from contextvars import ContextVar

# Request-scoped fields (user_id, session_id, request_id) merged into every log
# line, so they don't have to be threaded through every function signature.
#
# The var holds a mutable dict that init_log_context installs once per request,
# and set_log_context mutates in place. That indirection matters: FastAPI runs
# the endpoint in a child task with a COPIED context, so rebinding the var
# downstream (ctx.set(...)) would be invisible to the middleware that logs the
# request summary afterwards. Mutating one shared dict is visible to both.
_context: ContextVar[dict] = ContextVar("log_context")


def _ctx() -> dict:
    try:
        return _context.get()
    except LookupError:
        ctx: dict = {}
        _context.set(ctx)
        return ctx


def init_log_context(**kwargs) -> None:
    """Start a fresh scope. Call once per request or background job."""
    _context.set({k: v for k, v in kwargs.items() if v is not None})


def set_log_context(**kwargs) -> None:
    """Add fields to the current scope, visible to parent tasks."""
    _ctx().update({k: v for k, v in kwargs.items() if v is not None})


def clear_log_context() -> None:
    _context.set({})


def get_log_context() -> dict:
    return dict(_ctx())


# Attributes present on a stock LogRecord. Anything else was passed via extra=
# and should surface as a top-level JSON field.
_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(_ctx())
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = None) -> None:
    """Idempotent. Call once at process start (main.py for the server, cli.py for the CLI)."""
    level = level or os.getenv("LOG_LEVEL", "INFO")
    if "--debug" in sys.argv:
        level = "DEBUG"
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # Third-party noise: httpx logs every outbound request, and uvicorn's access
    # log duplicates the request middleware in main.py.
    logging.getLogger("httpx").setLevel("WARNING")
    logging.getLogger("httpcore").setLevel("WARNING")
    logging.getLogger("uvicorn.access").setLevel("WARNING")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
