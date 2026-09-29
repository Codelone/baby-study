import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.urls import reverse
from openpyxl import Workbook

from catalog.enrichment.collector import collect, make_entity
from catalog.enrichment.importer import import_records, normalize_isbn
from catalog.enrichment.report import build_report, markdown
from catalog.enrichment.sources import CachedClient, SourceError, parse_openlibrary, parse_xiaobien
from catalog.models import (
    Book,
    BookSeries,
    CatalogImportRecord,
    SeriesMembership,
    ShelfItem,
    SourceEvidence,
)

URL = "https://example.org/edition/123"


def entity(key="edition:123", kind="book", **values):
    fields = {
        "title" if kind == "book" else "name": "真实读物" if kind == "book" else key,
        **values,
    }
    return {
        "kind": kind,
        "identity_key": key,
        "edition_verified": True,
        "members": [],
        "fields": {
            field: {
                "value": value,
                "status": "confirmed",
                "source_url": URL,
                "collected_at": "2026-09-28T01:00:00+00:00",
            }
            for field, value in fields.items()
        },
    }


def row(*entities):
    return {
        "source_file": "test.xlsx",
        "sheet": "Sheet1",
        "row": 2,
        "raw": {"书本名称": "原名", "购买价格": 99, "备注": "私人备注"},
        "status": "ready",
        "issues": [],
        "candidates": [],
        "entities": list(entities),
    }


class ImportTests(TestCase):
    def test_member_identity_reuses_old_id_and_retains_search_alias_and_state(self):
        old = Book.objects.create(title="惊喜派对", source_file="test.xlsx", source_row=44)
        user = get_user_model().objects.create_user("original-parent")
        shelf = ShelfItem.objects.create(
            user=user, book=old, ownership_status="owned", reading_status="reading"
        )
        before = shelf.updated_at
        member = entity("xiaobien:book:1006863", title="All Change! Party", aliases=["惊喜派对"])
        member["existing_source_row"] = 44
        group = entity("all-change", "series", expected_count=1, completeness="complete")
        group["members"] = [member]
        record = row(group)
        for _ in range(2):
            self.assertEqual(import_records([record], apply=True)[0]["status"], "imported")
        self.assertEqual(Book.objects.get(identity_key=member["identity_key"]).pk, old.pk)
        shelf.refresh_from_db()
        self.assertEqual((shelf.reading_status, shelf.updated_at), ("reading", before))
        self.client.force_login(user)
        found = self.client.get(reverse("books"), {"q": "惊喜派对"}).json()
        self.assertEqual(found["books"][0]["id"], old.pk)
        report = build_report([record], [{"row": 2, "status": "imported"}])
        self.assertEqual(report["summary"]["rows"], 1)
        self.assertIn("All Change! Party", markdown(report))
        self.assertNotIn("私人备注", markdown(report))

    def test_membership_removal_is_not_undone_by_unrelated_book_save(self):
        first = BookSeries.objects.create(name="旧主系列")
        second = BookSeries.objects.create(name="新系列")
        book = Book.objects.create(title="保留单本", series=first)
        SeriesMembership.objects.create(book=book, series=second)
        book.groups.remove(first)
        book.description = "后台资料修正"
        book.save()
        self.assertEqual(book.series_name, second.name)
        self.assertEqual(list(book.groups.values_list("pk", flat=True)), [second.pk])

    def test_dry_run_idempotency_and_private_raw(self):
        record = row(entity(isbn="978-0-375-82778-5", authors=["Ole Risom"]))
        before = Book.objects.count()
        self.assertEqual(import_records([record])[0]["status"], "ready")
        self.assertEqual(Book.objects.count(), before)
        self.assertFalse(CatalogImportRecord.objects.filter(source_file="test.xlsx").exists())
        for _ in range(2):
            self.assertEqual(import_records([record], apply=True)[0]["status"], "imported")
        book = Book.objects.get(identity_key="edition:123")
        self.assertEqual(book.isbn, "9780375827785")
        self.assertEqual(SourceEvidence.objects.filter(book=book).count(), 3)
        self.assertEqual(
            CatalogImportRecord.objects.get(source_file="test.xlsx").raw["购买价格"], 99
        )
        self.assertFalse(ShelfItem.objects.exists())

    def test_wrong_isbn_unconfirmed_edition_and_attachment_are_rejected(self):
        wrong = entity(isbn="9780375827786")
        unknown = entity(pages=24)
        unknown["edition_verified"] = False
        candidate = entity()
        candidate["fields"]["title"]["status"] = "candidate"
        attachment = entity()
        attachment["reader_kind"] = "attachment"
        edition_lexile = entity(
            reading_attributes={
                "lexile": {"value": "AD560L", "status": "confirmed", "source_url": URL}
            }
        )
        edition_lexile["edition_verified"] = False
        for item in (wrong, unknown, candidate, attachment, edition_lexile):
            with self.subTest(item=item):
                self.assertEqual(import_records([row(item)], apply=True)[0]["status"], "rejected")
        self.assertFalse(Book.objects.filter(identity_key="edition:123").exists())

    def test_malformed_field_types_and_missing_source_host_are_rejected(self):
        for item in (entity(authors="Author"), entity(aliases=[123]), entity(pages=True)):
            self.assertEqual(import_records([row(item)], apply=True)[0]["status"], "rejected")
        missing_source = entity()
        missing_source["fields"]["title"]["source_url"] = "http://"
        self.assertEqual(import_records([row(missing_source)])[0]["status"], "rejected")

    def test_set_isbn_is_not_copied_to_children_or_used_as_reader(self):
        group = entity("set:1", "series", isbn="9780375827785")
        group["members"] = [entity("member:1", isbn="9780375827785")]
        outcome = import_records([row(group)], apply=True)[0]
        self.assertEqual(outcome["status"], "rejected")
        self.assertIn("套装 ISBN", outcome["reason"])
        self.assertFalse(BookSeries.objects.filter(identity_key="set:1").exists())

    def test_same_title_different_editions_are_distinct_and_shared_member_is_reused(self):
        original = entity("edition:original", language="english")
        translated = entity("edition:translated", language="chinese")
        import_records([row(original, translated)], apply=True)
        self.assertEqual(
            Book.objects.filter(
                identity_key__in=["edition:original", "edition:translated"]
            ).count(),
            2,
        )
        first = entity("set:first", "series", expected_count=1, completeness="complete")
        first["members"] = [original]
        second = entity("set:second", "series", expected_count=2, completeness="partial")
        second["members"] = [original]
        self.assertEqual(import_records([row(first, second)], apply=True)[0]["status"], "imported")
        book = Book.objects.get(identity_key="edition:original")
        self.assertEqual(book.groups.count(), 2)

    def test_manual_field_protection_and_incomplete_count_validation(self):
        import_records([row(entity(authors=["Source author"]))], apply=True)
        book = Book.objects.get(identity_key="edition:123")
        book.authors = ["Manually corrected"]
        book.manual_fields = ["authors"]
        book.save()
        import_records([row(entity(authors=["New source author"]))], apply=True)
        book.refresh_from_db()
        self.assertEqual(book.authors, ["Manually corrected"])
        group = entity("incomplete", "series", expected_count=2, completeness="complete")
        group["members"] = [entity()]
        self.assertEqual(import_records([row(group)], apply=True)[0]["status"], "rejected")

    def test_manual_memberships_and_archive_status_are_preserved(self):
        group = entity("manual-group", "series", expected_count=1, completeness="complete")
        group["members"] = [entity("manual-member")]
        import_records([row(group)], apply=True)
        saved = BookSeries.objects.get(identity_key="manual-group")
        saved.books.clear()
        saved.completeness = "partial"
        saved.manual_fields = ["memberships", "expected_count", "completeness"]
        saved.save()
        self.assertEqual(import_records([row(group)], apply=True)[0]["status"], "imported")
        self.assertEqual(saved.books.count(), 0)
        old = Book.objects.create(
            title="人工保留单本",
            source_file="test.xlsx",
            source_row=48,
            manual_fields=["catalog_status"],
        )
        group["archive_source_row"] = 48
        import_records([row(group)], apply=True)
        old.refresh_from_db()
        self.assertEqual(old.catalog_status, "active")

    def test_failure_rolls_back_entire_record_and_archive_does_not_propagate_status(self):
        first = entity("rollback:1")
        second = entity("rollback:2")
        original_save = Book.save

        def failing_save(book, *args, **kwargs):
            if book.identity_key == "rollback:2":
                raise ValueError("simulated failure")
            return original_save(book, *args, **kwargs)

        with patch.object(Book, "save", failing_save):
            self.assertEqual(
                import_records([row(first, second)], apply=True)[0]["status"], "rejected"
            )
        self.assertFalse(Book.objects.filter(identity_key__startswith="rollback:").exists())
        self.assertFalse(CatalogImportRecord.objects.filter(source_file="test.xlsx").exists())
        old = Book.objects.create(title="套装误建", source_file="test.xlsx", source_row=48)
        group = entity("set:archive", "series")
        group["archive_source_row"] = 48
        group["members"] = [entity("archive:member")]
        import_records([row(group)], apply=True)
        old.refresh_from_db()
        self.assertEqual(old.catalog_status, "legacy")
        self.assertFalse(ShelfItem.objects.exists())
        # An old record with any shelf reference stays intact, including removed records.
        old.catalog_status = "active"
        old.save()
        user = get_user_model().objects.create_user("old-owner")
        shelf = ShelfItem.objects.create(
            user=user, book=old, ownership_status="owned", reading_status="paused"
        )
        before = shelf.updated_at
        import_records([row(group)], apply=True)
        old.refresh_from_db()
        shelf.refresh_from_db()
        self.assertEqual(old.catalog_status, "active")
        self.assertEqual((shelf.reading_status, shelf.updated_at), ("paused", before))
        self.assertIn(
            "有书架引用", " ".join(CatalogImportRecord.objects.get(source_file="test.xlsx").issues)
        )

    def test_unknown_lexile_and_native_level_remain_native(self):
        attrs = {
            "native_level": {"value": "Ladybird Level 2", "status": "confirmed", "source_url": URL}
        }
        import_records([row(entity(reading_attributes=attrs))], apply=True)
        user = get_user_model().objects.create_user("detail-parent")
        self.client.force_login(user)
        book = Book.objects.get(identity_key="edition:123")
        detail = self.client.get(reverse("book-detail", args=[book.id])).json()
        self.assertIsNone(detail["lexile_value"])
        self.assertEqual(detail["reading_attributes"], attrs)
        self.assertTrue(detail["evidence"])
        self.assertNotIn("raw", detail)
        self.assertNotIn("购买价格", json.dumps(detail, ensure_ascii=False))


class SharedShelfTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("shared-parent")
        self.client.force_login(self.user)
        self.first = BookSeries.objects.create(
            name="一套", expected_count=2, completeness="partial"
        )
        self.second = BookSeries.objects.create(name="另一套")
        self.book = Book.objects.create(title="共同分册")
        SeriesMembership.objects.create(series=self.first, book=self.book, position=2)
        SeriesMembership.objects.create(series=self.second, book=self.book)

    def test_overlapping_add_preserves_shared_state_and_unique_total(self):
        self.client.post(reverse("shelf-series", args=[self.first.id]))
        item = ShelfItem.objects.get(user=self.user, book=self.book)
        item.reading_status = "familiar"
        item.save()
        before = item.updated_at
        result = self.client.post(reverse("shelf-series", args=[self.second.id])).json()
        self.assertEqual(result["added_count"], 0)
        item.refresh_from_db()
        self.assertEqual((item.reading_status, item.updated_at), ("familiar", before))
        shelf = self.client.get(reverse("shelf")).json()["items"]
        self.assertEqual(len(shelf), 1)
        self.assertEqual(shelf[0]["book"]["series_ids"], [self.first.id, self.second.id])
        self.client.put(
            reverse("shelf-book", args=[self.book.id]),
            json.dumps({"ownership_status": "not_owned", "reading_status": None}),
            content_type="application/json",
        )
        self.assertEqual(self.client.get(reverse("shelf")).json()["items"], [])

    def test_details_auth_missing_and_ordered_partial_members(self):
        other = Book.objects.create(title="先读的分册")
        SeriesMembership.objects.create(series=self.first, book=other, position=1)
        detail = self.client.get(reverse("series-detail", args=[self.first.id])).json()
        self.assertEqual([b["id"] for b in detail["books"]], [other.id, self.book.id])
        self.assertEqual(detail["expected_count"], 2)
        self.assertEqual(detail["completeness"], "partial")
        self.assertEqual(self.client.get(reverse("book-detail", args=[9999999])).status_code, 404)
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse("series-detail", args=[self.first.id])).status_code, 403
        )


class SourceTests(SimpleTestCase):
    def test_magazines_aggregate_audio_attachments_and_legacy_mapping(self):
        source = {
            "kind": "series",
            "identity_key": "magazines",
            "url": URL,
            "adapter": "xiaobien",
            "edition_verified": True,
            "members_verified": True,
            "member_pattern": r"2022年[1-9]月",
            "exclude_members": ["资源卡"],
            "member_positions": {"jan": 1, "sep": 9},
            "existing_member_mappings": {"jan": 44},
            "raw_row": 44,
        }
        parsed = {
            "title": "合集",
            "source_id": "aggregate",
            "members": [
                {"title": "合集", "source_id": "aggregate"},
                {"title": "High Five 2022年9月", "source_id": "sep"},
                {"title": "High Five 2022年1月", "source_id": "jan"},
                {"title": "High Five 2023年1月", "source_id": "other"},
                {"title": "2022年1月资源卡", "source_id": "card"},
            ],
        }
        result = make_entity(source, parsed, "2026-09-28T01:00:00Z", {"书本名称": "旧名"})
        self.assertEqual([m["position"] for m in result["members"]], [9, 1])
        january = result["members"][1]
        self.assertEqual(january["existing_source_row"], 44)
        self.assertEqual(january["fields"]["aliases"]["value"], ["旧名"])
        source["expected_source_id"] = "previous-edition"
        with self.assertRaisesRegex(ValueError, "版本标识变化"):
            make_entity(source, parsed, "2026-09-28T01:00:00Z", {"书本名称": "旧名"})

    def test_isbn_checks_and_work_search_is_candidate_only(self):
        self.assertEqual(normalize_isbn("0375827781"), "9780375827785")
        with self.assertRaises(ValueError):
            normalize_isbn("668800315")
        data = parse_openlibrary('{"docs":[{"key":"/works/OL1W","title":"Book"}]}', URL)
        self.assertFalse(data["edition_verified"])
        edition = parse_openlibrary(
            '{"key":"/books/OL1M","title":"Reader","isbn_13":["9780375827785"]}', URL
        )
        self.assertFalse(edition["edition_verified"])
        self.assertEqual(edition["isbn"], "9780375827785")
        self.assertEqual(
            edition["cover_url"],
            "https://covers.openlibrary.org/b/olid/OL1M-L.jpg?default=false",
        )

    def test_offline_nuxt_fixture_and_changed_structure(self):
        data = [
            ["ShallowReactive", 1],
            {"data": 2},
            {"test": 3},
            {"book_name": 4, "book_id": 5, "download_url": 6},
            "Reader",
            123,
            [],
        ]
        html = (
            '<script id="__NUXT_DATA__" type="application/json">' + json.dumps(data) + "</script>"
        )
        result = parse_xiaobien(html, URL)
        self.assertEqual(result["title"], "Reader")
        self.assertEqual(result["source_id"], "123")
        with self.assertRaises(SourceError):
            parse_xiaobien("<h1>Changed</h1>", URL)

    def test_timeout_retry_429_retry_after_cache_and_refresh(self):
        with (
            tempfile.TemporaryDirectory() as cache,
            patch("catalog.enrichment.sources.time.sleep") as sleep,
        ):
            client = CachedClient(cache, interval=0, timeout=3)
            failure = Mock(status_code=429, headers={"Retry-After": "5"})
            failure.raise_for_status.side_effect = requests.HTTPError(response=failure)
            ok = Mock(status_code=200, content=b"book", text="book", encoding="utf-8", url=URL)
            client.session.get = Mock(side_effect=[requests.Timeout(), failure, ok])
            self.assertEqual(client.fetch(URL)["text"], "book")
            self.assertEqual(client.session.get.call_count, 3)
            sleep.assert_any_call(5)
            client.fetch(URL)
            self.assertEqual(client.session.get.call_count, 3)
            client.refresh = True
            client.session.get = Mock(return_value=ok)
            client.fetch(URL)
            client.session.get.assert_called_once()
            with self.assertRaises(SourceError):
                client.fetch("https://covers.openlibrary.org/b/id/1-L.jpg")

    def test_checkpoint_resume_and_mapping_change(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workbook = Workbook()
            workbook.active.append(["书本名称"])
            workbook.active.append(["Test"])
            workbook.save(base / "input.xlsx")
            mapping = {"records": {"2": {"queries": ["Test"], "issues": ["Need edition"]}}}
            (base / "mapping.json").write_text(json.dumps(mapping))
            with patch.object(
                CachedClient,
                "fetch",
                return_value={
                    "text": '{"list":[],"total":0}',
                    "collected_at": "2026-09-28T01:00:00+00:00",
                },
            ) as fetch:
                first = collect(
                    base / "input.xlsx", base / "mapping.json", base / "out.json", base / "cache"
                )
                second = collect(
                    base / "input.xlsx", base / "mapping.json", base / "out.json", base / "cache"
                )
                self.assertEqual(first, second)
                self.assertEqual(fetch.call_count, 1)
                mapping["records"]["2"]["issues"] = ["Updated review"]
                (base / "mapping.json").write_text(json.dumps(mapping))
                collect(
                    base / "input.xlsx", base / "mapping.json", base / "out.json", base / "cache"
                )
                self.assertEqual(fetch.call_count, 2)
                workbook.active.append(["Next row"])
                workbook.save(base / "input.xlsx")
                mapping["records"]["2"]["sources"] = [{"url": URL, "adapter": "xiaobien"}]
                (base / "mapping.json").write_text(json.dumps(mapping))
                fetch.side_effect = lambda url: {
                    "text": (
                        '<script id="__NUXT_DATA__">[{"data":99}]</script>'
                        if url == URL
                        else '{"list":[],"total":0}'
                    ),
                    "collected_at": "2026-09-28T01:00:00+00:00",
                }
                results = collect(
                    base / "input.xlsx", base / "mapping.json", base / "out.json", base / "cache"
                )
                self.assertEqual(len(results), 2)
                self.assertEqual(results[0]["candidates"][-1]["status"], "failed")
                self.assertEqual(results[1]["row"], 3)


class MetadataMigrationTests(TransactionTestCase):
    def test_membership_and_import_migration_preserve_original_ids_and_shelf(self):
        old_target = [("catalog", "0003_book_series")]
        latest = [("catalog", "0006_evidence_uniqueness")]
        executor = MigrationExecutor(connection)
        executor.migrate(old_target)
        try:
            old = executor.loader.project_state(old_target).apps
            BookOld = old.get_model("catalog", "Book")
            ShelfOld = old.get_model("catalog", "ShelfItem")
            fields = (
                "id",
                "title",
                "language",
                "series_id",
                "series_description",
                "source_file",
                "source_row",
                "created_at",
                "updated_at",
            )
            before = list(BookOld.objects.order_by("id").values_list(*fields))
            before_shelf = list(ShelfOld.objects.values())
            MigrationExecutor(connection).migrate(latest)
            self.assertEqual(list(Book.objects.order_by("id").values_list(*fields)), before)
            self.assertEqual(list(ShelfItem.objects.values()), before_shelf)
            for book in Book.objects.exclude(series_id=None):
                self.assertTrue(
                    SeriesMembership.objects.filter(book=book, series_id=book.series_id).exists()
                )
            self.assertEqual(CatalogImportRecord.objects.count(), 19)
        finally:
            MigrationExecutor(connection).migrate(latest)
