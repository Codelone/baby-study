import json
import sys
from types import ModuleType
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from catalog.intake.recognition import (
    empty_fields,
    extract_photo,
    normalize_isbn,
    recognition_messages,
    validate_extraction,
)

DATA_URL = "data:image/png;base64,aW1hZ2U="


def extraction(**values):
    return {
        "fields": {},
        "photo_type": "cover",
        "isbn_kind": "none",
        "identity_text": "",
        "issues": [],
        **values,
    }


class ExtractionValidationTests(SimpleTestCase):
    def test_missing_claims_remain_empty_and_unverified_levels_are_excluded(self):
        result = validate_extraction(
            extraction(fields={"title": "可见书名", "reading_attributes": {"lexile": "500L"}})
        )
        self.assertEqual(result["fields"], {**empty_fields(), "title": "可见书名"})
        self.assertIn("unsupported_field", [item["code"] for item in result["issues"]])
        self.assertNotIn("reading_attributes", result["fields"])

    def test_visible_fields_preserve_roles_edition_and_identity_text(self):
        fields = {
            "title": "  Forest Walk: Volume 2  ",
            "authors": ["A. Writer", "A. Writer"],
            "illustrators": ["B. Artist"],
            "translators": ["C. Translator"],
            "publisher": "Visible Press",
            "edition": "第二版",
            "publication_date": "2024年6月",
            "pages": 32,
            "language": "bilingual",
        }
        result = validate_extraction(
            extraction(fields=fields, identity_text="Forest Walk: Volume 2\n第二版")
        )
        self.assertEqual(result["fields"]["title"], "Forest Walk: Volume 2")
        self.assertEqual(result["fields"]["authors"], ["A. Writer"])
        self.assertEqual(result["fields"]["illustrators"], ["B. Artist"])
        self.assertEqual(result["identity_text"], "Forest Walk: Volume 2\n第二版")
        self.assertEqual(result["fields"]["edition"], "第二版")
        self.assertEqual(result["issues"], [])

    def test_isbn10_and13_normalize_to_same_valid_identity(self):
        for value in ("0-375-82778-1", "978-0-375-82778-5", "９７８０３７５８２７７８５"):
            with self.subTest(value=value):
                result = validate_extraction(extraction(fields={"isbn": value}, isbn_kind="single"))
                self.assertEqual(result["fields"]["isbn"], "9780375827785")
        self.assertEqual(normalize_isbn("0-8044-2957-X"), "9780804429573")

    def test_invalid_isbn_is_not_identity_and_original_value_is_a_doubt(self):
        for value in ("9780375827786", "1234567890128", "978ABCDEFGHIJ", "0375827780"):
            with self.subTest(value=value):
                result = validate_extraction(extraction(fields={"isbn": value}, isbn_kind="single"))
                self.assertEqual(result["fields"]["isbn"], "")
                self.assertEqual(result["issues"][0]["code"], "invalid_isbn")
                self.assertEqual(result["issues"][0]["value"], value)

    def test_set_other_and_unknown_numbers_are_not_single_book_identity(self):
        for kind in ("set", "other", "unknown", "none"):
            with self.subTest(kind=kind):
                result = validate_extraction(
                    extraction(
                        fields={"title": "单本分册", "isbn": "9780375827785"}, isbn_kind=kind
                    )
                )
                self.assertEqual(result["fields"]["isbn"], "")
                self.assertEqual(result["fields"]["title"], "单本分册")
                self.assertEqual(result["issues"][0]["value"], "9780375827785")

    def test_marketing_blurry_and_multiple_books_never_produce_book_claims(self):
        for kind in ("marketing", "blurry", "multiple_books"):
            with self.subTest(kind=kind):
                result = validate_extraction(
                    extraction(
                        fields={"title": "Guess", "isbn": "9780375827785"},
                        photo_type=kind,
                        isbn_kind="single",
                        identity_text="可见但不能确定归属的字",
                    )
                )
                self.assertEqual(result["fields"], empty_fields())
                self.assertIn(kind, [note["code"] for note in result["issues"]])
                self.assertEqual(result["identity_text"], "可见但不能确定归属的字")
                self.assertEqual(validate_extraction(result), result)

    def test_wrong_types_are_cleared_without_losing_valid_claims(self):
        result = validate_extraction(
            extraction(
                fields={
                    "title": "Visible title",
                    "publisher": ["Wrong type"],
                    "authors": "Wrong type",
                    "themes": [123],
                    "language": "french",
                    "pages": True,
                },
                photo_type=[],
                isbn_kind={},
                identity_text=["Wrong type"],
            )
        )
        self.assertEqual(result["fields"], {**empty_fields(), "title": "Visible title"})
        self.assertEqual(result["photo_type"], "unknown")
        self.assertEqual(result["isbn_kind"], "unknown")
        self.assertEqual(result["identity_text"], "")
        self.assertEqual(len(result["issues"]), 8)

    def test_malformed_root_is_a_failed_extraction(self):
        for payload in (None, [], {}, {"fields": []}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate_extraction(payload)

    def test_private_photo_does_not_become_remote_cover_and_claims_stay_unmodified(self):
        payload = extraction(fields={"title": "A book", "cover_url": "https://invented.test/a.jpg"})
        before = json.dumps(payload)
        result = validate_extraction(payload)
        self.assertEqual(result["fields"]["cover_url"], "")
        self.assertEqual(json.dumps(payload), before)


class FakeModelAPIError(Exception):
    def __init__(self, code, message, retryable, status_code=None, usage=None):
        super().__init__(message)
        self.code, self.message = code, message
        self.retryable, self.status_code = retryable, status_code
        self.usage = usage


class PhotoChatTests(SimpleTestCase):
    def setUp(self):
        # The model adapter is developed in another worktree. No real SDK/network needed.
        module = ModuleType("ai.client")
        module.ModelAPIError = FakeModelAPIError
        self.module_patch = patch.dict(sys.modules, {"ai.client": module})
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)

    def test_prompt_contains_single_private_image_and_visible_claim_constraints(self):
        messages = recognition_messages(DATA_URL)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["content"][1]["image_url"]["url"], DATA_URL)
        for constraint in ("JSON", "marketing", "multiple_books", "套装", "蓝思", "identity_text"):
            self.assertIn(constraint, messages[0]["content"])
        for invalid in (None, "https://remote.test/a.jpg", "data:audio/wav;base64,aA=="):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                recognition_messages(invalid)

    def test_full_chat_response_is_parsed_and_actual_model_is_used(self):
        client = Mock()
        client.chat.return_value = {
            "model": "configured-image-model",
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            extraction(fields={"title": "Visible title"}, model_name="invented")
                        )
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 100},
        }
        result = extract_photo(client, DATA_URL)
        self.assertEqual(result["fields"]["title"], "Visible title")
        self.assertEqual(result["model_name"], "configured-image-model")
        client.chat.assert_called_once_with(
            recognition_messages(DATA_URL), capability="image", json_output=True
        )

    def test_invalid_truncated_refused_or_tool_output_has_safe_error(self):
        messages = [
            ("length", {"content": '{"fields":{"title":"secret-private-text"}}'}),
            ("stop", {"content": "```json\n{}\n```"}),
            ("stop", {"content": "broken JSON secret-private-text"}),
            ("stop", {"content": "[]"}),
            ("stop", {"content": "{}"}),
            ("stop", {"content": []}),
            ("stop", {"content": '{"fields":{"title":"A","title":"B"}}'}),
            ("stop", {"content": '{"fields":{"pages":NaN}}'}),
            ("stop", {"content": '{"fields":{}}', "refusal": "No"}),
            ("stop", {"content": '{"fields":{}}', "tool_calls": [{"id": "1"}]}),
        ]
        responses = [
            None,
            {},
            {"choices": []},
            {"choices": [None]},
            {"choices": "bad"},
            {"choices": [{"finish_reason": "stop", "message": None}]},
        ] + [
            {"choices": [{"finish_reason": finish, "message": message}]}
            for finish, message in messages
        ]
        for response in responses:
            with self.subTest(response=response):
                client = Mock()
                client.chat.return_value = response
                with self.assertRaises(FakeModelAPIError) as caught:
                    extract_photo(client, DATA_URL)
                self.assertEqual(caught.exception.code, "invalid_extraction")
                self.assertFalse(caught.exception.retryable)
                self.assertNotIn("secret-private-text", str(caught.exception))
                self.assertNotIn(DATA_URL, str(caught.exception))

    def test_invalid_business_result_preserves_usage_for_worker_accounting(self):
        client = Mock()
        client.chat.return_value = {
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        }
        with self.assertRaises(FakeModelAPIError) as caught:
            extract_photo(client, DATA_URL)
        self.assertEqual(caught.exception.usage, {"prompt_tokens": 100, "completion_tokens": 10})
        self.assertIsNone(caught.exception.__cause__)

    def test_supplier_error_propagates_without_automatic_extra_call(self):
        client = Mock()
        error = FakeModelAPIError("rate_limited", "稍后重试", True, 429)
        client.chat.side_effect = error
        with self.assertRaises(FakeModelAPIError) as caught:
            extract_photo(client, DATA_URL)
        self.assertIs(caught.exception, error)
        self.assertEqual(client.chat.call_count, 1)
