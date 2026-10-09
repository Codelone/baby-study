import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connections
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from catalog.intake_api import serialize_intake
from catalog.intake_worker import run_once
from catalog.models import BookIntake, BookSeries, IntakeBatch
from catalog.test_intakes import IntakeTestHelpers, picture
from config.observability import JsonFormatter, event, log_context


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SubmissionTests(IntakeTestHelpers, TestCase):
    def submit(self, key, color="red", **extra):
        return self.client.post(
            "/baby/study/book-intakes",
            {
                "idempotency_key": str(key),
                "files": [
                    SimpleUploadedFile("photo.jpg", picture(color), content_type="image/jpeg")
                ],
                **extra,
            },
            format="multipart",
        )

    def test_response_loss_replay_preserves_one_task_and_photos(self):
        key = uuid.uuid4()
        first, second = self.submit(key), self.submit(key)
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.data["intake"]["id"], second.data["intake"]["id"])
        self.assertEqual(BookIntake.objects.count(), 1)
        self.assertEqual(BookIntake.objects.get().photos.count(), 1)
        self.assertEqual(first.data["intake"]["mode"], "realtime")
        self.assertEqual(first.data["intake"]["processing_stage"], "queued")

    def test_reused_key_cannot_change_files_or_mode(self):
        key = uuid.uuid4()
        self.submit(key)
        self.assertEqual(self.submit(key, "blue").status_code, 409)
        self.assertEqual(self.submit(key, mode="batch").status_code, 409)
        series = BookSeries.objects.create(name="Fixture Series")
        self.assertEqual(self.submit(key, target_series_id=series.pk).status_code, 409)
        self.assertEqual(BookIntake.objects.count(), 1)

    def test_invalid_key_is_rejected_and_keys_are_user_scoped(self):
        self.assertEqual(self.submit("bad-key").status_code, 400)
        key = uuid.uuid4()
        self.submit(key)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.submit(key, "blue").status_code, 201)
        self.assertEqual(BookIntake.objects.count(), 2)

    def test_explicit_batch_stage_and_model_default(self):
        response = self.submit(uuid.uuid4(), mode="batch")
        intake = BookIntake.objects.get(pk=response.data["intake"]["id"])
        intake.status = "processing"
        IntakeBatch.objects.create(intake=intake, status="validating", provider_batch_id="fixture")
        body = serialize_intake(intake)
        self.assertEqual(
            (body["processing_stage"], body["batch_status"]), ("supplier_waiting", "validating")
        )
        self.assertEqual(BookIntake.objects.create(user=self.admin).mode, "realtime")

    def test_new_tasks_are_processed_before_supplier_polls(self):
        BookIntake.objects.create(user=self.admin)
        old = BookIntake.objects.create(user=self.admin, mode="batch", status="processing")
        IntakeBatch.objects.create(intake=old, status="validating", provider_batch_id="fixture")
        calls = []
        with patch(
            "catalog.intake_worker.process_realtime", side_effect=lambda _: calls.append("new")
        ):
            with patch(
                "catalog.intake_worker.poll_job", side_effect=lambda _: calls.append("poll")
            ):
                run_once()
                self.assertEqual(calls, ["new", "poll"])
                calls.clear()
                run_once(poll_batches=False)
                self.assertEqual(calls, [])


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ConcurrentSubmissionTests(IntakeTestHelpers, TransactionTestCase):
    def test_concurrent_same_submission_has_one_owner(self):
        key = uuid.uuid4()
        barrier = threading.Barrier(2)
        create = BookIntake.objects.create

        def simultaneous_create(**kwargs):
            barrier.wait(timeout=10)
            return create(**kwargs)

        def upload(_):
            client = APIClient()
            client.force_authenticate(self.admin)
            try:
                response = client.post(
                    "/baby/study/book-intakes",
                    {
                        "idempotency_key": str(key),
                        "files": [
                            SimpleUploadedFile("photo.jpg", picture(), content_type="image/jpeg")
                        ],
                    },
                    format="multipart",
                )
                return response.status_code, response.data["intake"]["id"]
            finally:
                connections.close_all()

        with patch("catalog.intake_api.BookIntake.objects.create", side_effect=simultaneous_create):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(upload, range(2)))
        self.assertEqual(sorted(row[0] for row in results), [200, 201])
        self.assertEqual(results[0][1], results[1][1])
        self.assertEqual(BookIntake.objects.count(), 1)
        self.assertEqual(BookIntake.objects.get().photos.count(), 1)


class DiagnosticLoggingTests(SimpleTestCase):
    def test_allowlist_drops_credentials_and_preserves_context(self):
        with self.assertLogs("baby_study", level="INFO") as captured:
            with log_context(intake_id="test-task", photo_id="test-photo"):
                event(
                    "model_attempt",
                    elapsed_ms=12,
                    api_key="private-test-secret",
                    cookie="private-cookie",
                )
        body = json.loads(JsonFormatter().format(captured.records[0]))
        self.assertEqual(body["intake_id"], "test-task")
        self.assertEqual(body["photo_id"], "test-photo")
        self.assertNotIn("private", json.dumps(body))
        self.assertIn("time", body)

    def test_worker_checks_queue_often_and_batches_separately(self):
        with patch(
            "catalog.management.commands.process_book_intakes.run_once",
            return_value={"submitted_or_processed": 0, "polled": 0},
        ) as run:
            with patch(
                "catalog.management.commands.process_book_intakes.time.monotonic",
                side_effect=[0, 0, 5, 65, 65],
            ):
                with patch(
                    "catalog.management.commands.process_book_intakes.time.sleep",
                    side_effect=[None, None, RuntimeError("done")],
                ):
                    with self.assertRaisesRegex(RuntimeError, "done"):
                        call_command("process_book_intakes")
        self.assertEqual(
            [call.kwargs["poll_batches"] for call in run.call_args_list], [True, False, True]
        )
