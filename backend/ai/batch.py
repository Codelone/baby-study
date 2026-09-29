"""MiMo Batch transport; persistence and custom_id mapping belong to the worker.

Protocol: https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/text-generation/batch-api
All POSTs are sent once. A submission_unknown error must be reconciled before
submitting again; it does not mean that the provider rejected the submission.
"""

import io
import json
import math
import re
import time
from urllib.parse import urlsplit

import requests
from ai.client import ModelAPIError
from django.conf import settings

MAX_BATCH_BYTES = 128 * 1024 * 1024
BATCH_ENDPOINT = "/v1/chat/completions"
RETRYABLE_STATUSES = {408, 429, 500, 502, 503, 504}
SAFE_ERROR_CODES = {
    "inference_failed",
    "rate_limit_exceeded",
    "invalid_request_error",
    "authentication_error",
    "insufficient_quota",
    "batch_expired",
    "request_timeout",
    "server_error",
}


def _error(code, *, retryable=False, status_code=None):
    return ModelAPIError(code, retryable=retryable, status_code=status_code)


def _safe_error(value):
    """Do not propagate provider messages, which may echo credentials or inputs."""
    code = value.get("code") if isinstance(value, dict) else None
    return {
        "code": code if isinstance(code, str) and code in SAFE_ERROR_CODES else "provider_error",
        "message": "批量推理请求失败，请核对输入或稍后重试",
    }


def _safe_batch(payload):
    payload = dict(payload)
    if payload.get("errors") is not None:
        errors = payload["errors"]
        if isinstance(errors, dict) and isinstance(errors.get("data"), list):
            payload["errors"] = {"data": [_safe_error(item) for item in errors["data"]]}
        elif isinstance(errors, list):
            payload["errors"] = [_safe_error(item) for item in errors]
        else:
            payload["errors"] = _safe_error(errors)
    if payload.get("error") is not None:
        payload["error"] = _safe_error(payload["error"])
    return payload


def _reject_constant(value):
    raise ValueError("Non-finite JSON number")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _validate_content_part(part):
    if not isinstance(part, dict):
        raise _error("invalid_batch_request")
    kind = part.get("type")
    if not isinstance(kind, str):
        raise _error("invalid_batch_request")
    if kind == "text" and isinstance(part.get("text"), str):
        return
    if kind in {"image_url", "input_audio"}:
        media = part.get(kind)
        field = "url" if kind == "image_url" else "data"
        if isinstance(media, dict) and isinstance(media.get(field), str) and media[field].strip():
            return
    raise _error("invalid_batch_request")


def _validate_request(item, seen):
    if not isinstance(item, dict):
        raise _error("invalid_batch_request")
    custom_id = item.get("custom_id")
    if not isinstance(custom_id, str) or not custom_id.strip() or custom_id in seen:
        raise _error("invalid_batch_request")
    seen.add(custom_id)
    if item.get("method") != "POST" or item.get("url") != BATCH_ENDPOINT:
        raise _error("invalid_batch_request")
    body = item.get("body")
    if not isinstance(body, dict) or not isinstance(body.get("model"), str):
        raise _error("invalid_batch_request")
    if not body["model"].strip():
        raise _error("invalid_batch_request")
    if body.get("stream") is not None and body["stream"] is not False:
        raise _error("invalid_batch_request")
    for field in ("max_completion_tokens", "max_tokens"):
        if field in body and body[field] is not None:
            if type(body[field]) is not int or not 1 <= body[field] <= 131072:
                raise _error("invalid_batch_request")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise _error("invalid_batch_request")
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("role"), str):
            raise _error("invalid_batch_request")
        if message["role"] not in {
            "developer",
            "system",
            "user",
            "assistant",
            "tool",
        }:
            raise _error("invalid_batch_request")
        content = message.get("content")
        if content is None and message["role"] == "assistant" and message.get("tool_calls"):
            continue
        if not isinstance(content, (str, list)):
            raise _error("invalid_batch_request")
        if isinstance(content, list):
            if not content:
                raise _error("invalid_batch_request")
            for part in content:
                _validate_content_part(part)


def _jsonl_file(batch_requests):
    if not isinstance(batch_requests, list) or not batch_requests:
        raise _error("invalid_batch_request")
    stream = io.BytesIO()
    seen = set()
    for item in batch_requests:
        _validate_request(item, seen)
        try:
            line = (json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise _error("invalid_jsonl") from None
        if stream.tell() + len(line) > MAX_BATCH_BYTES:
            raise _error("batch_file_too_large")
        stream.write(line)
    stream.seek(0)
    return stream


class BatchClient:
    def __init__(
        self,
        api_key,
        base_url,
        *,
        timeout_seconds=60,
        connect_timeout_seconds=10,
        max_retries=2,
        retry_backoff_seconds=1,
        retry_max_delay_seconds=30,
    ):
        try:
            if not isinstance(base_url, str):
                raise ValueError("Invalid URL type")
            parsed = urlsplit(base_url)
            valid_url = (
                parsed.scheme == "https"
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
            )
            valid_limits = type(max_retries) is int and 0 <= max_retries <= 5
            for value, upper_bound in (
                (timeout_seconds, 300),
                (connect_timeout_seconds, 60),
                (retry_backoff_seconds, 60),
                (retry_max_delay_seconds, 60),
            ):
                valid_limits = valid_limits and (
                    type(value) in {int, float}
                    and math.isfinite(value)
                    and 0 < value <= upper_bound
                )
        except (TypeError, ValueError):
            valid_url = valid_limits = False
        if not isinstance(api_key, str) or not api_key.strip():
            raise _error("not_configured")
        if not valid_url or not valid_limits:
            raise _error("invalid_configuration")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.connect_timeout_seconds = connect_timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.retry_max_delay_seconds = retry_max_delay_seconds
        self.session = requests.Session()
        self.session.trust_env = False

    @classmethod
    def from_settings(cls):
        return cls(
            api_key=getattr(settings, "MIMO_API_KEY", ""),
            base_url=getattr(settings, "MIMO_BATCH_BASE_URL", ""),
            timeout_seconds=getattr(settings, "MIMO_TIMEOUT_SECONDS", 60),
            connect_timeout_seconds=getattr(settings, "MIMO_CONNECT_TIMEOUT_SECONDS", 10),
            max_retries=getattr(settings, "MIMO_MAX_RETRIES", 2),
            retry_backoff_seconds=getattr(settings, "MIMO_RETRY_BACKOFF_SECONDS", 1),
            retry_max_delay_seconds=getattr(settings, "MIMO_RETRY_MAX_DELAY_SECONDS", 30),
        )

    def _wait(self, attempt):
        time.sleep(min(self.retry_backoff_seconds * 2**attempt, self.retry_max_delay_seconds))

    def _request(self, method, path, **kwargs):
        attempts = self.max_retries + 1 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = self.session.request(
                    method,
                    self.base_url + path,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=(self.connect_timeout_seconds, self.timeout_seconds),
                    allow_redirects=False,
                    **kwargs,
                )
            except requests.RequestException:
                if method != "GET":
                    raise _error("submission_unknown") from None
                if attempt + 1 < attempts:
                    self._wait(attempt)
                    continue
                raise _error("network_error", retryable=True) from None
            status = response.status_code
            if 200 <= status < 300:
                return response
            if method == "GET" and status in RETRYABLE_STATUSES and attempt + 1 < attempts:
                response.close()
                self._wait(attempt)
                continue
            response.close()
            if method != "GET" and (status == 408 or status >= 500):
                raise _error(
                    "submission_unknown",
                    status_code=status,
                )
            if status == 429:
                raise _error(
                    "rate_limited",
                    retryable=True,
                    status_code=status,
                )
            if status in {401, 403}:
                raise _error(
                    "authentication_error",
                    status_code=status,
                )
            raise _error(
                "provider_error",
                retryable=status in RETRYABLE_STATUSES,
                status_code=status,
            )

    def _object(self, method, path, *, batch=False, **kwargs):
        response = self._request(method, path, **kwargs)
        try:
            payload = response.json()
            valid = isinstance(payload, dict) and self._valid_id(payload.get("id"))
            if batch:
                valid = valid and isinstance(payload.get("status"), str) and payload["status"]
        except (TypeError, ValueError, RecursionError):
            valid = False
            payload = None
        finally:
            response.close()
        if not valid:
            code = "submission_unknown" if method != "GET" else "invalid_response"
            error = _error(code)
            if batch and isinstance(payload, dict) and self._valid_id(payload.get("id")):
                error.provider_batch_id = payload["id"]
            raise error from None
        return _safe_batch(payload)

    @staticmethod
    def _valid_id(value):
        return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value) is not None

    def _identifier(self, value):
        if not self._valid_id(value):
            raise _error("invalid_identifier")
        return value

    def submit(self, requests, name=""):
        """Upload JSONL and submit once; errors carry operation/input_file_id if known."""
        if not isinstance(name, str):
            raise _error("invalid_batch_request")
        try:
            name.encode("utf-8")
        except UnicodeError:
            raise _error("invalid_batch_request") from None
        with _jsonl_file(requests) as stream:
            try:
                upload = self._object(
                    "POST",
                    "/files",
                    data={"purpose": "batch"},
                    files={"file": ("input.jsonl", stream, "application/jsonl")},
                )
            except ModelAPIError as error:
                error.operation = "upload"
                raise
        file_id = upload["id"]
        try:
            result = self._object(
                "POST",
                "/batches",
                batch=True,
                json={
                    "input_file_id": file_id,
                    "endpoint": BATCH_ENDPOINT,
                    "completion_window": "24h",
                    "name": name,
                },
            )
        except ModelAPIError as error:
            error.input_file_id = file_id
            error.operation = "submit"
            raise
        result["input_file_id"] = file_id
        return result

    def retrieve(self, batch_id):
        return self._object("GET", f"/batches/{self._identifier(batch_id)}", batch=True)

    def cancel(self, batch_id):
        return self._object("POST", f"/batches/{self._identifier(batch_id)}/cancel", batch=True)

    def results(self, file_id):
        """Read output or error JSONL, retaining provider order and custom_id."""
        response = self._request("GET", f"/files/{self._identifier(file_id)}/content")
        try:
            content = response.content.decode("utf-8-sig")
            rows, seen = [], set()
            for line in content.splitlines():
                if not line.strip():
                    continue
                row = json.loads(
                    line, parse_constant=_reject_constant, object_pairs_hook=_unique_object
                )
                if not isinstance(row, dict):
                    raise ValueError("Not an object")
                custom_id = row.get("custom_id")
                if not isinstance(custom_id, str) or not custom_id.strip() or custom_id in seen:
                    raise ValueError("Invalid custom_id")
                seen.add(custom_id)
                result, failure = row.get("response"), row.get("error")
                if not isinstance(result, dict) and not isinstance(failure, dict):
                    raise ValueError("Missing response and error")
                if result is not None:
                    if not isinstance(result, dict) or not isinstance(result.get("body"), dict):
                        raise ValueError("Invalid response body")
                    status = result.get("status_code")
                    if type(status) is not int or not 100 <= status <= 599:
                        raise ValueError("Invalid status_code")
                    if result["body"].get("error") is not None:
                        result["body"]["error"] = _safe_error(result["body"]["error"])
                if failure is not None:
                    if not isinstance(failure, dict):
                        raise ValueError("Invalid error")
                    row["error"] = _safe_error(failure)
                rows.append(row)
            return rows
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise _error("invalid_jsonl") from None
        finally:
            response.close()
