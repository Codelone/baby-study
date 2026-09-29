"""Non-streaming MiMo Chat Completions using the existing requests dependency.

Private media uses data URLs: image_url.url for images, input_audio.data for audio.
JSON mode validates syntax/object shape only; callers must validate business fields.
No request, response body, credentials, or underlying transport errors are logged.
"""

import base64
import binascii
import copy
import json
import math
import time
from collections.abc import Sequence
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests
from django.conf import settings

DEFAULT_MODEL = "mimo-v2.6-flash"
DEFAULT_BASE_URL = "https://api.xiaomimimo.com/v1"
MAX_BASE64_LENGTH = 50 * 1024 * 1024
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"}
AUDIO_MIME_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/x-wav",
    "audio/flac",
    "audio/x-flac",
    "audio/mp4",
    "audio/m4a",
    "audio/x-m4a",
    "audio/ogg",
}
TOKEN_FIELDS = {"prompt_tokens", "completion_tokens", "total_tokens"}
TOKEN_DETAIL_FIELDS = {
    "cached_tokens",
    "reasoning_tokens",
    "audio_tokens",
    "image_tokens",
    "video_tokens",
}
ERROR_MESSAGES = {
    "not_configured": "MiMo is not configured. Use manual entry or contact the administrator.",
    "invalid_configuration": "MiMo configuration is invalid. Contact the administrator.",
    "invalid_request": "The model request or media format is invalid.",
    "timeout": "MiMo timed out. Try again later.",
    "connection_error": "MiMo could not be reached. Try again later.",
    "transport_error": "The MiMo request could not be completed.",
    "authentication_failed": "MiMo authentication failed. Contact the administrator.",
    "insufficient_balance": "MiMo balance is insufficient. Use manual entry or contact an admin.",
    "permission_denied": "MiMo access was denied. Contact the administrator.",
    "unsupported_model": "The configured MiMo model or endpoint is unavailable.",
    "content_filtered": "MiMo did not return content. Revise the input or use manual entry.",
    "rate_limited": "MiMo is rate limited. Try again later.",
    "temporarily_unavailable": "MiMo is temporarily unavailable. Try again later.",
    "provider_error": "MiMo rejected the request. Use manual entry or contact the administrator.",
    "invalid_response": "MiMo returned an invalid response. Review the input or use manual entry.",
    "output_truncated": "MiMo output was truncated. Reduce the input or increase the output limit.",
    "invalid_json": "MiMo returned an invalid JSON object. Review the input or use manual entry.",
    "submission_unknown": "MiMo submission status is unknown. Check the task before retrying.",
    "batch_not_configured": "MiMo Batch is not configured. Contact the administrator.",
    "invalid_batch_request": "The MiMo Batch request is invalid.",
    "invalid_batch_file": "The MiMo Batch file is invalid.",
    "invalid_batch_response": "MiMo Batch returned an invalid response.",
    "invalid_jsonl": "The MiMo Batch file is not valid JSONL.",
    "batch_file_too_large": "The MiMo Batch file is too large. Submit a smaller batch.",
    "invalid_identifier": "The MiMo Batch resource identifier is invalid.",
    "network_error": "MiMo could not be reached. Try again later.",
    "authentication_error": "MiMo authentication failed. Contact the administrator.",
}


class ModelAPIError(Exception):
    """Safe for API feedback; raw vendor/requests exceptions are never attached.

    retryable describes a future user/worker retry, not permission to retry forever.
    usage retains only known numeric token counters when a billed result is invalid.
    """

    def __init__(self, code, *, retryable=False, status_code=None, usage=None):
        self.code = code if isinstance(code, str) and code in ERROR_MESSAGES else "provider_error"
        self.message = ERROR_MESSAGES[self.code]
        self.retryable = retryable
        self.status_code = status_code
        self.usage = _safe_usage(usage)
        super().__init__(self.message)


def _safe_usage(usage):
    if not isinstance(usage, dict):
        return None
    safe = {
        key: value
        for key, value in usage.items()
        if key in TOKEN_FIELDS and type(value) is int and value >= 0
    }
    for key in ("prompt_tokens_details", "completion_tokens_details"):
        if isinstance(usage.get(key), dict):
            safe[key] = {
                name: value
                for name, value in usage[key].items()
                if name in TOKEN_DETAIL_FIELDS and type(value) is int and value >= 0
            }
    return safe or None


def _is_https_url(value):
    try:
        parsed = urlparse(value)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
        )
    except ValueError:
        return False


def _media_data(value, mime_type, allowed_types, *, raw_base64=False):
    if not isinstance(mime_type, str) or mime_type not in allowed_types:
        raise ModelAPIError("invalid_request")
    if isinstance(value, bytes):
        if not value or 4 * ((len(value) + 2) // 3) > MAX_BASE64_LENGTH:
            raise ModelAPIError("invalid_request")
        return f"data:{mime_type};base64,{base64.b64encode(value).decode('ascii')}"
    if not isinstance(value, str) or not value:
        raise ModelAPIError("invalid_request")
    if _is_https_url(value):
        return value
    if value.startswith("data:"):
        header, separator, encoded = value.partition(",")
        if not separator or header not in {f"data:{mime};base64" for mime in allowed_types}:
            raise ModelAPIError("invalid_request")
    elif raw_base64:
        header, encoded = f"data:{mime_type};base64", value
    else:
        raise ModelAPIError("invalid_request")
    if not encoded or len(encoded) > MAX_BASE64_LENGTH:
        raise ModelAPIError("invalid_request")
    invalid_encoding = False
    try:
        base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        invalid_encoding = True
    if invalid_encoding:
        raise ModelAPIError("invalid_request")
    return f"{header},{encoded}"


def _reject_nonfinite(value):
    raise ValueError("Non-finite JSON number")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        _reject_nonfinite(value)
    return number


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def validate_chat_response(response, *, json_output=False, status_code=None):
    """Validate a realtime or Batch response without discarding choices or usage.

    Malformed/truncated completions are not automatically retried: they may be billed.
    Business schema checks belong to the caller, including unknown/missing book fields.
    """
    usage = response.get("usage") if isinstance(response, dict) else None

    def fail(code):
        raise ModelAPIError(code, status_code=status_code, usage=usage)

    if not isinstance(response, dict) or "error" in response:
        fail("invalid_response")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        fail("invalid_response")
    if usage is not None:
        if not isinstance(usage, dict):
            fail("invalid_response")
        for field in TOKEN_FIELDS:
            if field in usage and (type(usage[field]) is not int or usage[field] < 0):
                fail("invalid_response")
        for field in ("prompt_tokens_details", "completion_tokens_details"):
            details = usage.get(field)
            if details is not None and not isinstance(details, dict):
                fail("invalid_response")
            if isinstance(details, dict):
                for key in TOKEN_DETAIL_FIELDS:
                    if key in details and (type(details[key]) is not int or details[key] < 0):
                        fail("invalid_response")
    for choice in choices:
        if not isinstance(choice, dict):
            fail("invalid_response")
        finish_reason = choice.get("finish_reason")
        if not isinstance(finish_reason, str):
            fail("invalid_response")
        if finish_reason in {"length", "repetition_truncation"}:
            fail("output_truncated")
        if finish_reason == "content_filter":
            fail("content_filtered")
        message = choice.get("message")
        if finish_reason != "stop" or not isinstance(message, dict):
            fail("invalid_response")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            fail("invalid_response")
        if json_output:
            invalid_json = False
            try:
                parsed = json.loads(
                    content,
                    parse_constant=_reject_nonfinite,
                    parse_float=_finite_float,
                    object_pairs_hook=_unique_object,
                )
            except (ValueError, RecursionError):
                invalid_json = True
            if invalid_json or not isinstance(parsed, dict):
                fail("invalid_json")
    return response


class MimoClient:
    """A reusable protocol client; construction/body building never sends a request."""

    def __init__(
        self,
        *,
        api_key="",
        base_url=DEFAULT_BASE_URL,
        batch_base_url="",
        text_model=DEFAULT_MODEL,
        image_model=DEFAULT_MODEL,
        audio_model=DEFAULT_MODEL,
        timeout_seconds=60,
        connect_timeout_seconds=10,
        max_retries=2,
        retry_backoff_seconds=1,
        retry_max_delay_seconds=30,
        max_completion_tokens=4096,
        session=None,
    ):
        self._api_key = api_key
        self.base_url = base_url.rstrip("/") if isinstance(base_url, str) else ""
        self.batch_base_url = batch_base_url.rstrip("/") if isinstance(batch_base_url, str) else ""
        self.models = {"text": text_model, "image": image_model, "audio": audio_model}
        self.timeout_seconds = timeout_seconds
        self.connect_timeout_seconds = connect_timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.retry_max_delay_seconds = retry_max_delay_seconds
        self.max_completion_tokens = max_completion_tokens
        if (
            not _is_https_url(self.base_url)
            or urlparse(self.base_url).query
            or (
                self.batch_base_url
                and (not _is_https_url(self.batch_base_url) or urlparse(self.batch_base_url).query)
            )
            or not isinstance(api_key, str)
            or any(
                not isinstance(model, str) or not model.strip() for model in self.models.values()
            )
            or type(max_retries) is not int
            or not 0 <= max_retries <= 5
            or type(max_completion_tokens) is not int
            or not 1 <= max_completion_tokens <= 131072
        ):
            raise ModelAPIError("invalid_configuration")
        for value, upper_bound in (
            (timeout_seconds, 300),
            (connect_timeout_seconds, 60),
            (retry_backoff_seconds, 60),
            (retry_max_delay_seconds, 60),
        ):
            if (
                type(value) not in {int, float}
                or not math.isfinite(value)
                or not 0 < value <= upper_bound
            ):
                raise ModelAPIError("invalid_configuration")
        self.session = session if session is not None else requests.Session()
        # Avoid environment .netrc authentication overriding the explicit MiMo header.
        self.session.trust_env = False

    @classmethod
    def from_settings(cls):
        return cls(
            api_key=getattr(settings, "MIMO_API_KEY", ""),
            base_url=getattr(settings, "MIMO_BASE_URL", DEFAULT_BASE_URL),
            batch_base_url=getattr(settings, "MIMO_BATCH_BASE_URL", ""),
            text_model=getattr(settings, "MIMO_TEXT_MODEL", DEFAULT_MODEL),
            image_model=getattr(settings, "MIMO_IMAGE_MODEL", DEFAULT_MODEL),
            audio_model=getattr(settings, "MIMO_AUDIO_MODEL", DEFAULT_MODEL),
            timeout_seconds=getattr(settings, "MIMO_TIMEOUT_SECONDS", 60),
            connect_timeout_seconds=getattr(settings, "MIMO_CONNECT_TIMEOUT_SECONDS", 10),
            max_retries=getattr(settings, "MIMO_MAX_RETRIES", 2),
            retry_backoff_seconds=getattr(settings, "MIMO_RETRY_BACKOFF_SECONDS", 1),
            retry_max_delay_seconds=getattr(settings, "MIMO_RETRY_MAX_DELAY_SECONDS", 30),
            max_completion_tokens=getattr(settings, "MIMO_MAX_COMPLETION_TOKENS", 4096),
        )

    def build_chat_body(
        self, messages, capability="text", json_output=False, max_completion_tokens=None
    ):
        if not isinstance(capability, str) or capability not in self.models:
            raise ModelAPIError("invalid_request")
        token_limit = (
            self.max_completion_tokens if max_completion_tokens is None else max_completion_tokens
        )
        if type(token_limit) is not int or not 1 <= token_limit <= 131072:
            raise ModelAPIError("invalid_request")
        if type(json_output) is not bool or not isinstance(messages, (list, tuple)) or not messages:
            raise ModelAPIError("invalid_request")
        invalid_messages = False
        try:
            json.dumps(messages, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            invalid_messages = True
        if invalid_messages:
            raise ModelAPIError("invalid_request")
        body = {
            "model": self.models[capability],
            "messages": copy.deepcopy(list(messages)),
            "stream": False,
            "thinking": {"type": "disabled"},
            "max_completion_tokens": token_limit,
        }
        for message in body["messages"]:
            if (
                not isinstance(message, dict)
                or not isinstance(message.get("role"), str)
                or message["role"] not in {"system", "developer", "user", "assistant", "tool"}
            ):
                raise ModelAPIError("invalid_request")
            content = message.get("content")
            if isinstance(content, str):
                continue
            if not isinstance(content, list) or not content:
                raise ModelAPIError("invalid_request")
            for part in content:
                if not isinstance(part, dict):
                    raise ModelAPIError("invalid_request")
                kind = part.get("type")
                if kind == "text" and isinstance(part.get("text"), str):
                    continue
                if kind == "image_url" and capability == "image":
                    media = part.get("image_url")
                    if isinstance(media, dict):
                        _media_data(media.get("url"), "image/jpeg", IMAGE_MIME_TYPES)
                        continue
                if kind == "input_audio" and capability == "audio":
                    media = part.get("input_audio")
                    if isinstance(media, dict):
                        _media_data(media.get("data"), "audio/wav", AUDIO_MIME_TYPES)
                        continue
                raise ModelAPIError("invalid_request")
        if json_output:
            body["response_format"] = {"type": "json_object"}
        return body

    def chat(self, messages, capability="text", json_output=False, max_completion_tokens=None):
        body = self.build_chat_body(messages, capability, json_output, max_completion_tokens)
        if not self._api_key.strip():
            raise ModelAPIError("not_configured")
        for attempt in range(self.max_retries + 1):
            response = None
            error = None
            try:
                response = self.session.post(
                    f"{self.base_url}/chat/completions",
                    json=body,
                    headers={"api-key": self._api_key, "Content-Type": "application/json"},
                    timeout=(self.connect_timeout_seconds, self.timeout_seconds),
                    allow_redirects=False,
                )
            except requests.Timeout:
                error = ModelAPIError("timeout", retryable=True)
            except (requests.exceptions.SSLError, requests.exceptions.InvalidURL):
                error = ModelAPIError("transport_error")
            except requests.ConnectionError:
                error = ModelAPIError("connection_error", retryable=True)
            except requests.RequestException:
                error = ModelAPIError("transport_error")
            if response is not None:
                status = response.status_code
                if status == 200:
                    invalid_payload = False
                    try:
                        payload = response.json()
                    except (ValueError, RecursionError):
                        invalid_payload = True
                    response.close()
                    if invalid_payload:
                        raise ModelAPIError("invalid_response", status_code=status)
                    return validate_chat_response(
                        payload, json_output=json_output, status_code=status
                    )
                error = self._http_error(status)
            delay = self._retry_delay(attempt, response)
            if response is not None:
                response.close()
            if not error.retryable or attempt == self.max_retries or delay is None:
                raise error from None
            time.sleep(delay)

    @staticmethod
    def _http_error(status):
        codes = {
            400: "invalid_request",
            401: "authentication_failed",
            402: "insufficient_balance",
            403: "permission_denied",
            404: "unsupported_model",
            421: "content_filtered",
            429: "rate_limited",
        }
        transient = status in {408, 425, 429, 500, 502, 503, 504}
        code = codes.get(status, "temporarily_unavailable" if transient else "provider_error")
        return ModelAPIError(code, retryable=transient, status_code=status)

    def _retry_delay(self, attempt, response):
        delay = min(self.retry_backoff_seconds * 2**attempt, self.retry_max_delay_seconds)
        retry_after = response.headers.get("Retry-After") if response is not None else None
        if retry_after:
            try:
                requested = float(retry_after)
            except (TypeError, ValueError):
                try:
                    requested = parsedate_to_datetime(retry_after).timestamp() - time.time()
                except (TypeError, ValueError, OverflowError):
                    requested = 0
            if not math.isfinite(requested) or requested > self.retry_max_delay_seconds:
                return None
            delay = max(delay, requested)
        return delay

    @staticmethod
    def _prompt_messages(prompt, system, parts=None):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ModelAPIError("invalid_request")
        messages = []
        if system is not None:
            if not isinstance(system, str) or not system.strip():
                raise ModelAPIError("invalid_request")
            messages.append({"role": "system", "content": system})
        content = prompt if parts is None else [*parts, {"type": "text", "text": prompt}]
        messages.append({"role": "user", "content": content})
        return messages

    def text(self, prompt, *, system=None, json_output=False, max_completion_tokens=None):
        return self.chat(
            self._prompt_messages(prompt, system), "text", json_output, max_completion_tokens
        )

    def image(
        self,
        prompt,
        images,
        *,
        mime_type="image/jpeg",
        system=None,
        json_output=False,
        max_completion_tokens=None,
    ):
        if isinstance(images, (str, bytes)):
            images = [images]
        if not isinstance(images, Sequence) or not images:
            raise ModelAPIError("invalid_request")
        parts = [
            {
                "type": "image_url",
                "image_url": {"url": _media_data(value, mime_type, IMAGE_MIME_TYPES)},
            }
            for value in images
        ]
        return self.chat(
            self._prompt_messages(prompt, system, parts),
            "image",
            json_output,
            max_completion_tokens,
        )

    def audio(
        self,
        prompt,
        audio,
        *,
        mime_type="audio/wav",
        system=None,
        json_output=False,
        max_completion_tokens=None,
    ):
        data = _media_data(audio, mime_type, AUDIO_MIME_TYPES, raw_base64=True)
        parts = [{"type": "input_audio", "input_audio": {"data": data}}]
        return self.chat(
            self._prompt_messages(prompt, system, parts),
            "audio",
            json_output,
            max_completion_tokens,
        )
