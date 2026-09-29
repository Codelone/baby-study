"""Offline provider fixtures; no database, credentials, or real inference calls."""

import copy
import json
from unittest.mock import call, patch

import requests
from ai.client import ModelAPIError
from django.test import SimpleTestCase, override_settings

from ai.batch import BATCH_ENDPOINT, MAX_BATCH_BYTES, BatchClient

BASE_URL = "https://batch-api-cn.xiaomimimo.com/v1"
FAKE_KEY = "offline-test-key"
UPLOAD = {"id": "file-input", "purpose": "batch", "status": "active"}
BATCH = {
    "id": "batch-example",
    "status": "validating",
    "input_file_id": "file-input",
    "output_file_id": None,
    "error_file_id": None,
    "request_counts": {"total": 2, "completed": 0, "failed": 0},
}
SUCCESS_JSONL = (
    '{"id":"result-2","custom_id":"photo-2","response":{"status_code":200,'
    '"body":{"choices":[{"message":{"content":"版权页"}}],'
    '"usage":{"prompt_tokens":100,"completion_tokens":20,"total_tokens":120}}},"error":null}\n'
    '{"id":"result-1","custom_id":"photo-1","response":{"status_code":200,'
    '"body":{"choices":[{"message":{"content":"封面"}}]}},"error":null}\n'
)
ERROR_JSONL = (
    '{"id":"result-3","custom_id":"photo-3","response":null,'
    '"error":{"code":"inference_failed","message":"400 Bad Request"}}\n'
)


def request_row(custom_id="photo-1"):
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": BATCH_ENDPOINT,
        "body": {
            "model": "mimo-v2.6-flash",
            "messages": [{"role": "user", "content": "识别图书\n保留未知值"}],
            "response_format": {"type": "json_object"},
        },
    }


def response(payload=None, *, status=200, content=None):
    result = requests.Response()
    result.status_code = status
    result._content = content if content is not None else json.dumps(payload).encode("utf-8")
    result._content_consumed = True
    return result


class BatchClientTests(SimpleTestCase):
    def setUp(self):
        session_patch = patch("ai.batch.requests.Session")
        self.session = session_patch.start().return_value
        self.addCleanup(session_patch.stop)
        sleep_patch = patch("ai.batch.time.sleep")
        self.sleep = sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        self.client = BatchClient(FAKE_KEY, BASE_URL)
        self.request = self.session.request

    @override_settings(
        MIMO_API_KEY=FAKE_KEY,
        MIMO_BATCH_BASE_URL=BASE_URL + "/",
        MIMO_BASE_URL="https://realtime.example/v1",
        MIMO_TIMEOUT_SECONDS=90,
        MIMO_CONNECT_TIMEOUT_SECONDS=7,
        MIMO_MAX_RETRIES=1,
        MIMO_RETRY_BACKOFF_SECONDS=2,
        MIMO_RETRY_MAX_DELAY_SECONDS=6,
    )
    def test_from_settings_uses_separate_batch_configuration(self):
        client = BatchClient.from_settings()
        self.assertEqual(client.base_url, BASE_URL)
        self.assertEqual(client.timeout_seconds, 90)
        self.assertEqual(client.connect_timeout_seconds, 7)
        self.assertEqual(client.max_retries, 1)
        self.assertEqual(client.retry_backoff_seconds, 2)
        self.assertEqual(client.retry_max_delay_seconds, 6)

    def test_transport_has_bearer_timeout_and_no_redirect_or_environment_auth(self):
        self.request.return_value = response(BATCH)
        self.client.retrieve("batch-example")
        self.request.assert_called_once_with(
            "GET",
            BASE_URL + "/batches/batch-example",
            headers={"Authorization": "Bearer " + FAKE_KEY},
            timeout=(10, 60),
            allow_redirects=False,
        )
        self.assertIs(self.session.trust_env, False)

    def test_missing_key_prevents_any_network_request(self):
        with self.assertRaises(ModelAPIError) as raised:
            BatchClient("", BASE_URL)
        self.assertEqual(raised.exception.code, "not_configured")
        self.request.assert_not_called()

    def test_invalid_configuration_is_rejected_without_echoing_input(self):
        configurations = [
            {"base_url": "http://unsafe.example/v1"},
            {"base_url": "https://user:secret@example/v1"},
            {"base_url": "https://example/v1?key=private"},
            {"base_url": "https://example/v1#secret"},
            {"base_url": ""},
            {"base_url": 123},
            {"timeout_seconds": 0},
            {"timeout_seconds": float("nan")},
            {"connect_timeout_seconds": -1},
            {"max_retries": -1},
            {"max_retries": 6},
            {"max_retries": True},
            {"retry_backoff_seconds": 0},
            {"retry_max_delay_seconds": 61},
        ]
        for configuration in configurations:
            with self.subTest(configuration=configuration):
                values = {"api_key": FAKE_KEY, "base_url": BASE_URL, **configuration}
                with self.assertRaises(ModelAPIError) as raised:
                    BatchClient(**values)
                self.assertEqual(raised.exception.code, "invalid_configuration")
                self.assertNotIn("private", str(raised.exception))
                self.assertNotIn("secret", str(raised.exception))
        self.request.assert_not_called()

    def test_submit_multipart_then_batch_with_24h_endpoint_and_name(self):
        captured = {}

        def respond(method, url, **kwargs):
            if url.endswith("/files"):
                captured["filename"], stream, captured["mime"] = kwargs["files"]["file"]
                captured["content"] = stream.read()
                captured["purpose"] = kwargs["data"]
                return response(UPLOAD)
            return response(BATCH)

        self.request.side_effect = respond
        originals = [request_row(), request_row("photo-2")]
        result = self.client.submit(originals, name="图书照片")
        self.assertEqual(result["id"], "batch-example")
        self.assertEqual(result["status"], "validating")
        self.assertEqual(result["input_file_id"], "file-input")
        self.assertEqual(captured["purpose"], {"purpose": "batch"})
        self.assertEqual(captured["filename"], "input.jsonl")
        self.assertEqual(captured["mime"], "application/jsonl")
        self.assertTrue(captured["content"].endswith(b"\n"))
        self.assertEqual([json.loads(line) for line in captured["content"].splitlines()], originals)
        self.request.assert_has_calls(
            [
                call(
                    "POST",
                    BASE_URL + "/files",
                    headers={"Authorization": "Bearer " + FAKE_KEY},
                    timeout=(10, 60),
                    allow_redirects=False,
                    data={"purpose": "batch"},
                    files=self.request.call_args_list[0].kwargs["files"],
                ),
                call(
                    "POST",
                    BASE_URL + "/batches",
                    headers={"Authorization": "Bearer " + FAKE_KEY},
                    timeout=(10, 60),
                    allow_redirects=False,
                    json={
                        "input_file_id": "file-input",
                        "endpoint": BATCH_ENDPOINT,
                        "completion_window": "24h",
                        "name": "图书照片",
                    },
                ),
            ]
        )
        self.assertEqual(self.request.call_count, 2)
        self.sleep.assert_not_called()

    def test_image_body_and_custom_ids_are_preserved(self):
        item = request_row("generation-1/photo-1")
        item["body"]["messages"][0]["content"] = [
            {"type": "text", "text": "仅提取可见字段"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1hZ2U="}},
        ]
        captured = []

        def respond(method, url, **kwargs):
            if url.endswith("/files"):
                captured.append(json.loads(kwargs["files"]["file"][1].read()))
                return response(UPLOAD)
            return response(BATCH)

        self.request.side_effect = respond
        original = copy.deepcopy(item)
        self.client.submit([item])
        self.assertEqual(captured, [original])
        self.assertEqual(item, original)

    def test_invalid_requests_fail_before_upload(self):
        invalid_items = [
            None,
            {},
            {**request_row(), "custom_id": ""},
            {**request_row(), "custom_id": 1},
            {**request_row(), "custom_id": []},
            {**request_row(), "method": "GET"},
            {**request_row(), "url": "https://example/chat/completions"},
            {**request_row(), "url": "/v1/responses"},
            {**request_row(), "body": []},
            {**request_row(), "body": {"model": "", "messages": []}},
            {**request_row(), "body": {"model": "mimo-v2.6-flash", "messages": "hello"}},
            {**request_row(), "body": {"model": "mimo-v2.6-flash", "messages": [{}]}},
            {
                **request_row(),
                "body": {
                    "model": "mimo-v2.6-flash",
                    "messages": [{"role": [], "content": "hello"}],
                },
            },
            {
                **request_row(),
                "body": {
                    "model": "mimo-v2.6-flash",
                    "messages": [{"role": "user", "content": {"text": "hello"}}],
                },
            },
            {
                **request_row(),
                "body": {
                    "model": "mimo-v2.6-flash",
                    "messages": [{"role": "user", "content": ["hello"]}],
                },
            },
            {**request_row(), "body": {**request_row()["body"], "stream": True}},
            {**request_row(), "body": {**request_row()["body"], "stream": 0}},
        ]
        for item in invalid_items:
            with self.subTest(item=item):
                with self.assertRaises(ModelAPIError) as raised:
                    self.client.submit([item])
                self.assertEqual(raised.exception.code, "invalid_batch_request")
        for items in ([], None, "not-jsonl", [request_row(), request_row()]):
            with self.subTest(items=items):
                with self.assertRaises(ModelAPIError) as raised:
                    self.client.submit(items)
                self.assertEqual(raised.exception.code, "invalid_batch_request")
        self.request.assert_not_called()

    def test_non_json_values_fail_before_upload(self):
        for value in (float("nan"), float("inf"), object(), "\ud800"):
            with self.subTest(value=repr(value)):
                item = request_row()
                item["body"]["extra"] = value
                with self.assertRaises(ModelAPIError) as raised:
                    self.client.submit([item])
                self.assertEqual(raised.exception.code, "invalid_jsonl")
        self.request.assert_not_called()

    def test_malformed_multimodal_parts_and_token_limits_fail_before_upload(self):
        for content in (
            [{"type": "text"}],
            [{"type": "image_url", "image_url": {}}],
            [{"type": "input_audio", "input_audio": {"data": None}}],
            [{"type": [], "text": "hello"}],
            [{"type": "unknown", "data": "hello"}],
        ):
            with self.subTest(content=content):
                item = request_row()
                item["body"]["messages"][0]["content"] = content
                with self.assertRaises(ModelAPIError) as raised:
                    self.client.submit([item])
                self.assertEqual(raised.exception.code, "invalid_batch_request")
        for limit in (0, -1, True, "100", 131073):
            with self.subTest(limit=limit):
                item = request_row()
                item["body"]["max_completion_tokens"] = limit
                with self.assertRaises(ModelAPIError) as raised:
                    self.client.submit([item])
                self.assertEqual(raised.exception.code, "invalid_batch_request")
        for name in (None, b"bytes", "\ud800"):
            with self.subTest(name=repr(name)):
                with self.assertRaises(ModelAPIError) as raised:
                    self.client.submit([request_row()], name=name)
                self.assertEqual(raised.exception.code, "invalid_batch_request")
        self.request.assert_not_called()

    def test_128_mib_limit_counts_utf8_and_final_newline(self):
        self.assertEqual(MAX_BATCH_BYTES, 128 * 1024 * 1024)
        item = request_row()
        payload_size = len((json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8"))
        self.request.side_effect = [response(UPLOAD), response(BATCH)]
        with patch("ai.batch.MAX_BATCH_BYTES", payload_size):
            self.client.submit([item])
        self.request.reset_mock()
        with patch("ai.batch.MAX_BATCH_BYTES", payload_size - 1):
            with self.assertRaises(ModelAPIError) as raised:
                self.client.submit([item])
        self.assertEqual(raised.exception.code, "batch_file_too_large")
        self.request.assert_not_called()

    def test_completed_fixture_preserves_unordered_results_and_usage(self):
        completed = {
            **BATCH,
            "status": "completed",
            "output_file_id": "file-output",
            "request_counts": {"total": 2, "completed": 2, "failed": 0},
        }
        self.request.side_effect = [response(completed), response(content=SUCCESS_JSONL.encode())]
        batch = self.client.retrieve("batch-example")
        rows = self.client.results(batch["output_file_id"])
        self.assertEqual([row["custom_id"] for row in rows], ["photo-2", "photo-1"])
        self.assertEqual(rows[0]["response"]["body"]["usage"]["total_tokens"], 120)
        self.assertEqual(rows[1]["response"]["body"]["choices"][0]["message"]["content"], "封面")
        self.assertEqual(
            self.request.call_args.args[:2], ("GET", BASE_URL + "/files/file-output/content")
        )

    def test_partial_failure_downloads_output_and_error_files(self):
        partial = {
            **BATCH,
            "status": "completed",
            "output_file_id": "file-output",
            "error_file_id": "file-error",
            "request_counts": {"total": 3, "completed": 2, "failed": 1},
        }
        self.request.side_effect = [
            response(partial),
            response(content=SUCCESS_JSONL.encode()),
            response(content=ERROR_JSONL.encode()),
        ]
        batch = self.client.retrieve("batch-example")
        success = self.client.results(batch["output_file_id"])
        errors = self.client.results(batch["error_file_id"])
        self.assertEqual(len(success), 2)
        self.assertEqual(errors[0]["custom_id"], "photo-3")
        self.assertEqual(errors[0]["error"]["code"], "inference_failed")
        self.assertIsNone(errors[0]["response"])
        self.assertEqual(
            self.request.call_args.args[:2], ("GET", BASE_URL + "/files/file-error/content")
        )

    def test_cancel_is_async_and_completed_output_is_retained(self):
        cancelling = {**BATCH, "status": "cancelling"}
        cancelled = {**BATCH, "status": "cancelled", "output_file_id": "file-output"}
        self.request.side_effect = [
            response(cancelling),
            response(cancelled),
            response(content=SUCCESS_JSONL.encode()),
        ]
        self.assertEqual(self.client.cancel("batch-example")["status"], "cancelling")
        batch = self.client.retrieve("batch-example")
        self.assertEqual(batch["status"], "cancelled")
        self.assertEqual(len(self.client.results(batch["output_file_id"])), 2)
        self.assertEqual(
            self.request.call_args_list[0].args[:2],
            ("POST", BASE_URL + "/batches/batch-example/cancel"),
        )
        self.sleep.assert_not_called()

    def test_empty_output_and_utf8_bom_are_supported(self):
        self.request.side_effect = [
            response(content=b""),
            response(content=b"\xef\xbb\xbf" + ERROR_JSONL.encode()),
        ]
        self.assertEqual(self.client.results("file-empty"), [])
        self.assertEqual(self.client.results("file-error")[0]["custom_id"], "photo-3")

    def test_repeated_download_has_no_hidden_state(self):
        self.request.side_effect = [
            response(content=SUCCESS_JSONL.encode()),
            response(content=SUCCESS_JSONL.encode()),
        ]
        self.assertEqual(self.client.results("file-output"), self.client.results("file-output"))

    def test_malformed_jsonl_is_rejected_without_partial_results(self):
        invalid_contents = [
            b"not JSON",
            b"[]",
            b"\xff",
            b'{"custom_id": NaN}',
            b'{"custom_id":"a","custom_id":"b","response":null}',
            (ERROR_JSONL + ERROR_JSONL).encode(),
            (SUCCESS_JSONL + "not JSON").encode(),
            b'{"custom_id":1,"error":{}}',
            b'{"custom_id":"a","error":"broken"}',
            b'{"custom_id":"a","response":{}}',
            b'{"custom_id":"a","response":{"status_code":true,"body":{}}}',
            b'{"custom_id":"a","response":{"status_code":200,"body":[]}}',
        ]
        for content in invalid_contents:
            with self.subTest(content=content):
                self.request.return_value = response(content=content)
                with self.assertRaises(ModelAPIError) as raised:
                    self.client.results("file-output")
                self.assertEqual(raised.exception.code, "invalid_jsonl")
                self.assertFalse(raised.exception.retryable)
                self.assertNotIn("not JSON", str(raised.exception))

    def test_provider_errors_are_sanitized_in_status_and_result_files(self):
        sensitive = {
            "code": "inference_failed",
            "message": FAKE_KEY + " private-input",
            "request": {"Authorization": "Bearer " + FAKE_KEY},
        }
        failed_batch = {**BATCH, "status": "failed", "errors": {"data": [sensitive]}}
        failed_row = {"custom_id": "photo-1", "response": None, "error": sensitive}
        failed_response = {
            "custom_id": "photo-2",
            "error": None,
            "response": {"status_code": 400, "body": {"error": sensitive}},
        }
        self.request.side_effect = [
            response(failed_batch),
            response(
                content=(json.dumps(failed_row) + "\n" + json.dumps(failed_response)).encode()
            ),
        ]
        batch = self.client.retrieve("batch-example")
        results = self.client.results("file-error")
        self.assertNotIn(FAKE_KEY, json.dumps([batch, results]))
        self.assertNotIn("private-input", json.dumps([batch, results]))
        self.assertEqual(results[0]["error"]["code"], "inference_failed")

    def test_unknown_provider_error_code_does_not_echo_sensitive_code(self):
        self.request.return_value = response(
            content=json.dumps(
                {
                    "custom_id": "photo-1",
                    "response": None,
                    "error": {"code": FAKE_KEY, "message": "sensitive"},
                }
            ).encode()
        )
        result = self.client.results("file-error")
        self.assertEqual(result[0]["error"]["code"], "provider_error")
        self.assertNotIn(FAKE_KEY, json.dumps(result))

    def test_get_retries_network_rate_limit_and_transient_errors_with_bounds(self):
        for failure in (requests.Timeout("secret"), response(status=429), response(status=503)):
            with self.subTest(failure=type(failure).__name__):
                self.request.reset_mock()
                self.sleep.reset_mock()
                self.request.side_effect = [failure, response(BATCH)]
                self.assertEqual(self.client.retrieve("batch-example")["id"], "batch-example")
                self.assertEqual(self.request.call_count, 2)
                self.sleep.assert_called_once_with(1)

    def test_get_retry_exhaustion_and_backoff_cap(self):
        client = BatchClient(
            FAKE_KEY, BASE_URL, max_retries=2, retry_backoff_seconds=3, retry_max_delay_seconds=4
        )
        self.request.side_effect = requests.ConnectionError("secret " + FAKE_KEY)
        with self.assertRaises(ModelAPIError) as raised:
            client.retrieve("batch-example")
        self.assertEqual(self.request.call_count, 3)
        self.assertEqual(self.sleep.call_args_list, [call(3), call(4)])
        self.assertEqual(raised.exception.code, "network_error")
        self.assertTrue(raised.exception.retryable)
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertNotIn(FAKE_KEY, str(raised.exception))

    def test_get_rate_limit_exhaustion_preserves_classification(self):
        self.client.max_retries = 1
        self.request.side_effect = [response(status=429), response(status=429)]
        with self.assertRaises(ModelAPIError) as raised:
            self.client.retrieve("batch-example")
        self.assertEqual(self.request.call_count, 2)
        self.assertEqual(raised.exception.code, "rate_limited")
        self.assertEqual(raised.exception.status_code, 429)
        self.assertTrue(raised.exception.retryable)

    def test_post_rate_limit_does_not_automatically_retry(self):
        self.request.side_effect = [response(UPLOAD), response(status=429)]
        with self.assertRaises(ModelAPIError) as raised:
            self.client.submit([request_row()])
        error = raised.exception
        self.assertEqual(error.code, "rate_limited")
        self.assertTrue(error.retryable)
        self.assertEqual(error.input_file_id, "file-input")
        self.assertEqual(self.request.call_count, 2)
        self.sleep.assert_not_called()

    def test_uncertain_batch_submission_is_not_retried_and_retains_file_id(self):
        for failure in (
            requests.Timeout("secret " + FAKE_KEY),
            requests.ConnectionError("private-input"),
            response(status=408),
            response(status=503),
            response(content=b"not-json"),
            response({"missing_id": True}),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.request.reset_mock()
                self.request.side_effect = [response(UPLOAD), failure]
                with self.assertRaises(ModelAPIError) as raised:
                    self.client.submit([request_row()])
                error = raised.exception
                self.assertEqual(error.code, "submission_unknown")
                self.assertFalse(error.retryable)
                self.assertEqual(error.operation, "submit")
                self.assertEqual(error.input_file_id, "file-input")
                self.assertEqual(self.request.call_count, 2)
                self.assertNotIn(FAKE_KEY, str(error))
        self.sleep.assert_not_called()

    def test_uncertain_upload_stops_before_batch_creation(self):
        self.request.side_effect = requests.Timeout("secret")
        with self.assertRaises(ModelAPIError) as raised:
            self.client.submit([request_row()])
        self.assertEqual(raised.exception.code, "submission_unknown")
        self.assertEqual(raised.exception.operation, "upload")
        self.assertFalse(hasattr(raised.exception, "input_file_id"))
        self.request.assert_called_once()
        self.sleep.assert_not_called()

    def test_incomplete_batch_response_retains_provider_batch_id(self):
        self.request.side_effect = [response(UPLOAD), response({"id": "batch-example"})]
        with self.assertRaises(ModelAPIError) as raised:
            self.client.submit([request_row()])
        self.assertEqual(raised.exception.code, "submission_unknown")
        self.assertEqual(raised.exception.provider_batch_id, "batch-example")
        self.assertEqual(raised.exception.input_file_id, "file-input")

    def test_cancel_network_failure_is_sent_once(self):
        self.request.side_effect = requests.Timeout("secret")
        with self.assertRaises(ModelAPIError) as raised:
            self.client.cancel("batch-example")
        self.assertEqual(raised.exception.code, "submission_unknown")
        self.assertFalse(raised.exception.retryable)
        self.request.assert_called_once()

    def test_invalid_identifiers_cannot_change_url_path_or_origin(self):
        for identifier in (
            "../private",
            "file/input",
            "https://other.example",
            "a?key=secret",
            "",
            None,
            [],
            "a" * 257,
        ):
            for operation in (self.client.retrieve, self.client.results, self.client.cancel):
                with self.subTest(identifier=identifier, operation=operation.__name__):
                    with self.assertRaises(ModelAPIError) as raised:
                        operation(identifier)
                    self.assertEqual(raised.exception.code, "invalid_identifier")
        self.request.assert_not_called()

    def test_authentication_or_invalid_read_response_does_not_retry(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.request.reset_mock()
                self.request.return_value = response({"message": FAKE_KEY}, status=status)
                with self.assertRaises(ModelAPIError) as raised:
                    self.client.retrieve("batch-example")
                self.assertEqual(raised.exception.code, "authentication_error")
                self.assertEqual(raised.exception.status_code, status)
                self.assertNotIn(FAKE_KEY, str(raised.exception))
                self.request.assert_called_once()
        for payload in ({}, [], {"id": "batch-example"}):
            with self.subTest(payload=payload):
                self.request.reset_mock()
                self.request.return_value = response(payload)
                with self.assertRaises(ModelAPIError) as raised:
                    self.client.retrieve("batch-example")
                self.assertEqual(raised.exception.code, "invalid_response")
                self.request.assert_called_once()
