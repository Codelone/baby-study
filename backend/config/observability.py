"""Small, allowlisted diagnostics; never log request/provider bodies or credentials."""

import json
import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

logger = logging.getLogger("baby_study")
context = ContextVar("diagnostic_context", default={})
SAFE_FIELDS = {
    "request_id",
    "intake_id",
    "photo_id",
    "generation",
    "mode",
    "stage",
    "elapsed_ms",
    "queue_ms",
    "attempt",
    "error_code",
    "status",
    "method",
    "path",
    "count",
    "bytes",
    "batch_status",
    "model",
    "polls",
    "processed",
}


def event(name, **fields):
    fields = {**context.get(), **fields}
    logger.info(name, extra={"diagnostics": {k: v for k, v in fields.items() if k in SAFE_FIELDS}})


@contextmanager
def log_context(**fields):
    token = context.set({**context.get(), **fields})
    try:
        yield
    finally:
        context.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps(
            {
                "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                "level": record.levelname,
                "event": record.getMessage(),
                **getattr(record, "diagnostics", {}),
            },
            ensure_ascii=False,
            default=str,
        )


class RequestTimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = str(uuid.uuid4())
        started = time.perf_counter()
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        event(
            "http_request",
            request_id=request.request_id,
            method=request.method,
            path=request.path,
            status=response.status_code,
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return response
