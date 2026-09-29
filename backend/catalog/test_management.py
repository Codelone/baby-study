import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import close_old_connections, connection, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import include, path, reverse
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from catalog import views
from catalog.models import (
    Book,
    BookIntake,
    BookSeries,
    CatalogImportRecord,
    IntakePhoto,
    SeriesMembership,
    ShelfItem,
    SourceEvidence,
)
from catalog.serializers import BookDetailSerializer, BookSeriesSerializer
from catalog.services import CatalogConflict, archive_book, create_book

ISBN = "9780375827785"
OTHER_ISBN = "9780060256654"
FIELDS = {"title": "Management Reader", "language": "english", "isbn": ISBN}

# The integration owner registers production URLs. This test-only URL allows
# capabilities/session authentication checks before that route is integrated.
urlpatterns = [
    path(
        "baby/study/catalog-capabilities",
        views.catalog_capabilities,
        name="management-capabilities",
    ),
    path("", include("config.urls")),
]


class ManagementServiceTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="management-staff", is_staff=True
        )
        self.parent = get_user_model().objects.create_user(username="management-parent")

    def test_services_require_authenticated_administrator(self):
        book = Book.objects.create(title="Protected reader")
        for user in (self.parent, AnonymousUser()):
            with self.subTest(user=user):
                with self.assertRaises(PermissionDenied):
                    create_book(user, FIELDS)
                with self.assertRaises(PermissionDenied):
                    archive_book(user, book.pk)
        book.refresh_from_db()
        self.assertEqual(book.catalog_status, "active")

    def test_superuser_without_staff_flag_can_manage(self):
        self.staff.is_staff = False
        self.staff.is_superuser = True
        self.staff.save()
        book, created = create_book(self.staff, FIELDS)
        self.assertTrue(created)
        self.assertEqual(archive_book(self.staff, book.pk).catalog_status, "archived")

    def test_manual_create_records_explicit_fields_and_confirmation(self):
        fields = {**FIELDS, "authors": [" Visible Author "], "pages": None, "publisher": ""}
        book, created = create_book(self.staff, fields)
        self.assertTrue(created)
        self.assertEqual(book.authors, ["Visible Author"])
        self.assertEqual(set(book.manual_fields), set(fields))
        self.assertEqual(set(book.evidence.values_list("field", flat=True)), set(fields))
        self.assertFalse(
            book.evidence.exclude(origin_kind="manual", confirmed_by=self.staff).exists()
        )
        self.assertFalse(book.evidence.exclude(source_url="", model_name="").exists())
        self.assertEqual(book.reading_attributes, {})
        self.assertFalse(ShelfItem.objects.filter(book=book).exists())

    def test_unspecified_language_stays_unknown_without_invented_evidence(self):
        book, _ = create_book(self.staff, {"title": "Reader without language"})
        self.assertEqual(book.language, "unknown")
        self.assertEqual(book.manual_fields, ["title"])
        self.assertEqual(list(book.evidence.values_list("field", flat=True)), ["title"])

    def test_invalid_fields_are_rejected_without_creating_books(self):
        count = Book.objects.count()
        for change in (
            {"title": ""},
            {"title": 12},
            {"isbn": "9780375827786"},
            {"isbn": "978０３７５８２７７８５"},
            {"authors": "Author"},
            {"authors": [12]},
            {"pages": 0},
            {"pages": True},
            {"pages": "12"},
            {"pages": 2147483648},
            {"language": "French"},
            {"cover_url": "file:///private/photo.jpg"},
            {"reading_attributes": {"lexile": {"value": 500, "status": "confirmed"}}},
            {"catalog_status": "archived"},
            {"series": 1},
        ):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                create_book(self.staff, {**FIELDS, **change})
        for fields in ([], None, "Reader"):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                create_book(self.staff, fields)
        self.assertEqual(Book.objects.count(), count)

    def test_isbn10_and_formatted_isbn13_reuse_one_book(self):
        book, _ = create_book(self.staff, {**FIELDS, "isbn": "0-375-82778-1"})
        duplicate, created = create_book(self.staff, {**FIELDS, "isbn": "978-0-375-82778-5"})
        self.assertEqual(book.isbn, ISBN)
        self.assertEqual(duplicate.pk, book.pk)
        self.assertFalse(created)
        self.assertEqual(Book.objects.filter(isbn=ISBN).count(), 1)

    def test_legacy_isbn10_is_compared_without_overwriting(self):
        book = Book.objects.create(title=FIELDS["title"], isbn="0-375-82778-1", language="english")
        duplicate, created = create_book(self.staff, FIELDS)
        self.assertFalse(created)
        self.assertEqual(duplicate.pk, book.pk)
        self.assertEqual(duplicate.isbn, "0-375-82778-1")

    def test_same_isbn_with_conflicting_identity_is_blocked(self):
        original, _ = create_book(self.staff, {**FIELDS, "edition": "First edition", "pages": 32})
        before = Book.objects.filter(pk=original.pk).values().get()
        for change in (
            {"title": "Different work"},
            {"edition": "Second edition"},
            {"language": "chinese"},
            {"pages": 64},
        ):
            with self.subTest(change=change), self.assertRaises(CatalogConflict):
                create_book(self.staff, {**FIELDS, **change})
        self.assertEqual(Book.objects.filter(pk=original.pk).values().get(), before)

    def test_isbn_reuse_preserves_all_existing_materials_and_manual_locks(self):
        original, _ = create_book(
            self.staff, {**FIELDS, "authors": ["Original author"], "description": "Original"}
        )
        before_book = Book.objects.filter(pk=original.pk).values().get()
        before_evidence = list(original.evidence.values())
        duplicate, created = create_book(
            self.staff, {**FIELDS, "authors": ["Replacement"], "description": "Replacement"}
        )
        self.assertEqual(duplicate.pk, original.pk)
        self.assertFalse(created)
        self.assertEqual(Book.objects.filter(pk=original.pk).values().get(), before_book)
        self.assertEqual(list(original.evidence.values()), before_evidence)

    def test_same_name_different_isbn_or_editions_remain_separate(self):
        first, _ = create_book(self.staff, FIELDS)
        second, _ = create_book(self.staff, {**FIELDS, "isbn": OTHER_ISBN})
        third, _ = create_book(
            self.staff, {"title": FIELDS["title"], "publisher": "Publisher", "edition": "First"}
        )
        fourth, _ = create_book(
            self.staff, {"title": FIELDS["title"], "publisher": "Publisher", "edition": "Second"}
        )
        self.assertEqual(len({first.pk, second.pk, third.pk, fourth.pk}), 4)

    def test_no_isbn_reuses_only_specific_edition_identity(self):
        fields = {
            "title": "Edition identified reader",
            "publisher": "Publisher",
            "edition": "Third edition",
        }
        book, _ = create_book(self.staff, fields)
        duplicate, created = create_book(self.staff, {**fields, "description": "Extra information"})
        self.assertEqual(duplicate.pk, book.pk)
        self.assertFalse(created)
        self.assertEqual(duplicate.description, "")
        unnamed_version = {"title": "Title is not edition identity"}
        first, _ = create_book(self.staff, unnamed_version)
        second, _ = create_book(self.staff, unnamed_version)
        self.assertNotEqual(first.pk, second.pk)

    def test_explicit_existing_book_reuses_without_filling_missing_fields(self):
        book = Book.objects.create(title=FIELDS["title"], language="unknown")
        duplicate, created = create_book(
            self.staff, {**FIELDS, "publisher": "New claim"}, existing_book_id=book.pk
        )
        self.assertEqual(duplicate.pk, book.pk)
        self.assertFalse(created)
        book.refresh_from_db()
        self.assertEqual(book.isbn, "")
        self.assertEqual(book.publisher, "")
        self.assertEqual(book.language, "unknown")
        self.assertEqual(book.manual_fields, [])

    def test_specific_edition_identity_normalizes_case_and_whitespace(self):
        first, _ = create_book(
            self.staff, {"title": "Specific Reader", "publisher": "Publisher", "edition": "First"}
        )
        second, created = create_book(
            self.staff,
            {"title": "specific   reader", "publisher": "PUBLISHER", "edition": "FIRST"},
        )
        self.assertFalse(created)
        self.assertEqual(second.pk, first.pk)

    def test_explicit_selection_validates_identity_and_missing_id(self):
        book, _ = create_book(self.staff, FIELDS)
        with self.assertRaises(CatalogConflict):
            create_book(self.staff, {**FIELDS, "isbn": OTHER_ISBN}, existing_book_id=book.pk)
        with self.assertRaises(NotFound):
            create_book(self.staff, FIELDS, existing_book_id=999999)
        for invalid_id in (True, "1", -1):
            with self.subTest(invalid_id=invalid_id), self.assertRaises(ValidationError):
                create_book(self.staff, FIELDS, existing_book_id=invalid_id)

    def test_ambiguous_isbn_requires_explicit_compatible_record(self):
        first = Book.objects.create(**FIELDS)
        Book.objects.create(**FIELDS)
        with self.assertRaises(CatalogConflict):
            create_book(self.staff, FIELDS)
        selected, created = create_book(self.staff, FIELDS, existing_book_id=first.pk)
        self.assertEqual(selected.pk, first.pk)
        self.assertFalse(created)

    def test_known_set_isbn_cannot_create_single_reader(self):
        BookSeries.objects.create(name="Set with known ISBN", isbn=ISBN)
        with self.assertRaises(ValidationError):
            create_book(self.staff, FIELDS)
        self.assertFalse(Book.objects.filter(isbn=ISBN).exists())

    def test_idempotency_replay_and_changed_payload(self):
        fields = {"title": "Unknown version", "authors": ["Author"]}
        first, _ = create_book(self.staff, fields, idempotency_key="manual-request")
        duplicate, created = create_book(self.staff, fields, idempotency_key="manual-request")
        self.assertEqual(duplicate.pk, first.pk)
        self.assertFalse(created)
        with self.assertRaises(CatalogConflict) as raised:
            create_book(
                self.staff, {**fields, "title": "Changed"}, idempotency_key="manual-request"
            )
        self.assertEqual(raised.exception.default_code, "idempotency_conflict")
        self.assertEqual(
            CatalogImportRecord.objects.filter(
                source_file=f"catalog-management:{self.staff.pk}"
            ).count(),
            1,
        )

    def test_idempotency_is_scoped_to_user_and_reuse_of_existing_book(self):
        second_admin = get_user_model().objects.create_user(
            username="management-second", is_staff=True
        )
        fields = {"title": "Unidentified edition"}
        first, _ = create_book(self.staff, fields, idempotency_key="same-client-key")
        second, _ = create_book(second_admin, fields, idempotency_key="same-client-key")
        self.assertNotEqual(first.pk, second.pk)
        existing = Book.objects.create(**FIELDS)
        reused, created = create_book(self.staff, FIELDS, idempotency_key="existing-request")
        self.assertEqual(reused.pk, existing.pk)
        self.assertFalse(created)
        replay, created = create_book(self.staff, FIELDS, idempotency_key="existing-request")
        self.assertEqual(replay.pk, existing.pk)
        self.assertFalse(created)
        self.assertIsNone(existing.identity_key)

    def test_failure_rolls_back_book_evidence_and_request_mapping(self):
        count = Book.objects.count()
        with patch(
            "catalog.services.SourceEvidence.objects.get_or_create",
            side_effect=RuntimeError("storage failed"),
        ):
            with self.assertRaises(RuntimeError):
                create_book(self.staff, FIELDS, idempotency_key="rolled-back")
        self.assertEqual(Book.objects.count(), count)
        self.assertFalse(
            CatalogImportRecord.objects.filter(
                source_file=f"catalog-management:{self.staff.pk}"
            ).exists()
        )
        self.assertFalse(SourceEvidence.objects.filter(value=FIELDS["title"]).exists())

    def test_multiple_creates_roll_back_in_callers_confirmation_transaction(self):
        with self.assertRaises(ValidationError), transaction.atomic():
            create_book(self.staff, FIELDS, idempotency_key="confirmation-first")
            create_book(self.staff, {**FIELDS, "isbn": "bad"})
        self.assertFalse(Book.objects.filter(isbn=ISBN).exists())
        self.assertFalse(
            CatalogImportRecord.objects.filter(
                source_file=f"catalog-management:{self.staff.pk}"
            ).exists()
        )

    def photo(self, user=None, sha="a"):
        intake = BookIntake.objects.create(user=user or self.staff)
        return IntakePhoto.objects.create(
            intake=intake,
            file="private/original.jpg",
            original_name="private-original.jpg",
            sha256=sha * 64,
        )

    def test_photo_web_and_manual_sources_preserve_origin_and_all_photos(self):
        first = self.photo()
        second = self.photo(sha="b")
        fields = {
            "title": "Photo Reader",
            "publisher": "Publisher",
            "authors": ["Confirmed author"],
        }
        evidence = [
            {
                "field": "title",
                "value": fields["title"],
                "kind": "photo",
                "photo_id": str(photo.pk),
                "model_name": "actual-model",
            }
            for photo in (first, second, first)
        ] + [
            {
                "field": "publisher",
                "value": "Publisher",
                "kind": "web",
                "source_url": "https://example.org/edition",
            }
        ]
        book, _ = create_book(self.staff, fields, evidence=evidence)
        self.assertEqual(
            set(book.evidence.filter(origin_kind="photo").values_list("photo_id", flat=True)),
            {first.pk, second.pk},
        )
        self.assertEqual(book.evidence.filter(origin_kind="photo").count(), 2)
        self.assertEqual(book.manual_fields, ["authors"])
        self.assertTrue(book.evidence.filter(origin_kind="web", field="publisher").exists())
        self.assertTrue(book.evidence.filter(origin_kind="manual", field="authors").exists())
        data = BookDetailSerializer(book).data
        self.assertEqual(
            {claim["origin_kind"] for claim in data["evidence"]}, {"photo", "manual", "web"}
        )
        self.assertEqual(
            {claim["model_name"] for claim in data["evidence"] if claim["origin_kind"] == "photo"},
            {"actual-model"},
        )
        serialized = json.dumps(data, default=str)
        self.assertNotIn("private/", serialized)
        self.assertNotIn(first.original_name, serialized)
        self.assertNotIn(str(first.pk), serialized)
        self.assertEqual(book.cover_url, "")

    def test_manual_correction_can_coexist_with_same_field_photo_source(self):
        photo = self.photo()
        fields = {"title": "Corrected Reader"}
        evidence = [
            {"field": "title", "value": fields["title"], "kind": "manual"},
            {
                "field": "title",
                "value": fields["title"],
                "kind": "photo",
                "photo_id": str(photo.pk),
            },
        ]
        book, _ = create_book(self.staff, fields, evidence=evidence)
        self.assertEqual(book.manual_fields, ["title"])
        self.assertEqual(book.evidence.count(), 2)

    def test_photo_source_checks_owner_and_confirmed_value(self):
        photo = self.photo(user=self.parent)
        for claim in (
            {
                "field": "title",
                "value": FIELDS["title"],
                "kind": "photo",
                "photo_id": str(photo.pk),
            },
            {"field": "title", "value": "Different", "kind": "manual"},
            {"field": "pages", "value": 20, "kind": "manual"},
            {"field": "title", "value": FIELDS["title"], "kind": "web"},
            {
                "field": "title",
                "value": FIELDS["title"],
                "kind": "manual",
                "source_url": "https://example.org/edition",
            },
            {"field": "title", "value": FIELDS["title"], "kind": "photo"},
        ):
            with self.subTest(claim=claim), self.assertRaises(ValidationError):
                create_book(self.staff, FIELDS, evidence=[claim])
        self.assertFalse(Book.objects.filter(isbn=ISBN).exists())

    def test_archive_is_idempotent_and_cannot_be_recreated_or_restored(self):
        book, _ = create_book(self.staff, FIELDS, idempotency_key="archive-create")
        archived = archive_book(self.staff, book.pk)
        before = Book.objects.filter(pk=book.pk).values().get()
        self.assertIsNotNone(archived.archived_at)
        self.assertIn("catalog_status", archived.manual_fields)
        archive_book(self.staff, book.pk)
        self.assertEqual(Book.objects.filter(pk=book.pk).values().get(), before)
        for kwargs in ({}, {"idempotency_key": "archive-create"}, {"existing_book_id": book.pk}):
            with self.subTest(kwargs=kwargs), self.assertRaises(CatalogConflict):
                create_book(self.staff, FIELDS, **kwargs)
        self.assertEqual(Book.objects.filter(isbn=ISBN).count(), 1)

    def test_archiving_missing_or_legacy_book_returns_not_found(self):
        legacy = Book.objects.create(title="Historical set mapping", catalog_status="legacy")
        for book_id in (legacy.pk, 999999):
            with self.subTest(book_id=book_id), self.assertRaises(NotFound):
                archive_book(self.staff, book_id)


@override_settings(ROOT_URLCONF=__name__)
class ManagementAPITests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username="api-manager", is_staff=True)
        self.parent = get_user_model().objects.create_user(username="api-parent")
        self.client.force_login(self.staff)

    def post(self, fields=None, **kwargs):
        return self.client.post(
            reverse("books"),
            {"fields": fields or FIELDS, **kwargs},
            content_type="application/json",
        )

    def test_anonymous_and_parent_cannot_write_but_parent_can_read(self):
        book = Book.objects.create(**FIELDS)
        for user in (None, self.parent):
            if user:
                self.client.force_login(user)
            else:
                self.client.logout()
            self.assertEqual(self.post().status_code, 403)
            self.assertEqual(
                self.client.delete(reverse("book-detail", args=[book.pk])).status_code, 403
            )
        self.assertEqual(self.client.get(reverse("books")).status_code, 200)
        self.assertEqual(self.client.get(reverse("book-detail", args=[book.pk])).status_code, 200)
        book.refresh_from_db()
        self.assertEqual(book.catalog_status, "active")

    def test_create_and_optional_shelf_are_explicit_and_user_isolated(self):
        first = self.post()
        self.assertEqual(first.status_code, 201)
        self.assertTrue(first.json()["created"])
        self.assertFalse(first.json()["book"]["is_on_shelf"])
        self.assertFalse(ShelfItem.objects.exists())
        second = self.post(add_to_shelf=True)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.json()["created"])
        self.assertTrue(second.json()["book"]["is_on_shelf"])
        item = ShelfItem.objects.get(user=self.staff)
        self.assertEqual((item.ownership_status, item.reading_status), ("owned", "unread"))
        before = ShelfItem.objects.filter(pk=item.pk).values().get()
        self.post(add_to_shelf=True)
        self.assertEqual(ShelfItem.objects.filter(pk=item.pk).values().get(), before)
        self.client.force_login(self.parent)
        self.assertEqual(self.client.get(reverse("shelf")).json()["items"], [])

    def test_optional_shelf_preserves_owned_status_and_readds_removed_book(self):
        book = Book.objects.create(**FIELDS)
        item = ShelfItem.objects.create(
            user=self.staff, book=book, ownership_status="owned", reading_status="familiar"
        )
        before = ShelfItem.objects.filter(pk=item.pk).values().get()
        self.assertEqual(self.post(add_to_shelf=True).status_code, 200)
        self.assertEqual(ShelfItem.objects.filter(pk=item.pk).values().get(), before)
        item.ownership_status = "not_owned"
        item.reading_status = None
        item.save()
        self.assertEqual(self.post(add_to_shelf=True).status_code, 200)
        item.refresh_from_db()
        self.assertEqual((item.ownership_status, item.reading_status), ("owned", "unread"))

    def test_create_api_validation_and_conflict_error_envelopes(self):
        self.assertEqual(self.post({"title": "Missing ISBN is allowed"}).status_code, 201)
        invalid = self.post({**FIELDS, "isbn": "9780375827786"})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["error"]["code"], "invalid_input")
        self.assertEqual(self.post(add_to_shelf="false").status_code, 400)
        self.assertEqual(self.post(unsupported=True).status_code, 400)
        self.post()
        conflict = self.post({**FIELDS, "title": "Conflicting title"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "book_conflict")

    def test_post_and_delete_enforce_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.get("/admin/login/")
        client.force_login(self.staff)
        body = json.dumps({"fields": FIELDS})
        self.assertEqual(
            client.post(reverse("books"), body, content_type="application/json").status_code, 403
        )
        token = client.cookies["csrftoken"].value
        accepted = client.post(
            reverse("books"), body, content_type="application/json", HTTP_X_CSRFTOKEN=token
        )
        self.assertEqual(accepted.status_code, 201)
        url = reverse("book-detail", args=[accepted.json()["book"]["id"]])
        self.assertEqual(client.delete(url).status_code, 403)
        self.assertEqual(client.delete(url, HTTP_X_CSRFTOKEN=token).status_code, 204)

    def test_archive_preserves_shelf_and_multiple_series_references(self):
        archived = Book.objects.create(**FIELDS)
        active = Book.objects.create(title="Active remaining reader")
        first = BookSeries.objects.create(
            name="Archive Group One", expected_count=2, completeness="complete"
        )
        second = BookSeries.objects.create(
            name="Archive Group Two", expected_count=1, completeness="complete"
        )
        first.books.add(archived, active)
        second.books.add(archived)
        for user, reading in ((self.staff, "reading"), (self.parent, "familiar")):
            ShelfItem.objects.create(
                user=user, book=archived, ownership_status="owned", reading_status=reading
            )
        before_items = list(ShelfItem.objects.filter(book=archived).values())
        before_memberships = list(SeriesMembership.objects.filter(book=archived).values())
        url = reverse("book-detail", args=[archived.pk])
        self.assertEqual(self.client.delete(url).status_code, 204)
        archived.refresh_from_db()
        updated_at = archived.updated_at
        self.assertEqual(self.client.delete(url).status_code, 204)
        archived.refresh_from_db()
        self.assertEqual(archived.updated_at, updated_at)
        self.assertEqual(list(ShelfItem.objects.filter(book=archived).values()), before_items)
        self.assertEqual(
            list(SeriesMembership.objects.filter(book=archived).values()), before_memberships
        )
        self.assertEqual(self.client.get(url).status_code, 200)
        catalog = self.client.get(reverse("books")).json()
        self.assertNotIn(archived.pk, {book["id"] for book in catalog["books"]})
        group = self.client.get(reverse("series-detail", args=[first.pk])).json()
        self.assertEqual(group["total_count"], 1)
        self.assertEqual(group["owned_count"], 0)
        self.assertEqual(group["completeness"], "partial")
        self.assertEqual([book["id"] for book in group["books"]], [active.pk])
        self.assertEqual(
            self.client.post(
                reverse("shelf-series", args=[first.pk]), {}, content_type="application/json"
            ).json()["total_count"],
            1,
        )
        self.assertEqual(
            self.client.post(
                reverse("shelf-series", args=[second.pk]), {}, content_type="application/json"
            ).status_code,
            400,
        )
        for user in (self.staff, self.parent):
            self.client.force_login(user)
            item = next(
                item
                for item in self.client.get(reverse("shelf")).json()["items"]
                if item["book"]["id"] == archived.pk
            )
            self.assertEqual(item["book"]["catalog_status"], "archived")
            self.assertIsNotNone(item["book"]["archived_at"])
            self.assertTrue(item["book"]["is_on_shelf"])

    def test_series_serializer_filters_archived_members_without_prefetch(self):
        first = Book.objects.create(title="Archived member", catalog_status="archived")
        second = Book.objects.create(title="Active member")
        series = BookSeries.objects.create(
            name="Direct serializer group", expected_count=2, completeness="complete"
        )
        series.books.add(first, second)
        data = BookSeriesSerializer(series, context={"owned_book_ids": {first.pk}}).data
        self.assertEqual(data["total_count"], 1)
        self.assertEqual(data["owned_count"], 0)
        self.assertEqual(data["completeness"], "partial")
        self.assertEqual([book["id"] for book in data["books"]], [second.pk])

    def test_shelf_failure_rolls_back_entire_manual_create(self):
        with patch(
            "catalog.views.ShelfItem.objects.get_or_create",
            side_effect=RuntimeError("shelf failed"),
        ):
            with self.assertRaises(RuntimeError):
                self.post(add_to_shelf=True, idempotency_key="shelf-rollback")
        self.assertFalse(Book.objects.filter(isbn=ISBN).exists())
        self.assertFalse(
            CatalogImportRecord.objects.filter(
                source_file=f"catalog-management:{self.staff.pk}"
            ).exists()
        )

    @override_settings(
        MIMO_API_KEY="offline-test-key",
        MIMO_BASE_URL="https://example.org/v1",
        MIMO_BATCH_BASE_URL="",
    )
    def test_capabilities_are_booleans_without_configuration_values(self):
        url = reverse("management-capabilities")
        expected = {
            "can_manage_catalog": True,
            "realtime_configured": True,
            "batch_configured": False,
        }
        self.assertEqual(self.client.get(url).json(), expected)
        self.client.force_login(self.parent)
        self.assertEqual(self.client.get(url).json(), {**expected, "can_manage_catalog": False})
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 403)

    @override_settings(
        MIMO_API_KEY="",
        MIMO_BASE_URL="https://example.org/v1",
        MIMO_BATCH_BASE_URL="https://batch.example.org/v1",
    )
    def test_missing_model_credentials_do_not_block_manual_creation(self):
        data = self.client.get(reverse("management-capabilities")).json()
        self.assertFalse(data["realtime_configured"])
        self.assertFalse(data["batch_configured"])
        self.assertEqual(self.post().status_code, 201)


class ManagementConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.admin_ids = [
            get_user_model()
            .objects.create_user(username=f"concurrent-manager-{i}", is_staff=True)
            .pk
            for i in range(2)
        ]

    def create_concurrently(self, fields, same_user=False, key=""):
        barrier = Barrier(2)

        def write(index):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=self.admin_ids[0 if same_user else index])
                barrier.wait(timeout=10)
                book, created = create_book(user, fields, idempotency_key=key)
                return book.pk, created
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(write, range(2)))

    def test_two_administrators_create_one_isbn_version(self):
        result = self.create_concurrently(FIELDS)
        self.assertEqual(len({book_id for book_id, _ in result}), 1)
        self.assertEqual(sum(created for _, created in result), 1)
        self.assertEqual(Book.objects.filter(isbn=ISBN).count(), 1)

    def test_same_user_idempotency_serializes_unknown_edition(self):
        fields = {"title": "Concurrent unidentified reader"}
        result = self.create_concurrently(fields, same_user=True, key="concurrent-request")
        self.assertEqual(len({book_id for book_id, _ in result}), 1)
        self.assertEqual(sum(created for _, created in result), 1)
        self.assertEqual(Book.objects.filter(title=fields["title"]).count(), 1)
        self.assertEqual(
            CatalogImportRecord.objects.filter(
                source_file=f"catalog-management:{self.admin_ids[0]}"
            ).count(),
            1,
        )
