import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from catalog.enrichment.importer import import_records
from catalog.intake_worker import finish
from catalog.models import (
    Book,
    BookIntake,
    BookSeries,
    CatalogImportRecord,
    SeriesMembership,
    ShelfItem,
    SourceEvidence,
)
from catalog.series_services import add_members, remove_member, reorder_members, save_series
from catalog.services import CatalogConflict, create_book
from catalog.test_enrichment import entity, row
from catalog.test_intakes import IntakeTestHelpers, picture


class SeriesManagementTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(
            "series-manager", password="test", is_staff=True
        )
        self.parent = get_user_model().objects.create_user("series-parent")
        self.series = BookSeries.objects.create(name="维护系列")
        self.other = BookSeries.objects.create(name="另一系列")
        self.first = Book.objects.create(title="成员一")
        self.second = Book.objects.create(title="成员二")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_create_empty_series_search_by_alias_and_edit_manual_evidence(self):
        response = self.client.post(
            "/baby/study/series", {"name": "新系列第一阶", "aliases": ["新别名"]}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        pk = response.data["id"]
        response = self.client.get("/baby/study/series", {"q": "新别名"})
        self.assertEqual([s["id"] for s in response.data["series"]], [pk])
        response = self.client.patch(
            f"/baby/study/series/{pk}",
            {"description": "人工说明", "expected_count": None},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        series = BookSeries.objects.get(pk=pk)
        self.assertTrue(
            {"name", "aliases", "description", "expected_count"} <= set(series.manual_fields)
        )
        self.assertTrue(
            SourceEvidence.objects.filter(
                series=series,
                field="description",
                confirmed_by=self.admin,
                origin_kind="manual",
                value="人工说明",
            ).exists()
        )

    def test_create_series_with_current_book_is_atomic(self):
        response = self.client.post(
            "/baby/study/series", {"name": "单本新系列", "book_ids": [self.first.id]}, format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["books"][0]["id"], self.first.id)
        response = self.client.post(
            "/baby/study/series", {"name": "不能留空系列", "book_ids": [999999]}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(BookSeries.objects.filter(name="不能留空系列").exists())

    def test_names_are_required_and_normalized_duplicates_are_rejected(self):
        for fields in (
            {},
            {"name": "  "},
            {"name": self.series.name},
            {"name": "  维护系列  "},
            {"name": "abc", "unexpected": True},
        ):
            response = self.client.post("/baby/study/series", fields, format="json")
            self.assertEqual(response.status_code, 400)
        save_series(self.admin, {"name": "Version One"})
        with self.assertRaises(ValidationError):
            save_series(self.admin, {"name": "ｖｅｒｓｉｏｎ one"})

    def test_series_isbn_cannot_be_a_single_book_isbn(self):
        self.first.isbn = "9780375827785"
        self.first.save()
        with self.assertRaises(ValidationError):
            save_series(self.admin, {"name": "ISBN类型错误", "isbn": "978-0-375-82778-5"})
        response = self.client.post(
            "/baby/study/series", {"name": "ISBN格式错误", "isbn": "123"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_duplicate_add_multiple_series_and_shelf_state_preserved(self):
        item = ShelfItem.objects.create(
            user=self.parent, book=self.first, ownership_status="owned", reading_status="familiar"
        )
        add_members(self.admin, self.series.id, [self.first.id, self.second.id])
        add_members(self.admin, self.series.id, [self.first.id])
        add_members(self.admin, self.other.id, [self.first.id])
        self.assertEqual(self.first.groups.count(), 2)
        self.assertEqual(self.series.books.count(), 2)
        remove_member(self.admin, self.series.id, self.first.id)
        remove_member(self.admin, self.series.id, self.first.id)
        item.refresh_from_db()
        self.assertEqual(item.reading_status, "familiar")
        self.assertEqual(list(self.first.groups.all()), [self.other])
        self.assertTrue(Book.objects.filter(pk=self.first.id).exists())

    def test_removal_clears_old_primary_and_protects_both_sides(self):
        self.first.series = self.series
        self.first.save()
        remove_member(self.admin, self.series.id, self.first.id)
        self.first.refresh_from_db()
        self.series.refresh_from_db()
        self.assertIsNone(self.first.series_id)
        self.first.save()
        self.assertFalse(self.first.groups.exists())
        self.assertIn("memberships", self.first.manual_fields)
        self.assertTrue(
            {"memberships", "expected_count", "completeness"} <= set(self.series.manual_fields)
        )

    def test_add_validation_is_atomic_and_only_active_books_allowed(self):
        self.second.catalog_status = "archived"
        self.second.save()
        for ids in (
            [self.first.id, self.second.id],
            [self.first.id, 999999],
            [True],
            [self.first.id, self.first.id],
            ["1"],
            None,
        ):
            response = self.client.post(
                f"/baby/study/series/{self.series.id}/members", {"book_ids": ids}, format="json"
            )
            self.assertEqual(response.status_code, 400)
            self.assertFalse(self.series.books.exists())

    def test_member_isbn_cannot_equal_set_isbn(self):
        self.series.isbn = "9780375827785"
        self.series.save()
        self.first.isbn = "0375827781"
        self.first.save()
        with self.assertRaises(ValidationError):
            add_members(self.admin, self.series.id, [self.second.id, self.first.id])
        self.assertFalse(self.series.books.exists())

    def test_reorder_all_members_and_reject_stale_set(self):
        add_members(self.admin, self.series.id, [self.first.id, self.second.id])
        reorder_members(self.admin, self.series.id, [self.second.id, self.first.id])
        members = self.client.get(f"/baby/study/series/{self.series.id}/members").data["members"]
        self.assertEqual([m["book"]["id"] for m in members], [self.second.id, self.first.id])
        with self.assertRaises(CatalogConflict):
            reorder_members(self.admin, self.series.id, [self.first.id])
        self.assertEqual(list(self.series.memberships.values_list("position", flat=True)), [1, 2])

    def test_archived_members_visible_to_manager_and_can_be_removed(self):
        add_members(self.admin, self.series.id, [self.first.id])
        self.first.catalog_status = "archived"
        self.first.save()
        self.assertEqual(self.client.get(f"/baby/study/series/{self.series.id}").data["books"], [])
        members = self.client.get(f"/baby/study/series/{self.series.id}/members").data["members"]
        self.assertEqual(members[0]["book"]["catalog_status"], "archived")
        self.assertEqual(
            self.client.delete(
                f"/baby/study/series/{self.series.id}/members/{self.first.id}"
            ).status_code,
            204,
        )

    def test_count_conflicts_allowed_but_complete_requires_manual_verification(self):
        self.series.expected_count = 1
        self.series.save()
        add_members(self.admin, self.series.id, [self.first.id])
        self.series.refresh_from_db()
        self.assertEqual(self.series.completeness, "unknown")
        save_series(self.admin, {"completeness": "complete"}, self.series.id)
        reorder_members(self.admin, self.series.id, [self.first.id])
        self.series.refresh_from_db()
        self.assertEqual(self.series.completeness, "complete")
        add_members(self.admin, self.series.id, [self.second.id])
        self.series.refresh_from_db()
        self.assertEqual((self.series.expected_count, self.series.completeness), (1, "partial"))
        with self.assertRaises(ValidationError):
            save_series(self.admin, {"completeness": "complete"}, self.series.id)
        remove_member(self.admin, self.series.id, self.second.id)
        self.series.refresh_from_db()
        self.assertEqual(self.series.completeness, "partial")
        save_series(self.admin, {"completeness": "complete"}, self.series.id)
        save_series(self.admin, {"expected_count": None}, self.series.id)
        self.series.refresh_from_db()
        self.assertEqual(self.series.completeness, "partial")

    def test_manual_new_and_reused_books_associate_atomically_and_idempotently(self):
        data = {
            "fields": {"title": "录入成员", "isbn": "9780375827785"},
            "series_ids": [self.series.id],
            "idempotency_key": "one",
        }
        response = self.client.post("/baby/study/books", data, format="json")
        self.assertEqual(response.status_code, 201)
        pk = response.data["book"]["id"]
        self.assertEqual(response.data["book"]["series_ids"], [self.series.id])
        response = self.client.post("/baby/study/books", data, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["book"]["id"], pk)
        self.assertEqual(SeriesMembership.objects.filter(book_id=pk).count(), 1)
        data["series_ids"] = [self.other.id]
        self.assertEqual(
            self.client.post("/baby/study/books", data, format="json").status_code, 409
        )
        data["idempotency_key"] = "two"
        response = self.client.post("/baby/study/books", data, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data["book"]["series_ids"]), {self.series.id, self.other.id})
        book = Book.objects.get(pk=pk)
        self.assertEqual(book.publisher, "")

    def test_invalid_relation_rolls_back_new_book_provenance_and_shelf(self):
        before = Book.objects.count()
        response = self.client.post(
            "/baby/study/books",
            {
                "fields": {"title": "应回滚"},
                "series_ids": [999999],
                "add_to_shelf": True,
                "idempotency_key": "invalid",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Book.objects.count(), before)
        self.assertFalse(
            CatalogImportRecord.objects.filter(
                source_file=f"catalog-management:{self.admin.id}"
            ).exists()
        )
        with patch(
            "catalog.series_services.associate_book", side_effect=ValidationError("fixture")
        ):
            with self.assertRaises(ValidationError):
                create_book(
                    self.admin,
                    {"title": "事务回滚"},
                    series_ids=[self.series.id],
                    idempotency_key="rollback",
                )
        self.assertFalse(Book.objects.filter(title="事务回滚").exists())

    def test_search_single_isbn_with_separators(self):
        self.first.isbn = "9780375827785"
        self.first.save()
        response = self.client.get("/baby/study/books", {"q": "978-0-375-82778-5"})
        self.assertEqual([book["id"] for book in response.data["books"]], [self.first.id])

    def test_permissions_and_csrf_on_every_mutation(self):
        routes = [
            ("/baby/study/series", "post", {"name": "权限测试"}),
            (f"/baby/study/series/{self.series.id}", "patch", {"description": "no"}),
            (f"/baby/study/series/{self.series.id}/members", "post", {"book_ids": [self.first.id]}),
            (f"/baby/study/series/{self.series.id}/members/{self.first.id}", "delete", {}),
            (f"/baby/study/series/{self.series.id}/members/order", "put", {"book_ids": []}),
        ]
        for user in (None, self.parent):
            self.client.force_authenticate(user)
            for url, method, data in routes:
                self.assertEqual(
                    getattr(self.client, method)(url, data, format="json").status_code, 403
                )
        self.client.force_authenticate(self.parent)
        self.assertEqual(
            self.client.get(f"/baby/study/series/{self.series.id}/members").status_code, 403
        )
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.admin)
        for url, method, data in routes:
            self.assertEqual(
                getattr(csrf, method)(url, data, content_type="application/json").status_code, 403
            )
        csrf.logout()
        csrf.get("/admin/login/")
        csrf.force_login(self.admin)
        token = csrf.cookies["csrftoken"].value
        response = csrf.post(
            "/baby/study/series",
            {"name": "有CSRF"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 201)

    def test_recollection_preserves_service_edits_and_removed_member(self):
        group = entity("series-service-import", "series", expected_count=1, completeness="complete")
        group["members"] = [entity("series-service-book")]
        self.assertEqual(import_records([row(group)], apply=True)[0]["status"], "imported")
        series = BookSeries.objects.get(identity_key="series-service-import")
        book = series.books.get()
        save_series(self.admin, {"name": "人工改名", "description": "人工说明"}, series.id)
        remove_member(self.admin, series.id, book.id)
        self.assertEqual(import_records([row(group)], apply=True)[0]["status"], "imported")
        series.refresh_from_db()
        self.assertEqual(
            (series.name, series.description, series.completeness),
            ("人工改名", "人工说明", "partial"),
        )
        self.assertFalse(series.books.exists())
        self.assertTrue(Book.objects.filter(pk=book.id).exists())


class IntakeSeriesTests(IntakeTestHelpers, TestCase):
    def setUp(self):
        super().setUp()
        self.series = BookSeries.objects.create(name="照片目标系列")
        self.other_series = BookSeries.objects.create(name="照片另一系列")

    def test_upload_persists_target_and_worker_initializes_groups(self):
        response = self.client.post(
            "/baby/study/book-intakes",
            {
                "target_series_id": str(self.series.id),
                "files": [SimpleUploadedFile("series.jpg", picture(), content_type="image/jpeg")],
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        intake = BookIntake.objects.get(pk=response.data["intake"]["id"])
        self.assertEqual(intake.target_series_id, self.series.id)
        intake.status = "processing"
        intake.save()
        photo = intake.photos.get()
        photo.status = "succeeded"
        photo.extraction = {"fields": {"title": "照片系列分册"}}
        photo.save()
        finish(intake.id, intake.generation)
        detail = self.client.get(self.url(intake)).data["intake"]
        self.assertEqual(detail["target_series"], {"id": self.series.id, "name": self.series.name})
        self.assertEqual(detail["groups"][0]["series_ids"], [self.series.id])
        self.assertFalse(Book.objects.filter(title="照片系列分册").exists())

    def test_group_override_preserved_and_confirmation_can_reuse_existing(self):
        intake = self.draft(two=True)
        intake.target_series = self.series
        intake.save()
        groups = copy.deepcopy(intake.groups)
        groups[0]["series_ids"] = [self.series.id, self.other_series.id]
        groups[1]["series_ids"] = []
        existing = Book.objects.create(**groups[0]["fields"])
        groups[0]["existing_book_id"] = existing.id
        response = self.client.patch(
            self.url(intake, "groups"),
            {"revision": 1, "groups": groups, "unassigned_photo_ids": []},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        payload = {"revision": response.data["intake"]["revision"], "group_ids": ["one", "two"]}
        response = self.client.post(self.url(intake, "confirm"), payload, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(existing.groups.values_list("id", flat=True)),
            {self.series.id, self.other_series.id},
        )
        self.assertFalse(Book.objects.get(title="Fixture Book Two").groups.exists())
        before = SeriesMembership.objects.count()
        self.assertEqual(
            self.client.post(self.url(intake, "confirm"), payload, format="json").status_code, 200
        )
        self.assertEqual(SeriesMembership.objects.count(), before)
        self.assertEqual(
            self.client.post(
                self.url(intake, "confirm"), {**payload, "add_to_shelf": True}, format="json"
            ).status_code,
            409,
        )
        intake.refresh_from_db()
        self.assertEqual(
            self.client.patch(
                self.url(intake, "groups"),
                {"revision": intake.revision, "groups": groups, "unassigned_photo_ids": []},
                format="json",
            ).status_code,
            409,
        )

    def test_legacy_task_defaults_to_no_series_and_bad_ids_rejected(self):
        intake = self.draft()
        groups = copy.deepcopy(intake.groups)
        for ids in ([999999], [True], "1", [self.series.id, self.series.id]):
            groups[0]["series_ids"] = ids
            response = self.client.patch(
                self.url(intake, "groups"),
                {"revision": 1, "groups": groups, "unassigned_photo_ids": []},
                format="json",
            )
            self.assertIn(response.status_code, (400, 404))
        del groups[0]["series_ids"]
        response = self.client.patch(
            self.url(intake, "groups"),
            {"revision": 1, "groups": groups, "unassigned_photo_ids": []},
            format="json",
        )
        self.assertEqual(response.data["intake"]["groups"][0]["series_ids"], [])

    def test_confirmation_rolls_back_all_groups_and_relationships(self):
        intake = self.draft(two=True)
        intake.groups[0]["series_ids"] = [self.series.id]
        intake.groups[1]["series_ids"] = [999999]
        intake.save()
        response = self.client.post(
            self.url(intake, "confirm"),
            {"revision": 1, "group_ids": ["one", "two"], "add_to_shelf": True},
            format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Book.objects.filter(title__startswith="Fixture Book").exists())
        self.assertFalse(self.series.books.exists())
        self.assertFalse(ShelfItem.objects.filter(user=self.admin).exists())
        intake.refresh_from_db()
        self.assertEqual(intake.status, "awaiting_confirmation")

    def test_target_remains_after_manual_override_and_retry_finish(self):
        intake = self.draft()
        intake.target_series = self.series
        intake.groups[0]["series_ids"] = [self.other_series.id]
        intake.has_manual_edits = True
        intake.status = "processing"
        intake.save()
        finish(intake.id, intake.generation)
        intake.refresh_from_db()
        self.assertEqual(intake.groups[0]["series_ids"], [self.other_series.id])
        self.assertEqual(intake.target_series_id, self.series.id)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url(intake)).status_code, 404)

    def test_pre_upgrade_worker_group_uses_persisted_target_without_draft_edit(self):
        intake = self.draft()
        intake.target_series = self.series
        intake.save()
        response = self.client.post(
            self.url(intake, "confirm"), {"revision": 1, "group_ids": ["one"]}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        book = Book.objects.get(title="Fixture Book One")
        self.assertEqual(list(book.groups.values_list("id", flat=True)), [self.series.id])


class ConcurrentSeriesManagementTests(TransactionTestCase):
    def test_two_managers_adding_overlapping_members_preserve_uniqueness_and_order(self):
        series = BookSeries.objects.create(name="并发维护系列")
        books = [Book.objects.create(title=f"并发维护成员{i}") for i in range(3)]
        users = [
            get_user_model().objects.create_user(f"series-concurrent-{i}", is_staff=True)
            for i in range(2)
        ]
        barrier = Barrier(2)

        def add(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                add_members(users[index], series.id, [books[index].id, books[2].id])
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(add, range(2)))
        self.assertEqual(series.books.count(), 3)
        self.assertEqual(set(series.memberships.values_list("position", flat=True)), {1, 2, 3})
        self.assertEqual(SeriesMembership.objects.filter(series=series, book=books[2]).count(), 1)
