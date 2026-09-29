"""Offline public-source fixtures; no model, database or real paid API calls."""

import copy
import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import requests
from django.test import SimpleTestCase, override_settings

from catalog.enrichment.sources import SourceError
from catalog.intake import enrichment

ISBN = "9780375827785"
OTHER_ISBN = "9780140328721"
EDITION = {
    "key": "/books/OL100M",
    "title": "A Visible Reader",
    "isbn_13": [ISBN],
    "publishers": ["Visible Press"],
    "publish_date": "2004",
    "physical_format": "Paperback",
    "number_of_pages": 48,
    "edition_name": "First edition",
    "languages": [{"key": "/languages/eng"}],
    "covers": [123],
    "authors": [{"key": "/authors/OL10A"}],
}
AUTHOR = {"key": "/authors/OL10A", "name": "Visible Author"}


class Response:
    def __init__(self, data=None, status=200, headers=None, content=None):
        self.status_code = status
        self.headers = headers or {}
        self.content = json.dumps(data).encode() if content is None else content
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, chunk_size):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start : start + chunk_size]


def search_result(*keys):
    return {
        "docs": [
            {
                "key": "/works/OL999W",
                "title": "WORK TITLE MUST NOT BE APPLIED",
                "author_name": ["WORK AUTHOR MUST NOT BE APPLIED"],
                "isbn": [OTHER_ISBN],
                "publisher": ["WORK PRESS MUST NOT BE APPLIED"],
                "editions": {"docs": [{"key": key} for key in keys]},
            }
        ]
    }


class CandidateLookupTests(SimpleTestCase):
    def setUp(self):
        enrichment._CACHE.clear()
        self.throttle = patch.object(enrichment, "_throttle").start()
        self.sleep = patch.object(enrichment.time, "sleep").start()
        self.get = patch.object(requests.Session, "get").start()
        self.addCleanup(patch.stopall)
        self.addCleanup(enrichment._CACHE.clear)

    def fixture(self, *data):
        self.get.side_effect = [
            item if isinstance(item, (Response, Exception)) else Response(item) for item in data
        ]

    @override_settings(INTAKE_ENRICHMENT_PROXY_URL="http://127.0.0.1:8899")
    def test_explicit_public_lookup_proxy_does_not_enable_environment_authentication(self):
        client = enrichment._PublicClient()
        self.addCleanup(client.close)
        self.assertFalse(client.session.trust_env)
        self.assertEqual(client.session.proxies, {"https": "http://127.0.0.1:8899"})
        self.assertNotIn("Authorization", client.session.headers)

    @override_settings(INTAKE_ENRICHMENT_PROXY_URL="http://user:secret@127.0.0.1:8899")
    def test_invalid_proxy_configuration_is_a_safe_lookup_failure(self):
        result = enrichment.search_candidates({"isbn": ISBN})
        self.assertEqual(result["candidates"], [])
        self.assertIn("代理配置无效", result["issues"][0])
        self.assertNotIn("secret", str(result))
        self.get.assert_not_called()

    def test_isbn_10_lookup_returns_version_claims_without_mutation_or_media_fetch(self):
        draft = {
            "isbn": "0-375-82778-1",
            "title": "A Visible Reader",
            "pages": 999,
            "manual_fields": ["title", "pages"],
            "reading_attributes": {"lexile": 800},
        }
        before = copy.deepcopy(draft)
        self.fixture(EDITION, AUTHOR)
        result = enrichment.search_candidates(draft)
        self.assertEqual(draft, before)
        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["id"], "openlibrary:OL100M")
        self.assertEqual(candidate["fields"]["isbn"], ISBN)
        self.assertEqual(candidate["fields"]["pages"], 48)
        self.assertEqual(candidate["fields"]["authors"], ["Visible Author"])
        self.assertEqual(
            candidate["fields"]["cover_source_url"], "https://openlibrary.org/books/OL100M"
        )
        self.assertNotIn("reading_attributes", candidate["fields"])
        self.assertEqual(
            {item["field"] for item in candidate["evidence"]}, set(candidate["fields"])
        )
        for item in candidate["evidence"]:
            self.assertEqual(item["kind"], "web")
            self.assertEqual(item["status"], "candidate")
            self.assertTrue(item["source_url"].startswith(enrichment.BASE_URL))
            self.assertTrue(item["source_label"])
            self.assertTrue(item["collected_at"])
        self.assertTrue(any("尚未经用户核对" in item for item in candidate["issues"]))
        urls = [call.args[0] for call in self.get.call_args_list]
        self.assertEqual(
            urls,
            [
                f"https://openlibrary.org/isbn/{ISBN}.json",
                "https://openlibrary.org/authors/OL10A.json",
            ],
        )
        self.assertFalse(any("covers.openlibrary" in url for url in urls))
        for call in self.get.call_args_list:
            self.assertFalse(call.kwargs["allow_redirects"])
            self.assertTrue(call.kwargs["stream"])
            self.assertLessEqual(max(call.kwargs["timeout"]), 5)

    def test_no_isbn_queries_visible_author_publisher_and_fetches_specific_edition(self):
        self.fixture(search_result("/books/OL100M"), EDITION, AUTHOR)
        result = enrichment.search_candidates(
            {
                "title": "A Visible Reader",
                "authors": ["Visible Author"],
                "publisher": "Visible Press",
                "edition": "First edition",
            }
        )
        query = parse_qs(urlparse(self.get.call_args_list[0].args[0]).query)
        self.assertEqual(
            query["q"],
            ['title:"A Visible Reader" AND author:"Visible Author" AND publisher:"Visible Press"'],
        )
        self.assertIn("editions.key", query["fields"][0])
        self.assertEqual(result["candidates"][0]["fields"]["title"], EDITION["title"])
        self.assertEqual(result["candidates"][0]["fields"]["publisher"], "Visible Press")
        self.assertNotIn("WORK", json.dumps(result))

    def test_isbn_not_found_falls_back_to_title_with_isbn_conflict(self):
        edition = {**EDITION, "isbn_13": [OTHER_ISBN], "authors": []}
        self.fixture(Response(status=404), search_result("/books/OL100M"), edition)
        result = enrichment.search_candidates({"title": EDITION["title"], "isbn": ISBN})
        self.assertEqual(result["candidates"][0]["fields"]["isbn"], OTHER_ISBN)
        self.assertIn("未收录", " ".join(result["issues"]))
        self.assertIn("ISBN 与照片/草稿不一致", " ".join(result["candidates"][0]["issues"]))

    def test_same_title_different_versions_stay_separate_and_conflicts_are_visible(self):
        first = {**EDITION, "authors": []}
        second = {
            **first,
            "key": "/books/OL101M",
            "isbn_13": [OTHER_ISBN],
            "publishers": ["Other Press"],
            "edition_name": "Second edition",
        }
        self.fixture(
            search_result("/books/OL100M", "/books/OL101M", "/books/OL100M"), first, second
        )
        result = enrichment.search_candidates(
            {"title": EDITION["title"], "publisher": "Visible Press", "edition": "First edition"}
        )
        self.assertEqual(
            [c["id"] for c in result["candidates"]], ["openlibrary:OL100M", "openlibrary:OL101M"]
        )
        self.assertIn("出版社与照片/草稿不一致", " ".join(result["candidates"][1]["issues"]))
        self.assertIn("版本说明与照片/草稿不一致", " ".join(result["candidates"][1]["issues"]))

    def test_similar_title_requires_review_and_is_not_a_match(self):
        self.fixture(search_result("/books/OL100M"), {**EDITION, "authors": []})
        result = enrichment.search_candidates({"title": "A Different Reader"})
        candidate = result["candidates"][0]
        self.assertIn("书名与照片/草稿不一致", " ".join(candidate["issues"]))
        self.assertNotIn("existing_book_id", candidate)
        self.assertNotIn("matched", candidate)

    def test_sets_never_supply_a_single_book_isbn_or_invent_members(self):
        for update in (
            {"title": "Readers Boxed Set"},
            {"title": "读物全三册"},
            {"number_of_volumes": 2},
        ):
            with self.subTest(update=update):
                enrichment._CACHE.clear()
                self.fixture({**EDITION, **update, "authors": []})
                result = enrichment.search_candidates({"isbn": ISBN})
                candidate = result["candidates"][0]
                self.assertNotIn("isbn", candidate["fields"])
                self.assertNotIn("members", candidate)
                self.assertIn("套装 ISBN 不作为单本 ISBN", " ".join(candidate["issues"]))

    def test_multiple_isbns_are_not_arbitrarily_chosen_without_visible_isbn(self):
        self.fixture(
            search_result("/books/OL100M"),
            {**EDITION, "isbn_13": [ISBN, OTHER_ISBN], "authors": []},
        )
        result = enrichment.search_candidates({"title": EDITION["title"]})
        self.assertNotIn("isbn", result["candidates"][0]["fields"])
        self.assertIn("多个不同 ISBN", " ".join(result["candidates"][0]["issues"]))

    def test_source_isbn_10_and_13_equivalents_are_one_identity(self):
        self.fixture({**EDITION, "isbn_13": [ISBN], "isbn_10": ["0375827781"], "authors": []})
        result = enrichment.search_candidates({"isbn": ISBN})
        self.assertEqual(result["candidates"][0]["fields"]["isbn"], ISBN)

    def test_missing_isbn_and_version_metadata_are_reported_without_guessing(self):
        edition = {"key": "/books/OL100M", "title": EDITION["title"]}
        self.fixture(search_result("/books/OL100M"), edition)
        result = enrichment.search_candidates(
            {"title": EDITION["title"], "edition": "Special edition", "authors": ["Visible Author"]}
        )
        candidate = result["candidates"][0]
        self.assertEqual(candidate["fields"], {"title": EDITION["title"]})
        self.assertIn("缺少有效 ISBN", " ".join(candidate["issues"]))
        self.assertIn("缺少版本说明", " ".join(candidate["issues"]))
        self.assertIn("缺少可核对的版本作者", " ".join(candidate["issues"]))

    def test_malformed_optional_source_fields_are_ignored_without_inventing_metadata(self):
        edition = {
            "key": "/books/OL100M",
            "title": EDITION["title"],
            "isbn_13": [{"invalid": "value"}, "not-an-isbn"],
            "languages": [{"key": ["/languages/eng"]}, {"key": {"bad": "value"}}],
            "publishers": [None, 1, ""],
            "number_of_pages": True,
            "covers": [True, "123", -1],
            "authors": ["invalid", {"key": []}],
            "description": {"value": []},
        }
        self.fixture(edition)
        candidate = enrichment.search_candidates({"isbn": ISBN})["candidates"][0]
        self.assertEqual(candidate["fields"], {"title": EDITION["title"]})
        self.assertIn("无效 ISBN", " ".join(candidate["issues"]))
        self.assertEqual(self.get.call_count, 1)

    def test_missing_and_extra_languages_do_not_invent_bilingual_classification(self):
        for languages in (
            [],
            ["/languages/fre"],
            ["/languages/eng", "/languages/chi", "/languages/fre"],
        ):
            with self.subTest(languages=languages):
                enrichment._CACHE.clear()
                self.fixture(
                    {**EDITION, "authors": [], "languages": [{"key": key} for key in languages]}
                )
                candidate = enrichment.search_candidates({"isbn": ISBN})["candidates"][0]
                self.assertNotIn("language", candidate["fields"])

    def test_no_results_and_work_only_results_clearly_return_insufficient_source(self):
        for data in (
            {"docs": []},
            {
                "docs": [
                    {
                        "key": "/works/OL1W",
                        "title": "Reader",
                        "isbn": [ISBN],
                        "edition_key": ["OL100M"],
                    }
                ]
            },
        ):
            with self.subTest(data=data):
                enrichment._CACHE.clear()
                self.fixture(data)
                result = enrichment.search_candidates({"title": "Reader"})
                self.assertEqual(result["candidates"], [])
                self.assertIn("未提供足够", " ".join(result["issues"]))

    def test_invalid_and_absent_search_fields_do_not_fetch_arbitrary_urls(self):
        for fields in (
            None,
            {},
            {"isbn": "9780000000000"},
            {"title": "x" * 501},
            {"source_url": "http://127.0.0.1"},
        ):
            with self.subTest(fields=fields):
                result = enrichment.search_candidates(fields)
                self.assertEqual(result["candidates"], [])
                self.assertTrue(result["issues"])
        self.get.assert_not_called()

    def test_invalid_isbn_uses_real_title_lookup_instead(self):
        self.fixture(search_result("/books/OL100M"), {**EDITION, "authors": []})
        result = enrichment.search_candidates({"isbn": "668800315", "title": EDITION["title"]})
        self.assertTrue(result["candidates"])
        self.assertIn("ISBN 无效", " ".join(result["issues"]))
        self.assertEqual(urlparse(self.get.call_args_list[0].args[0]).path, "/search.json")

    def test_user_urls_and_solr_operators_are_only_literal_search_text(self):
        self.fixture({"docs": []})
        title = 'http://127.0.0.1:8000/secret?x=1" OR *:* \\ x'
        enrichment.search_candidates(
            {"title": title, "source_url": "http://169.254.169.254/", "cover_url": "file:///secret"}
        )
        request = urlparse(self.get.call_args.args[0])
        self.assertEqual(request.netloc, "openlibrary.org")
        self.assertEqual(request.path, "/search.json")
        self.assertEqual(
            parse_qs(request.query)["q"],
            ['title:"http://127.0.0.1:8000/secret?x=1\\" OR *:* \\\\ x"'],
        )
        self.assertEqual(self.get.call_count, 1)

    def test_upstream_edition_and_author_identifiers_cannot_make_ssrf_requests(self):
        self.fixture(
            search_result("http://127.0.0.1/", "/books/../../secret", "/books/OL100M"),
            {**EDITION, "authors": [{"key": "https://169.254.169.254/"}]},
        )
        result = enrichment.search_candidates({"title": EDITION["title"]})
        self.assertEqual(self.get.call_count, 2)
        self.assertTrue(result["candidates"])
        self.assertIn("标识无效", " ".join(result["candidates"][0]["issues"]))

    def test_redirects_outside_exact_https_api_allowlist_are_rejected_before_access(self):
        for location in (
            "http://openlibrary.org/books/OL100M.json",
            "https://127.0.0.1/",
            "https://openlibrary.org.evil.test/books/OL100M.json",
            "https://openlibrary.org:443/books/OL100M.json",
            "https://user@openlibrary.org/books/OL100M.json",
            "//169.254.169.254/metadata",
            "/works/OL999W.json",
            "https://covers.openlibrary.org/b/id/123-L.jpg",
        ):
            with self.subTest(location=location):
                self.get.reset_mock()
                self.fixture(Response(status=302, headers={"Location": location}))
                result = enrichment.search_candidates({"isbn": ISBN})
                self.assertEqual(result["candidates"], [])
                self.assertIn("允许的公开书目接口", " ".join(result["issues"]))
                self.assertEqual(self.get.call_count, 1)

    def test_safe_isbn_redirect_and_redirect_limit(self):
        self.fixture(
            Response(status=302, headers={"Location": "/books/OL100M.json"}),
            {**EDITION, "authors": []},
        )
        self.assertTrue(enrichment.search_candidates({"isbn": ISBN})["candidates"])
        enrichment._CACHE.clear()
        self.get.reset_mock()
        self.fixture(*[Response(status=302, headers={"Location": "/books/OL100M.json"})] * 3)
        result = enrichment.search_candidates({"isbn": ISBN})
        self.assertEqual(self.get.call_count, 3)
        self.assertIn("跳转次数过多", " ".join(result["issues"]))

    def test_mismatched_record_key_is_not_used(self):
        self.fixture(search_result("/books/OL100M"), {**EDITION, "key": "/books/OL999M"})
        result = enrichment.search_candidates({"title": EDITION["title"]})
        self.assertEqual(result["candidates"], [])
        self.assertIn("标识不一致", " ".join(result["issues"]))

    def test_author_name_evidence_is_from_author_record_not_work(self):
        edition = {**EDITION, "authors": [{"key": "/authors/OL10A"}, {"key": "/authors/OL11A"}]}
        self.fixture(edition, AUTHOR, {"key": "/authors/OL11A", "name": "Second Author"})
        candidate = enrichment.search_candidates({"isbn": ISBN})["candidates"][0]
        self.assertEqual(candidate["fields"]["authors"], ["Visible Author", "Second Author"])
        claims = [e for e in candidate["evidence"] if e["field"] == "authors"]
        self.assertEqual(claims[1]["value"], ["Visible Author"])
        self.assertEqual(claims[2]["value"], ["Second Author"])
        self.assertEqual(claims[2]["source_url"], "https://openlibrary.org/authors/OL11A")

    def test_author_failure_preserves_other_claims_and_reports_missing_author(self):
        self.fixture(EDITION, Response(status=404))
        candidate = enrichment.search_candidates({"isbn": ISBN, "authors": ["Visible Author"]})[
            "candidates"
        ][0]
        self.assertNotIn("authors", candidate["fields"])
        self.assertIn("缺少可核对的版本作者", " ".join(candidate["issues"]))

    def test_timeout_and_transient_http_statuses_retry_once_then_stop(self):
        for failure in (
            requests.Timeout("secret request"),
            requests.ConnectionError("secret"),
            Response(status=429),
            Response(status=503),
        ):
            with self.subTest(failure=failure):
                self.get.reset_mock()
                self.fixture(failure, Response(status=503))
                result = enrichment.search_candidates({"isbn": ISBN})
                self.assertEqual(self.get.call_count, 2)
                self.assertEqual(result["candidates"], [])
                self.assertNotIn("secret", json.dumps(result))

    def test_retry_after_is_honored_within_bound_and_does_not_sleep_for_long_wait(self):
        self.fixture(Response(status=429, headers={"Retry-After": "2"}), {**EDITION, "authors": []})
        self.assertTrue(enrichment.search_candidates({"isbn": ISBN})["candidates"])
        self.sleep.assert_any_call(2)
        for retry in ("3600", "nan", "inf", "-1"):
            with self.subTest(retry=retry):
                enrichment._CACHE.clear()
                self.get.reset_mock()
                self.sleep.reset_mock()
                self.fixture(Response(status=429, headers={"Retry-After": retry}))
                result = enrichment.search_candidates({"isbn": ISBN})
                self.assertEqual(result["candidates"], [])
                self.assertEqual(self.get.call_count, 1)
                self.sleep.assert_not_called()

    def test_nonretryable_http_failures_and_invalid_json_are_safe_failures(self):
        for response in (
            Response(status=403),
            Response(content=b"not json secret"),
            Response(data=[]),
            Response(data={"error": "upstream secret"}),
            Response(data={"docs": {}}),
        ):
            with self.subTest(response=response):
                self.get.reset_mock()
                self.fixture(response)
                result = enrichment.search_candidates({"title": "Reader"})
                self.assertEqual(result["candidates"], [])
                self.assertEqual(self.get.call_count, 1)
                self.assertNotIn("secret", json.dumps(result))

    def test_oversized_stream_is_stopped_and_closed(self):
        response = Response(content=b"x" * (enrichment.MAX_RESPONSE_BYTES + 100))
        self.fixture(response)
        result = enrichment.search_candidates({"isbn": ISBN})
        self.assertEqual(result["candidates"], [])
        self.assertIn("大小限制", " ".join(result["issues"]))
        self.assertTrue(response.closed)

    def test_cached_data_is_independent_and_expired_entries_are_refetched(self):
        self.fixture({**EDITION, "authors": []})
        first = enrichment.search_candidates({"isbn": ISBN})
        first["candidates"][0]["fields"]["title"] = "manual edit"
        second = enrichment.search_candidates({"isbn": ISBN})
        self.assertEqual(second["candidates"][0]["fields"]["title"], EDITION["title"])
        self.assertEqual(self.get.call_count, 1)
        key = next(iter(enrichment._CACHE))
        _, data = enrichment._CACHE[key]
        enrichment._CACHE[key] = (0, data)
        self.fixture({**EDITION, "authors": []})
        enrichment.search_candidates({"isbn": ISBN})
        self.assertEqual(self.get.call_count, 2)

    def test_request_and_candidate_limits_preserve_successful_results(self):
        keys = [f"/books/OL{i}M" for i in range(10)]
        editions = [{**EDITION, "key": key, "authors": []} for key in keys[:4]]
        self.fixture(search_result(*keys), *editions)
        self.assertEqual(
            len(enrichment.search_candidates({"title": EDITION["title"]})["candidates"]), 4
        )
        self.assertEqual(self.get.call_count, 5)
        enrichment._CACHE.clear()
        self.get.reset_mock()
        self.fixture(search_result("/books/OL100M"), EDITION)
        with patch.object(enrichment, "MAX_REQUESTS", 2):
            result = enrichment.search_candidates({"title": EDITION["title"]})
        self.assertEqual(self.get.call_count, 2)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertIn("达到请求/时间限制", " ".join(result["candidates"][0]["issues"]))


class TransportBoundaryTests(SimpleTestCase):
    def test_deadline_stops_lookup_without_network(self):
        client = enrichment._PublicClient()
        client.deadline = 0
        try:
            with patch.object(client.session, "get") as get:
                with self.assertRaises(SourceError):
                    client.get_json("/search.json")
                get.assert_not_called()
        finally:
            client.close()

    def test_process_rate_limit_waits_one_second_between_requests(self):
        with (
            patch.object(enrichment, "_LAST_REQUEST", 100.0),
            patch.object(enrichment.time, "monotonic", return_value=100.25),
            patch.object(enrichment.time, "sleep") as sleep,
        ):
            enrichment._throttle(110)
            sleep.assert_called_once_with(0.75)

    def test_client_rejects_media_and_arbitrary_paths_before_network(self):
        client = enrichment._PublicClient()
        try:
            with patch.object(client.session, "get") as get:
                for path in (
                    "//127.0.0.1/",
                    "/books/../secret",
                    "/works/OL1W.json",
                    "/isbn/invalid.json",
                    "/b/id/123-L.jpg",
                ):
                    with self.subTest(path=path), self.assertRaises(SourceError):
                        client.get_json(path)
                get.assert_not_called()
        finally:
            client.close()
