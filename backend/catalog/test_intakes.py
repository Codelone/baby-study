import copy
import hashlib
import io
import json
import shutil
import tempfile
import uuid
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from ai.client import MimoClient, ModelAPIError
from catalog.intake_worker import (
    claim_next,
    claim_poll,
    poll_job,
    renew,
    run_once,
    save_photo_result,
)
from catalog.models import Book, BookIntake, IntakeBatch, IntakePhoto, ShelfItem


def picture(color="red"):
    stream = io.BytesIO()
    Image.new("RGB", (60, 60), color).save(stream, format="JPEG")
    return stream.getvalue()


def completion(title):
    return {
        "model": "mimo-v2.6-flash",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "fields": {"title": title, "authors": ["Fixture Writer"]},
                            "photo_type": "cover",
                            "isbn_kind": "none",
                            "identity_text": title,
                            "issues": [],
                        }
                    )
                },
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
    }


class IntakeTestHelpers:
    def setUp(self):
        self.media = tempfile.mkdtemp(prefix="baby-study-intakes-")
        self.config = override_settings(MEDIA_ROOT=self.media)
        self.config.enable()
        self.addCleanup(self.config.disable)
        self.addCleanup(shutil.rmtree, self.media)
        users = get_user_model()
        self.admin = users.objects.create_user("intake_admin", password="test", is_staff=True)
        self.other = users.objects.create_user("other_admin", is_staff=True)
        self.parent = users.objects.create_user("intake_parent")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def photo(self, intake, color="red", status="pending", extraction=None):
        data = picture(color)
        row = IntakePhoto(
            intake=intake,
            original_name=f"{color}.jpg",
            sha256=hashlib.sha256(data).hexdigest(),
            status=status,
            extraction=extraction or {},
        )
        row.file.save("fixture.jpg", ContentFile(data))
        return row

    def draft(self, two=False):
        intake = BookIntake.objects.create(user=self.admin, status="awaiting_confirmation")
        photo = self.photo(intake, status="succeeded")
        intake.groups = [
            {
                "id": "one",
                "photo_ids": [str(photo.id)],
                "fields": {"title": "Fixture Book One", "language": "english"},
                "evidence": [],
                "issues": [],
                "selected": True,
                "existing_book_id": None,
            }
        ]
        if two:
            other = self.photo(intake, "blue", status="succeeded")
            intake.groups.append(
                {
                    "id": "two",
                    "photo_ids": [str(other.id)],
                    "fields": {"title": "Fixture Book Two"},
                    "evidence": [],
                    "issues": [],
                    "selected": True,
                    "existing_book_id": None,
                }
            )
        intake.save()
        return intake

    def url(self, intake, operation=""):
        return f"/baby/study/book-intakes/{intake.id}" + (f"/{operation}" if operation else "")

    def upload(self, files=None, mode=None):
        data = {
            "files": files
            or [SimpleUploadedFile("cover.jpg", picture(), content_type="image/jpeg")]
        }
        if mode is not None:
            data["mode"] = mode
        return self.client.post("/baby/study/book-intakes", data, format="multipart")


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class IntakeTests(IntakeTestHelpers, TestCase):
    def test_upload_defaults_realtime_and_removes_duplicate_photos(self):
        files = [
            SimpleUploadedFile(name, picture(), content_type="image/jpeg")
            for name in ["first.jpg", "second.jpg"]
        ]
        response = self.upload(files)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["intake"]["mode"], "realtime")
        self.assertEqual(len(response.data["intake"]["photos"]), 1)
        self.assertEqual(response.data["intake"]["groups"], [])

    def test_upload_realtime_is_explicit(self):
        response = self.upload(mode="realtime")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["intake"]["mode"], "realtime")

    def test_invalid_picture_is_rejected_before_intake_creation(self):
        response = self.upload(
            [SimpleUploadedFile("fake.jpg", b"not an image", content_type="image/jpeg")]
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(BookIntake.objects.exists())

    @override_settings(INTAKE_MAX_PHOTO_BYTES=10)
    def test_upload_size_limit(self):
        self.assertEqual(self.upload().status_code, 400)

    def test_parent_cannot_upload_or_access_management_tasks(self):
        intake = self.draft()
        self.client.force_authenticate(self.parent)
        self.assertEqual(self.upload().status_code, 403)
        self.assertEqual(self.client.get(self.url(intake)).status_code, 403)

    def test_admin_cannot_access_another_admin_private_task_or_photo(self):
        intake = self.draft()
        photo = intake.photos.first()
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url(intake)).status_code, 404)
        self.assertEqual(self.client.get(self.url(intake, f"photos/{photo.id}")).status_code, 404)
        self.assertEqual(self.client.get("/baby/study/book-intakes").data["intakes"], [])

    def test_authenticated_photo_is_private_and_unauthenticated_is_denied(self):
        intake = self.draft()
        photo = intake.photos.first()
        response = self.client.get(self.url(intake, f"photos/{photo.id}"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        for closer in response._resource_closers:
            self.addCleanup(closer)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url(intake, f"photos/{photo.id}")).status_code, 403)

    def test_session_upload_requires_csrf(self):
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response = client.post(
            "/baby/study/book-intakes",
            {"files": SimpleUploadedFile("cover.jpg", picture())},
            format="multipart",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(BookIntake.objects.exists())

    def test_group_revision_conflict_preserves_server_draft(self):
        intake = self.draft()
        response = self.client.patch(
            self.url(intake, "groups"), {"revision": 0, "groups": []}, format="json"
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["error"]["code"], "revision_conflict")
        intake.refresh_from_db()
        self.assertEqual(intake.groups[0]["fields"]["title"], "Fixture Book One")

    def test_group_correction_tracks_manual_source(self):
        intake = self.draft()
        groups = intake.groups
        groups[0]["fields"]["title"] = "Corrected Fixture Title"
        response = self.client.patch(
            self.url(intake, "groups"),
            {"revision": 1, "groups": groups, "unassigned_photo_ids": []},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        intake.refresh_from_db()
        self.assertTrue(intake.has_manual_edits)
        self.assertEqual(intake.groups[0]["evidence"][0]["kind"], "manual")

    def test_group_cannot_move_foreign_or_duplicate_photos(self):
        intake = self.draft(two=True)
        groups = intake.groups
        groups[1]["photo_ids"] = groups[0]["photo_ids"]
        response = self.client.patch(
            self.url(intake, "groups"), {"revision": 1, "groups": groups}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    def test_merge_retains_both_trusted_photo_sources_and_rejects_fabricated_source(self):
        intake = self.draft(two=True)
        photo_ids = []
        for group in intake.groups:
            photo_id = group["photo_ids"][0]
            photo_ids.append(photo_id)
            group["fields"]["title"] = "Shared Reader"
            group["evidence"] = [
                {
                    "field": "title",
                    "value": "Shared Reader",
                    "kind": "photo",
                    "photo_id": photo_id,
                    "source_label": "封面识别",
                }
            ]
        intake.save()
        merged = copy.deepcopy(intake.groups[0])
        merged["photo_ids"] = photo_ids
        merged["evidence"].append(
            {
                "field": "publisher",
                "value": "Fabricated Press",
                "kind": "photo",
                "photo_id": photo_ids[0],
            }
        )
        merged["fields"]["publisher"] = "Fabricated Press"
        response = self.client.patch(
            self.url(intake, "groups"),
            {"revision": 1, "groups": [merged], "unassigned_photo_ids": []},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        claims = response.data["intake"]["groups"][0]["evidence"]
        self.assertEqual(
            {claim["photo_id"] for claim in claims if claim["field"] == "title"}, set(photo_ids)
        )
        self.assertEqual(
            next(claim for claim in claims if claim["field"] == "publisher")["kind"], "manual"
        )

    def test_confirmation_preserves_compatible_partial_author_and_case_sources(self):
        intake = self.draft(two=True)
        first, second = [group["photo_ids"][0] for group in intake.groups]
        intake.groups = [
            {
                "id": "one",
                "photo_ids": [first, second],
                "fields": {"title": "Shared Reader", "authors": ["Author A", "Author B"]},
                "evidence": [
                    {
                        "field": "title",
                        "value": "SHARED READER",
                        "kind": "photo",
                        "photo_id": first,
                    },
                    {"field": "authors", "value": ["Author A"], "kind": "photo", "photo_id": first},
                    {
                        "field": "authors",
                        "value": ["Author B"],
                        "kind": "photo",
                        "photo_id": second,
                    },
                ],
            }
        ]
        intake.save()
        response = self.client.post(
            self.url(intake, "confirm"), {"revision": 1, "group_ids": ["one"]}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        book = Book.objects.get(pk=response.data["books"][0]["book_id"])
        self.assertEqual(book.evidence.get(field="title").value, "SHARED READER")
        self.assertCountEqual(
            list(book.evidence.filter(field="authors").values_list("value", flat=True)),
            [["Author A"], ["Author B"]],
        )

    def test_archived_owned_book_remains_readable_and_editable_without_new_ownership(self):
        book = Book.objects.create(title="Archived Fixture", catalog_status="archived")
        ShelfItem.objects.create(
            user=self.parent, book=book, ownership_status="owned", reading_status="reading"
        )
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/baby/study/books/{book.id}").status_code, 404)
        self.assertEqual(
            self.client.put(
                f"/baby/study/shelf/{book.id}",
                {"ownership_status": "owned", "reading_status": "unread"},
                format="json",
            ).status_code,
            404,
        )
        self.client.force_authenticate(self.parent)
        self.assertEqual(self.client.get(f"/baby/study/books/{book.id}").status_code, 200)
        self.assertEqual(
            self.client.put(
                f"/baby/study/shelf/{book.id}",
                {"ownership_status": "owned", "reading_status": "familiar"},
                format="json",
            ).status_code,
            200,
        )
        item = ShelfItem.objects.get(user=self.parent, book=book)
        self.assertEqual(item.reading_status, "familiar")
        self.assertEqual(
            self.client.put(
                f"/baby/study/shelf/{book.id}",
                {"ownership_status": "not_owned", "reading_status": None},
                format="json",
            ).status_code,
            200,
        )
        self.assertEqual(self.client.get(f"/baby/study/books/{book.id}").status_code, 404)

    @patch("catalog.intake.enrichment.search_candidates")
    def test_enrichment_updates_revision_and_rejects_concurrent_old_candidate(self, search):
        intake = self.draft()
        search.return_value = {"candidates": [], "issues": []}
        payload = {"revision": 1, "group_ids": ["one"]}
        first = self.client.post(self.url(intake, "enrichment"), payload, format="json")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data["intake"]["revision"], 2)
        self.assertEqual(
            self.client.post(self.url(intake, "enrichment"), payload, format="json").status_code,
            409,
        )

    def test_confirmation_is_atomic_and_never_automatic(self):
        intake = self.draft(two=True)
        before = Book.objects.count()
        intake.groups[1]["fields"]["title"] = ""
        intake.save()
        response = self.client.post(
            self.url(intake, "confirm"), {"revision": 1, "group_ids": ["one", "two"]}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Book.objects.count(), before)
        intake.refresh_from_db()
        self.assertEqual(intake.status, "awaiting_confirmation")

    def test_confirmation_is_idempotent_and_does_not_assume_ownership(self):
        intake = self.draft()
        payload = {"revision": 1, "group_ids": ["one"]}
        first = self.client.post(self.url(intake, "confirm"), payload, format="json")
        self.assertEqual(first.status_code, 200)
        second = self.client.post(self.url(intake, "confirm"), payload, format="json")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["books"], second.data["books"])
        self.assertFalse(ShelfItem.objects.filter(user=self.admin).exists())

    def test_confirmation_can_explicitly_add_to_own_shelf(self):
        intake = self.draft()
        response = self.client.post(
            self.url(intake, "confirm"),
            {"revision": 1, "group_ids": ["one"], "add_to_shelf": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ShelfItem.objects.get(user=self.admin).reading_status, "unread")

    def test_confirmed_task_is_immutable(self):
        intake = self.draft()
        self.client.post(
            self.url(intake, "confirm"), {"revision": 1, "group_ids": ["one"]}, format="json"
        )
        response = self.client.patch(
            self.url(intake, "groups"), {"revision": 2, "groups": intake.groups}, format="json"
        )
        self.assertEqual(response.status_code, 409)

    def test_cancelled_task_cannot_confirm(self):
        intake = self.draft()
        response = self.client.post(self.url(intake, "cancel"), {"revision": 1}, format="json")
        self.assertEqual(response.data["intake"]["status"], "cancelled")
        self.assertEqual(
            self.client.post(
                self.url(intake, "confirm"), {"revision": 2, "group_ids": ["one"]}, format="json"
            ).status_code,
            409,
        )

    @patch("catalog.intake.enrichment.search_candidates")
    def test_enrichment_is_optional_and_does_not_overwrite_fields(self, search):
        intake = self.draft()
        search.return_value = {
            "candidates": [
                {
                    "id": "candidate",
                    "fields": {"title": "External Candidate"},
                    "evidence": [
                        {
                            "field": "title",
                            "value": "External Candidate",
                            "kind": "web",
                            "source_url": "https://openlibrary.org/books/OL1M",
                            "source_label": "Open Library",
                        }
                    ],
                    "issues": [],
                }
            ],
            "issues": [],
        }
        response = self.client.post(
            self.url(intake, "enrichment"), {"revision": 1, "group_ids": ["one"]}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        intake.refresh_from_db()
        self.assertEqual(intake.groups[0]["fields"]["title"], "Fixture Book One")
        self.assertEqual(intake.groups[0]["enrichment_candidates"][0]["id"], "candidate")

    def test_cleanup_keeps_text_and_book_provenance(self):
        intake = self.draft()
        photo = intake.photos.first()
        IntakePhoto.objects.filter(pk=photo.pk).update(
            created_at=timezone.now() - timedelta(days=31),
            extraction={"fields": {"title": "Preserved"}},
        )
        call_command("cleanup_intake_media", stdout=io.StringIO())
        photo.refresh_from_db()
        self.assertFalse(photo.file)
        self.assertEqual(photo.extraction["fields"]["title"], "Preserved")
        self.assertEqual(self.client.get(self.url(intake, f"photos/{photo.id}")).status_code, 404)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class IntakeWorkerTests(IntakeTestHelpers, TestCase):
    def setUp(self):
        super().setUp()
        self.provider = Mock()
        self.client_model = MimoClient(api_key="test-not-a-real-key")
        self.client_model.chat = Mock()
        self.model_patch = patch(
            "ai.client.MimoClient.from_settings", return_value=self.client_model
        )
        self.batch_patch = patch("ai.batch.BatchClient.from_settings", return_value=self.provider)
        self.model_patch.start()
        self.batch_patch.start()
        self.addCleanup(self.model_patch.stop)
        self.addCleanup(self.batch_patch.stop)

    def test_realtime_worker_only_builds_draft_and_records_usage(self):
        intake = BookIntake.objects.create(user=self.admin, mode="realtime")
        photo = self.photo(intake)
        self.client_model.chat.return_value = completion("Worker Fixture")
        before = Book.objects.count()
        run_once()
        intake.refresh_from_db()
        photo.refresh_from_db()
        self.assertEqual(intake.status, "awaiting_confirmation")
        self.assertEqual(intake.groups[0]["fields"]["title"], "Worker Fixture")
        self.assertEqual(photo.extraction["usage"]["total_tokens"], 50)
        self.assertEqual(Book.objects.count(), before)

    def test_batch_submission_and_out_of_order_partial_result_mapping(self):
        intake = BookIntake.objects.create(user=self.admin, mode="batch")
        first = self.photo(intake)
        second = self.photo(intake, "blue")
        self.provider.submit.return_value = {
            "id": "batch-fixture",
            "input_file_id": "input",
            "status": "validating",
        }
        run_once()
        job = intake.batches.get()
        self.provider.retrieve.return_value = {
            "status": "completed",
            "output_file_id": "ok",
            "error_file_id": "bad",
        }
        mapping = {row["photo_id"]: row["custom_id"] for row in job.requests}
        self.provider.results.side_effect = lambda file_id: (
            [
                {
                    "custom_id": mapping[str(second.id)],
                    "response": {"status_code": 200, "body": completion("Second Fixture")},
                    "error": None,
                }
            ]
            if file_id == "ok"
            else [
                {
                    "custom_id": mapping[str(first.id)],
                    "response": None,
                    "error": {"code": "untrusted-provider-message"},
                }
            ]
        )
        run_once()
        first.refresh_from_db()
        second.refresh_from_db()
        intake.refresh_from_db()
        self.assertEqual(first.status, "failed")
        self.assertEqual(second.extraction["fields"]["title"], "Second Fixture")
        self.assertEqual(intake.status, "partial_failure")
        self.assertNotIn("untrusted-provider-message", json.dumps(intake.errors))

    def test_retry_submits_only_failed_photo(self):
        intake = BookIntake.objects.create(user=self.admin, mode="batch", status="partial_failure")
        failed = self.photo(intake, status="failed")
        self.photo(
            intake,
            "blue",
            status="succeeded",
            extraction=json.loads(completion("Keep Fixture")["choices"][0]["message"]["content"]),
        )
        response = self.client.post(self.url(intake, "retry"), {"revision": 1}, format="json")
        self.assertEqual(response.status_code, 200)
        self.provider.submit.return_value = {"id": "retry-batch", "status": "validating"}
        run_once()
        requests = self.provider.submit.call_args.args[0]
        self.assertEqual(len(requests), 1)
        self.assertIn(str(failed.id), requests[0]["custom_id"])

    def test_cancellation_ignores_late_result_and_cancels_remote_job(self):
        intake = BookIntake.objects.create(
            user=self.admin, mode="batch", status="cancelled", generation=2
        )
        photo = self.photo(intake, status="processing")
        job = IntakeBatch.objects.create(
            intake=intake,
            generation=1,
            provider_batch_id="late-batch",
            status="validating",
            requests=[{"photo_id": str(photo.id), "custom_id": "old"}],
        )
        self.provider.cancel.return_value = {"status": "cancelling"}
        run_once()
        self.provider.cancel.assert_called_once_with("late-batch")
        self.provider.retrieve.return_value = {"status": "completed", "output_file_id": "late-file"}
        run_once()
        photo.refresh_from_db()
        intake.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(photo.status, "processing")
        self.assertEqual(intake.status, "cancelled")
        self.assertEqual(job.status, "completed")
        self.provider.results.assert_not_called()

    def test_unknown_submission_is_not_blindly_retried(self):
        intake = BookIntake.objects.create(user=self.admin, mode="batch")
        self.photo(intake)
        self.provider.submit.side_effect = ModelAPIError("submission_unknown")
        run_once()
        intake.refresh_from_db()
        self.assertEqual(intake.status, "failed")
        self.assertEqual(intake.batches.get().status, "submission_unknown")
        response = self.client.post(
            self.url(intake, "retry"), {"revision": intake.revision}, format="json"
        )
        self.assertEqual(response.status_code, 409)
        run_once()
        self.assertEqual(self.provider.submit.call_count, 1)

    def test_output_download_retry_does_not_resubmit_inference(self):
        intake = BookIntake.objects.create(user=self.admin, mode="batch", status="processing")
        photo = self.photo(intake, status="processing")
        job = IntakeBatch.objects.create(
            intake=intake,
            provider_batch_id="ready",
            status="validating",
            requests=[{"photo_id": str(photo.id), "custom_id": "one"}],
        )
        self.provider.retrieve.return_value = {"status": "completed", "output_file_id": "output"}
        self.provider.results.side_effect = ModelAPIError("temporarily_unavailable")
        run_once()
        job.refresh_from_db()
        self.assertEqual(job.status, "retrieving_results")
        self.provider.results.side_effect = None
        self.provider.results.return_value = [
            {
                "custom_id": "one",
                "response": {"status_code": 200, "body": completion("Recovered Fixture")},
            }
        ]
        run_once()
        intake.refresh_from_db()
        self.assertEqual(intake.status, "awaiting_confirmation")
        self.provider.submit.assert_not_called()

    def test_manual_grouping_survives_failed_photo_retry(self):
        intake = self.draft()
        intake.mode = "realtime"
        intake.has_manual_edits = True
        intake.groups[0]["fields"]["title"] = "Parent Correction"
        intake.status = "partial_failure"
        intake.save()
        photo = self.photo(intake, "blue", status="failed")
        self.client.post(self.url(intake, "retry"), {"revision": 1}, format="json")
        self.client_model.chat.return_value = completion("Other Model Suggestion")
        run_once()
        intake.refresh_from_db()
        self.assertEqual(intake.groups[0]["fields"]["title"], "Parent Correction")
        self.assertIn(str(photo.id), intake.unassigned_photo_ids)

    def test_missing_batch_configuration_does_not_switch_to_realtime(self):
        self.batch_patch.stop()
        with patch(
            "ai.batch.BatchClient.from_settings", side_effect=ModelAPIError("batch_not_configured")
        ):
            intake = BookIntake.objects.create(user=self.admin, mode="batch")
            photo = self.photo(intake)
            run_once()
        intake.refresh_from_db()
        photo.refresh_from_db()
        self.assertEqual(intake.mode, "batch")
        self.assertEqual(intake.status, "failed")
        self.assertEqual(photo.error["code"], "batch_not_configured")
        self.client_model.chat.assert_not_called()

    def test_expired_realtime_lease_does_not_repeat_unknown_paid_call(self):
        intake = BookIntake.objects.create(
            user=self.admin,
            mode="realtime",
            status="processing",
            lease_expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.photo(intake, status="processing")
        run_once()
        intake.refresh_from_db()
        self.assertEqual(intake.status, "failed")
        self.client_model.chat.assert_not_called()

    def test_reclaimed_lease_fences_old_realtime_worker_and_prevents_paid_call(self):
        intake = BookIntake.objects.create(user=self.admin, mode="realtime")
        photo = self.photo(intake)
        old = claim_next()
        BookIntake.objects.filter(pk=intake.pk).update(
            lease_expires_at=timezone.now() - timedelta(minutes=1)
        )
        current = claim_next()
        self.assertNotEqual(old.lease_token, current.lease_token)
        self.assertFalse(renew(intake.id, intake.generation, old.lease_token))
        self.assertFalse(
            save_photo_result(
                intake.id,
                intake.generation,
                photo.id,
                extraction={"fields": {"title": "Stale"}},
                lease_token=old.lease_token,
            )
        )
        from catalog.intake_worker import process_realtime

        process_realtime(old)
        self.client_model.chat.assert_not_called()
        photo.refresh_from_db()
        self.assertEqual(photo.status, "pending")

    def test_old_batch_poll_cannot_release_a_new_worker_lease(self):
        intake = BookIntake.objects.create(user=self.admin, generation=2)
        job = IntakeBatch.objects.create(
            intake=intake, generation=1, provider_batch_id="old-batch", status="retrieving_results"
        )
        fresh_token = uuid.uuid4()

        def cancel_response(_):
            BookIntake.objects.filter(pk=intake.pk).update(
                status="processing",
                lease_token=fresh_token,
                lease_expires_at=timezone.now() + timedelta(minutes=5),
            )
            return {"status": "cancelled"}

        self.provider.cancel.side_effect = cancel_response
        poll_job(job)
        intake.refresh_from_db()
        self.assertEqual(intake.lease_token, fresh_token)
        self.assertIsNotNone(intake.lease_expires_at)

    def test_queued_intake_with_active_poll_lease_is_not_claimed(self):
        intake = BookIntake.objects.create(user=self.admin, mode="batch")
        job = IntakeBatch.objects.create(intake=intake, generation=0)
        self.assertTrue(claim_poll(job))
        self.assertIsNone(claim_next())

    def test_first_submission_terminal_status_still_consumes_results(self):
        intake = BookIntake.objects.create(user=self.admin, mode="batch")
        photo = self.photo(intake)
        self.provider.submit.return_value = {"id": "instant-failure", "status": "failed"}
        run_once()
        self.assertEqual(intake.batches.get().status, "retrieving_results")
        self.provider.retrieve.return_value = {"status": "failed"}
        run_once()
        intake.refresh_from_db()
        photo.refresh_from_db()
        self.assertEqual(intake.status, "failed")
        self.assertEqual(photo.status, "failed")

    def test_poll_transport_failure_is_visible_and_does_not_resubmit_inference(self):
        intake = BookIntake.objects.create(user=self.admin, mode="batch", status="processing")
        job = IntakeBatch.objects.create(
            intake=intake, generation=1, provider_batch_id="waiting-batch", status="validating"
        )
        self.provider.retrieve.side_effect = ModelAPIError("timeout", retryable=True)
        poll_job(job)
        intake.refresh_from_db()
        self.assertEqual(intake.errors[0]["code"], "timeout")
        self.assertEqual(intake.status, "processing")
        self.provider.submit.assert_not_called()
        self.provider.retrieve.side_effect = None
        self.provider.retrieve.return_value = {"status": "in_progress"}
        poll_job(job)
        intake.refresh_from_db()
        self.assertEqual(intake.errors, [])

    def test_known_batch_id_after_uncertain_submit_is_reconciled_without_resubmission(self):
        intake = BookIntake.objects.create(user=self.admin, mode="batch")
        photo = self.photo(intake)
        uncertain = ModelAPIError("submission_unknown")
        uncertain.provider_batch_id = "known-batch"
        uncertain.input_file_id = "known-input"
        self.provider.submit.side_effect = uncertain
        run_once()
        job = intake.batches.get()
        self.assertEqual(job.status, "reconciling")
        self.assertEqual(job.provider_batch_id, "known-batch")
        custom_id = job.requests[0]["custom_id"]
        self.provider.retrieve.return_value = {"status": "completed", "output_file_id": "output"}
        self.provider.results.return_value = [
            {
                "custom_id": custom_id,
                "response": {"status_code": 200, "body": completion("Known Fixture")},
            }
        ]
        run_once()
        photo.refresh_from_db()
        intake.refresh_from_db()
        self.assertEqual(photo.status, "succeeded")
        self.assertEqual(intake.status, "awaiting_confirmation")
        self.assertEqual(self.provider.submit.call_count, 1)
