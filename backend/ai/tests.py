import base64
import copy
import traceback
from email.utils import formatdate
from unittest.mock import Mock, patch

import requests
from django.test import SimpleTestCase, override_settings

from .client import ERROR_MESSAGES, MimoClient, ModelAPIError, validate_chat_response


def completion(content="A response", finish_reason="stop", usage=True):
    result = {
        "id": "test-response",
        "model": "mimo-v2.6-flash",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "reasoning_content": "",
                    "tool_calls": None,
                },
            }
        ],
    }
    if usage:
        result["usage"] = {
            "prompt_tokens": 25,
            "completion_tokens": 10,
            "total_tokens": 35,
            "prompt_tokens_details": {"image_tokens": 10, "audio_tokens": 4, "cached_tokens": 2},
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
    return result


def http_response(status=200, payload=None, headers=None):
    response = Mock(status_code=status, headers=headers or {})
    response.json.return_value = completion() if payload is None else payload
    return response


class MimoClientTests(SimpleTestCase):
    def setUp(self):
        self.session = Mock()
        self.session.post.return_value = http_response()
        self.client = MimoClient(api_key="test-only-placeholder", session=self.session)
        self.messages = [{"role": "user", "content": "Return visible book information"}]

    def assert_code(self, code, call, *args, **kwargs):
        with self.assertRaises(ModelAPIError) as captured:
            call(*args, **kwargs)
        self.assertEqual(captured.exception.code, code)
        return captured.exception

    @override_settings(
        MIMO_API_KEY="test-only-placeholder",
        MIMO_BASE_URL="https://example.test/realtime/v1/",
        MIMO_BATCH_BASE_URL="https://example.test/batch/v1/",
        MIMO_TEXT_MODEL="text-model",
        MIMO_IMAGE_MODEL="image-model",
        MIMO_AUDIO_MODEL="audio-model",
        MIMO_TIMEOUT_SECONDS=20,
        MIMO_CONNECT_TIMEOUT_SECONDS=4,
        MIMO_MAX_RETRIES=1,
        MIMO_RETRY_BACKOFF_SECONDS=2,
        MIMO_RETRY_MAX_DELAY_SECONDS=8,
        MIMO_MAX_COMPLETION_TOKENS=1024,
    )
    def test_from_settings_routes_each_capability_and_independent_batch_url(self):
        client = MimoClient.from_settings()
        self.assertEqual(client.base_url, "https://example.test/realtime/v1")
        self.assertEqual(client.batch_base_url, "https://example.test/batch/v1")
        self.assertEqual(client.timeout_seconds, 20)
        self.assertEqual(client.connect_timeout_seconds, 4)
        self.assertEqual(client.max_retries, 1)
        self.assertEqual(client.retry_backoff_seconds, 2)
        self.assertEqual(client.retry_max_delay_seconds, 8)
        for capability in ("text", "image", "audio"):
            with self.subTest(capability=capability):
                body = client.build_chat_body(self.messages, capability)
                self.assertEqual(body["model"], f"{capability}-model")
                self.assertEqual(body["max_completion_tokens"], 1024)
        self.assertNotIn("test-only-placeholder", repr(client))

    @override_settings(MIMO_API_KEY="", MIMO_BATCH_BASE_URL="")
    def test_unconfigured_client_can_build_batch_body_but_cannot_call(self):
        client = MimoClient.from_settings()
        client.session = self.session
        self.assertEqual(client.batch_base_url, "")
        self.assertEqual(client.build_chat_body(self.messages)["model"], "mimo-v2.6-flash")
        error = self.assert_code("not_configured", client.chat, self.messages)
        self.assertFalse(error.retryable)
        self.session.post.assert_not_called()

    def test_body_does_not_mutate_messages_or_include_credentials(self):
        messages = [
            {"role": "system", "content": "Return JSON with title and null for unknowns"},
            *self.messages,
        ]
        original = copy.deepcopy(messages)
        body = self.client.build_chat_body(messages, json_output=True, max_completion_tokens=512)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertFalse(body["stream"])
        self.assertEqual(body["max_completion_tokens"], 512)
        body["messages"][0]["content"] = "changed"
        self.assertEqual(messages, original)
        self.assertNotIn("api-key", body)
        self.session.post.assert_not_called()

    def test_text_returns_complete_response_and_safe_transport_options(self):
        expected = completion()
        self.session.post.return_value = http_response(payload=expected)
        self.assertEqual(self.client.text("Explain", system="Be concise"), expected)
        args, kwargs = self.session.post.call_args
        self.assertEqual(args, ("https://api.xiaomimimo.com/v1/chat/completions",))
        self.assertEqual(
            kwargs["headers"],
            {"api-key": "test-only-placeholder", "Content-Type": "application/json"},
        )
        self.assertEqual(kwargs["timeout"], (10, 60))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertFalse(self.session.trust_env)
        self.assertEqual(kwargs["json"]["messages"][0], {"role": "system", "content": "Be concise"})
        self.session.post.return_value.close.assert_called_once()

    def test_image_supports_multiple_private_data_urls_and_bytes(self):
        image_url = "https://example.test/book.png"
        data_url = "data:image/png;base64," + base64.b64encode(b"private-image").decode()
        result = self.client.image(
            "Read visible title",
            [image_url, data_url, b"private-image"],
            mime_type="image/png",
            json_output=False,
        )
        self.assertIn("usage", result)
        parts = self.session.post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertEqual(
            [part["image_url"]["url"] for part in parts[:-1]], [image_url, data_url, data_url]
        )
        self.assertEqual(parts[-1], {"type": "text", "text": "Read visible title"})

    def test_image_accepts_single_image(self):
        self.client.image("Describe", b"private-image")
        parts = self.session.post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertEqual(
            parts[0]["image_url"]["url"], "data:image/jpeg;base64,cHJpdmF0ZS1pbWFnZQ=="
        )

    def test_audio_uses_mimo_data_url_protocol_for_bytes_base64_url_and_data_url(self):
        encoded = base64.b64encode(b"private-audio").decode()
        data_url = f"data:audio/wav;base64,{encoded}"
        for audio, expected in (
            (b"private-audio", data_url),
            (encoded, data_url),
            (data_url, data_url),
            ("https://example.test/audio.wav", "https://example.test/audio.wav"),
        ):
            with self.subTest(input_kind=type(audio).__name__):
                self.client.audio("Describe the audio", audio)
                body = self.session.post.call_args.kwargs["json"]
                self.assertEqual(
                    body["messages"][0]["content"][0],
                    {"type": "input_audio", "input_audio": {"data": expected}},
                )
                self.assertNotIn("format", body["messages"][0]["content"][0]["input_audio"])

    def test_build_body_accepts_private_multimodal_payload_without_network(self):
        for capability, part in (
            ("image", {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}),
            (
                "audio",
                {"type": "input_audio", "input_audio": {"data": "data:audio/wav;base64,YQ=="}},
            ),
        ):
            with self.subTest(capability=capability):
                messages = [{"role": "user", "content": [part, {"type": "text", "text": "Read"}]}]
                self.assertEqual(
                    self.client.build_chat_body(messages, capability)["messages"], messages
                )
        self.session.post.assert_not_called()

    def test_invalid_requests_do_not_send_network_requests(self):
        cases = [
            ([], {}),
            (None, {}),
            (self.messages, {"capability": "video"}),
            (self.messages, {"capability": []}),
            (self.messages, {"json_output": "true"}),
            (self.messages, {"max_completion_tokens": True}),
            (self.messages, {"max_completion_tokens": 0}),
            (self.messages, {"max_completion_tokens": 131073}),
            ([{"role": "invalid", "content": "input"}], {}),
            ([{"role": "user", "content": []}], {}),
            ([{"role": "user", "content": [{"type": "video"}]}], {}),
            ([{"role": "user", "content": [{"type": "text", "text": None}]}], {}),
            ([{"role": "user", "content": "input", "extra": float("nan")}], {}),
            ([{"role": "user", "content": "input", "extra": object()}], {}),
        ]
        for messages, kwargs in cases:
            with self.subTest(messages=messages, kwargs=kwargs):
                self.assert_code("invalid_request", self.client.chat, messages, **kwargs)
        self.session.post.assert_not_called()

    def test_invalid_media_and_prompts_are_rejected_before_sending(self):
        cases = [
            (self.client.image, ("Read", []), {}),
            (self.client.image, ("Read", "file:///private/book.jpg"), {}),
            (self.client.image, ("Read", "http://example.test/book.jpg"), {}),
            (self.client.image, ("Read", "data:image/png;base64,bad!"), {}),
            (self.client.image, ("Read", b"image"), {"mime_type": "application/pdf"}),
            (self.client.audio, ("Read", "data:audio/wav;base64,"), {}),
            (self.client.audio, ("Read", "not base64"), {}),
            (self.client.audio, ("Read", b""), {}),
            (self.client.text, ("",), {}),
            (self.client.text, ("Read",), {"system": 1}),
        ]
        for call, args, kwargs in cases:
            with self.subTest(call=call.__name__):
                self.assert_code("invalid_request", call, *args, **kwargs)
        self.session.post.assert_not_called()

    @patch("ai.client.MAX_BASE64_LENGTH", 4)
    def test_base64_size_limit_applies_to_image_audio_and_raw_bytes(self):
        for call, value in (
            (self.client.image, "data:image/png;base64,YWJjZA=="),
            (self.client.image, b"four"),
            (self.client.audio, "YWJjZA=="),
            (self.client.audio, b"four"),
        ):
            with self.subTest(call=call.__name__):
                self.assert_code("invalid_request", call, "Read", value)
        self.session.post.assert_not_called()

    def test_invalid_configuration_is_safe(self):
        for options in (
            {"base_url": "http://example.test/v1"},
            {"base_url": "https://name:secret@example.test"},
            {"base_url": "https://example.test/v1?key=secret"},
            {"batch_base_url": "invalid"},
            {"text_model": ""},
            {"api_key": None},
            {"timeout_seconds": 0},
            {"timeout_seconds": float("nan")},
            {"max_retries": 6},
            {"max_retries": True},
            {"max_completion_tokens": 0},
            {"retry_max_delay_seconds": 61},
            {"retry_backoff_seconds": -1},
        ):
            with self.subTest(options=options):
                error = self.assert_code("invalid_configuration", MimoClient, **options)
                self.assertNotIn("secret", str(error))

    @patch("ai.client.time.sleep")
    def test_timeout_retry_and_usage_after_success(self, sleep):
        self.session.post.side_effect = [requests.Timeout("private raw body"), http_response()]
        response = self.client.chat(self.messages)
        self.assertEqual(response["usage"]["total_tokens"], 35)
        self.assertEqual(self.session.post.call_count, 2)
        sleep.assert_called_once_with(1)

    @patch("ai.client.time.sleep")
    def test_retries_are_bounded_with_exponential_backoff(self, sleep):
        self.session.post.side_effect = requests.Timeout("test-only-placeholder/private input")
        error = self.assert_code("timeout", self.client.chat, self.messages)
        self.assertTrue(error.retryable)
        self.assertIsNone(error.status_code)
        self.assertEqual(self.session.post.call_count, 3)
        self.assertEqual([item.args[0] for item in sleep.call_args_list], [1, 2])
        self.assertNotIn("test-only-placeholder", str(error))

    @patch("ai.client.time.sleep")
    def test_rate_limit_honors_retry_after(self, sleep):
        self.session.post.side_effect = [
            http_response(429, headers={"Retry-After": "3"}),
            http_response(),
        ]
        self.client.chat(self.messages)
        sleep.assert_called_once_with(3)

    @patch("ai.client.time.time", return_value=1790000000)
    @patch("ai.client.time.sleep")
    def test_retry_after_supports_http_date(self, sleep, clock):
        header = formatdate(1790000005, usegmt=True)
        self.session.post.side_effect = [
            http_response(503, headers={"Retry-After": header}),
            http_response(),
        ]
        self.client.chat(self.messages)
        sleep.assert_called_once_with(5)

    @patch("ai.client.time.sleep")
    def test_large_or_nonfinite_retry_after_leaves_retry_to_worker(self, sleep):
        for delay in ("600", "nan", "inf"):
            with self.subTest(delay=delay):
                self.session.post.reset_mock()
                self.session.post.return_value = http_response(429, headers={"Retry-After": delay})
                error = self.assert_code("rate_limited", self.client.chat, self.messages)
                self.assertTrue(error.retryable)
                self.assertEqual(error.status_code, 429)
                self.session.post.assert_called_once()
        sleep.assert_not_called()

    @patch("ai.client.time.sleep")
    def test_invalid_retry_after_uses_backoff(self, sleep):
        self.session.post.side_effect = [
            http_response(503, headers={"Retry-After": "bad"}),
            http_response(),
        ]
        self.client.chat(self.messages)
        sleep.assert_called_once_with(1)

    @patch("ai.client.time.sleep")
    def test_transient_http_codes_and_connection_failures_can_retry(self, sleep):
        for failure in [http_response(code) for code in (408, 425, 429, 500, 502, 503, 504)] + [
            requests.ConnectionError("private error")
        ]:
            with self.subTest(failure_type=type(failure).__name__):
                self.session.post.reset_mock()
                self.session.post.side_effect = [failure, http_response()]
                self.client.chat(self.messages)
                self.assertEqual(self.session.post.call_count, 2)

    @patch("ai.client.time.sleep")
    def test_zero_retries_and_nonretryable_http_errors(self, sleep):
        self.client.max_retries = 0
        for status, code, retryable in (
            (400, "invalid_request", False),
            (401, "authentication_failed", False),
            (402, "insufficient_balance", False),
            (403, "permission_denied", False),
            (404, "unsupported_model", False),
            (421, "content_filtered", False),
            (302, "provider_error", False),
            (501, "provider_error", False),
            (429, "rate_limited", True),
            (503, "temporarily_unavailable", True),
        ):
            with self.subTest(status=status):
                self.session.post.reset_mock()
                response = http_response(status, {"error": {"message": "private raw error"}})
                self.session.post.return_value = response
                error = self.assert_code(code, self.client.chat, self.messages)
                self.assertEqual(error.status_code, status)
                self.assertEqual(error.retryable, retryable)
                self.session.post.assert_called_once()
                response.json.assert_not_called()
                response.close.assert_called_once()
                self.assertNotIn("private", str(error))
        sleep.assert_not_called()

    @patch("ai.client.time.sleep")
    def test_tls_and_other_transport_failures_are_not_retried_or_exposed(self, sleep):
        for exception in (
            requests.exceptions.SSLError("test-only-placeholder/private body"),
            requests.exceptions.InvalidURL("private URL"),
            requests.RequestException("test-only-placeholder"),
        ):
            with self.subTest(exception_type=type(exception).__name__):
                self.session.post.reset_mock()
                self.session.post.side_effect = exception
                error = self.assert_code("transport_error", self.client.chat, self.messages)
                self.assertFalse(error.retryable)
                self.session.post.assert_called_once()
                self.assertNotIn("test-only-placeholder", str(error))
                self.assertNotIn("private body", "".join(traceback.format_exception(error)))
        sleep.assert_not_called()

    def test_http_json_decode_errors_are_safe_and_not_retried(self):
        self.session.post.return_value.json.side_effect = ValueError(
            "test-only-placeholder/private body"
        )
        error = self.assert_code("invalid_response", self.client.chat, self.messages)
        self.assertEqual(error.status_code, 200)
        self.assertIsNone(error.__context__)
        self.assertNotIn("private body", "".join(traceback.format_exception(error)))
        self.session.post.assert_called_once()

    def test_json_mode_validates_every_choice_and_keeps_original_response(self):
        response = completion('{"title": "书", "isbn": null}')
        response["choices"].append(
            {"index": 1, "finish_reason": "stop", "message": {"content": '{"title": "Other"}'}}
        )
        original = copy.deepcopy(response)
        self.session.post.return_value = http_response(payload=response)
        self.assertIs(self.client.chat(self.messages, json_output=True), response)
        self.assertEqual(response, original)
        response["choices"][1]["message"]["content"] = "invalid"
        self.assert_code("invalid_json", validate_chat_response, response, json_output=True)

    def test_invalid_json_objects_are_rejected_without_new_billed_calls(self):
        for content in (
            '{"title":',
            "[]",
            "null",
            "42",
            "```json\n{}\n```",
            '{"a":NaN}',
            '{"a":Infinity}',
            '{"a":1,"a":2}',
            '{"a":1e999}',
        ):
            with self.subTest(content=content):
                self.session.post.reset_mock()
                self.session.post.return_value = http_response(payload=completion(content))
                error = self.assert_code(
                    "invalid_json", self.client.chat, self.messages, json_output=True
                )
                self.assertFalse(error.retryable)
                self.assertIsNone(error.__context__)
                self.assertEqual(error.usage["total_tokens"], 35)
                self.session.post.assert_called_once()

    def test_truncated_or_filtered_output_is_not_used_or_retried(self):
        for reason, code in (
            ("length", "output_truncated"),
            ("repetition_truncation", "output_truncated"),
            ("content_filter", "content_filtered"),
        ):
            with self.subTest(reason=reason):
                self.session.post.reset_mock()
                self.session.post.return_value = http_response(payload=completion("{}", reason))
                error = self.assert_code(code, self.client.chat, self.messages, json_output=True)
                self.assertFalse(error.retryable)
                self.assertEqual(error.usage["total_tokens"], 35)
                self.session.post.assert_called_once()

    def test_missing_usage_is_preserved_and_never_invented(self):
        response = completion(usage=False)
        self.assertNotIn("usage", validate_chat_response(response))
        response["usage"] = None
        self.assertIsNone(validate_chat_response(response)["usage"])

    def test_invalid_envelopes_choices_and_usage(self):
        cases = [
            None,
            [],
            {"error": {"message": "private vendor error"}},
            {"choices": []},
            {"choices": [None]},
            {"choices": [{"finish_reason": None}]},
            {"choices": [{"finish_reason": []}]},
            completion(""),
            completion("   "),
            completion(None),
            completion("x", "tool_calls"),
        ]
        for usage in (
            [],
            {"total_tokens": -1},
            {"prompt_tokens": True},
            {"completion_tokens": "10"},
            {"prompt_tokens_details": []},
            {"completion_tokens_details": {"reasoning_tokens": -1}},
        ):
            response = completion()
            response["usage"] = usage
            cases.append(response)
        for response in cases:
            with self.subTest(response=response):
                self.assert_code("invalid_response", validate_chat_response, response)

    def test_error_usage_contains_only_safe_known_counters(self):
        response = completion("{", usage=True)
        response["usage"]["private"] = "private credential"
        response["usage"]["prompt_tokens_details"]["private"] = "private text"
        error = self.assert_code("invalid_json", validate_chat_response, response, json_output=True)
        self.assertEqual(error.usage["total_tokens"], 35)
        self.assertNotIn("private", str(error.usage))

    def test_unknown_error_code_cannot_expose_untrusted_text(self):
        error = ModelAPIError("private credential/vendor text")
        self.assertEqual(error.code, "provider_error")
        self.assertNotIn("private credential", str(error))

    def test_legacy_message_is_accepted_but_never_exposed(self):
        for code in ("invalid_response", "invalid_extraction", "submission_unknown", "unknown"):
            with self.subTest(code=code):
                for error in (
                    ModelAPIError(
                        code,
                        "private credential/vendor text",
                        retryable=False,
                        status_code=200,
                        usage={"total_tokens": 35},
                    ),
                    ModelAPIError(
                        code,
                        message="private credential/vendor text",
                        retryable=False,
                        status_code=200,
                        usage={"total_tokens": 35},
                    ),
                ):
                    expected_code = code if code in ERROR_MESSAGES else "provider_error"
                    self.assertEqual(error.code, expected_code)
                    self.assertEqual(error.message, ERROR_MESSAGES[expected_code])
                    self.assertEqual(error.args, (error.message,))
                    self.assertFalse(error.retryable)
                    self.assertEqual(error.status_code, 200)
                    self.assertEqual(error.usage, {"total_tokens": 35})
                    self.assertNotIn("private credential", str(error))
                    self.assertNotIn("private credential", repr(error))

    def test_all_error_messages_include_chinese_feedback(self):
        for code in ERROR_MESSAGES:
            with self.subTest(code=code):
                self.assertRegex(ModelAPIError(code).message, "[\\u4e00-\\u9fff]")

    def test_invalid_extraction_preserves_code_and_offers_manual_entry(self):
        error = ModelAPIError("invalid_extraction", "private invalid payload")
        self.assertEqual(error.code, "invalid_extraction")
        self.assertFalse(error.retryable)
        self.assertIn("手工录入", error.message)
        self.assertNotIn("private invalid payload", str(error))

    def test_batch_error_codes_are_preserved_with_fixed_safe_messages(self):
        for code in (
            "submission_unknown",
            "batch_not_configured",
            "invalid_batch_request",
            "invalid_batch_file",
            "invalid_batch_response",
            "invalid_jsonl",
            "batch_file_too_large",
            "invalid_identifier",
            "network_error",
            "authentication_error",
        ):
            with self.subTest(code=code):
                error = ModelAPIError(code, retryable=False)
                self.assertEqual(error.code, code)
                self.assertFalse(error.retryable)
                self.assertTrue(error.message)
