import base64
import copy
import json
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from catalog.models import BookIntake, IntakeBatch, IntakePhoto

TERMINAL_BATCH = {"completed", "failed", "expired", "cancelled"}


def error(code, message):
    return {"code": code, "message": message}


def photo_data_url(photo):
    with photo.file.open("rb") as stream:
        encoded = base64.b64encode(stream.read()).decode("ascii")
    return "data:image/jpeg;base64," + encoded


def claim_next():
    now = timezone.now()
    with transaction.atomic():
        intake = (
            BookIntake.objects.select_for_update(skip_locked=True)
            .filter(Q(status="queued") | Q(status="processing", lease_expires_at__lt=now))
            .order_by("created_at")
            .first()
        )
        if not intake:
            return None
        if intake.status == "processing":
            # A crashed call may have been billed; require an explicit failed-item retry.
            intake.photos.filter(status="processing").update(
                status="failed", error=error("interrupted", "处理被中断，请核对后重试")
            )
            intake.batches.filter(status="submitting").update(status="submission_unknown")
            if intake.batches.filter(status="submission_unknown").exists():
                intake.photos.filter(status="pending").update(
                    status="failed",
                    error=error("submission_unknown", "提交状态不确定，请核对控制台"),
                )
                intake.status = "failed"
                intake.lease_expires_at = None
                intake.save()
                return None
        intake.status = "processing"
        intake.lease_expires_at = now + timedelta(minutes=5)
        intake.save()
        return intake


def renew(intake_id, generation):
    return bool(
        BookIntake.objects.filter(pk=intake_id, generation=generation, status="processing").update(
            lease_expires_at=timezone.now() + timedelta(minutes=5)
        )
    )


def save_photo_result(intake_id, generation, photo_id, *, extraction=None, problem=None):
    with transaction.atomic():
        intake = BookIntake.objects.select_for_update().get(pk=intake_id)
        if intake.generation != generation or intake.status in {"cancelled", "confirmed"}:
            return False
        photo = IntakePhoto.objects.select_for_update().get(pk=photo_id, intake=intake)
        if photo.status == "succeeded":
            return True
        photo.status = "failed" if problem else "succeeded"
        photo.error = problem or {}
        photo.extraction = extraction or {}
        photo.save()
        counts = list(intake.photos.values_list("status", flat=True))
        intake.progress = {
            "total": len(counts),
            "completed": counts.count("succeeded"),
            "failed": counts.count("failed"),
        }
        intake.save()
    return True


def finish(intake_id, generation):
    from catalog.intake.grouping import group_photos

    with transaction.atomic():
        intake = BookIntake.objects.select_for_update().get(pk=intake_id)
        if intake.generation != generation or intake.status in {"cancelled", "confirmed"}:
            return
        photos = list(intake.photos.all())
        if any(photo.status in {"pending", "processing"} for photo in photos):
            return
        succeeded = [photo for photo in photos if photo.status == "succeeded"]
        failed = [photo for photo in photos if photo.status == "failed"]
        if not intake.has_manual_edits:
            grouped = group_photos(
                [{"id": str(photo.id), "extraction": photo.extraction} for photo in succeeded]
            )
            intake.groups = grouped["groups"]
        # Manual grouping remains authoritative after retry; new photos remain visibly unassigned.
        assigned = {photo_id for group in intake.groups for photo_id in group["photo_ids"]}
        intake.unassigned_photo_ids = [
            str(photo.id) for photo in photos if str(photo.id) not in assigned
        ]
        intake.progress = {"total": len(photos), "completed": len(succeeded), "failed": len(failed)}
        intake.errors = [dict(photo.error, photo_id=str(photo.id)) for photo in failed]
        intake.status = (
            "partial_failure"
            if failed and succeeded
            else "failed"
            if failed
            else "awaiting_confirmation"
        )
        intake.revision += 1
        intake.lease_expires_at = None
        intake.save()


def parse_response(body):
    from ai.client import validate_chat_response
    from catalog.intake.recognition import validate_extraction

    validate_chat_response(body, json_output=True)
    payload = json.loads(body["choices"][0]["message"]["content"])
    extracted = validate_extraction(payload)
    extracted["model_name"] = body.get("model", "")
    extracted["usage"] = {
        key: value
        for key, value in body.get("usage", {}).items()
        if key in {"prompt_tokens", "completion_tokens", "total_tokens"} and type(value) is int
    }
    return extracted


def process_realtime(intake):
    from ai.client import MimoClient, ModelAPIError
    from catalog.intake.recognition import recognition_messages

    client = MimoClient.from_settings()
    for photo in intake.photos.filter(status="pending"):
        if not renew(intake.id, intake.generation):
            break
        photo.status = "processing"
        photo.save(update_fields=["status"])
        try:
            response = client.chat(
                recognition_messages(photo_data_url(photo)), capability="image", json_output=True
            )
            extracted = parse_response(response)
            save_photo_result(intake.id, intake.generation, photo.id, extraction=extracted)
        except ModelAPIError as exc:
            save_photo_result(
                intake.id, intake.generation, photo.id, problem=error(exc.code, exc.message)
            )
        except (OSError, ValueError, KeyError, TypeError):
            save_photo_result(
                intake.id,
                intake.generation,
                photo.id,
                problem=error("invalid_photo_result", "照片或识别资料无效，请重拍或手工修正"),
            )
    finish(intake.id, intake.generation)


def process_batch_submission(intake):
    from ai.batch import BatchClient
    from ai.client import MimoClient, ModelAPIError
    from catalog.intake.recognition import recognition_messages

    photos = list(intake.photos.filter(status="pending"))
    if not photos:
        finish(intake.id, intake.generation)
        return
    client = MimoClient.from_settings()
    provider = BatchClient.from_settings()
    requests, mapping = [], []
    for photo in photos:
        try:
            body = client.build_chat_body(
                recognition_messages(photo_data_url(photo)), capability="image", json_output=True
            )
            custom_id = f"photo-{photo.id}-{intake.generation}"
            requests.append(
                {
                    "custom_id": custom_id,
                    "method": "POST",
                    "url": "/v1/chat/completions",
                    "body": body,
                }
            )
            mapping.append({"custom_id": custom_id, "photo_id": str(photo.id)})
        except (OSError, ValueError, ModelAPIError) as exc:
            problem = error(
                getattr(exc, "code", "media_missing"),
                getattr(exc, "message", "照片已不可用，请重拍"),
            )
            save_photo_result(intake.id, intake.generation, photo.id, problem=problem)
    if not requests or not renew(intake.id, intake.generation):
        finish(intake.id, intake.generation)
        return
    with transaction.atomic():
        current = BookIntake.objects.select_for_update().get(pk=intake.id)
        if current.generation != intake.generation or current.status != "processing":
            return
        job = IntakeBatch.objects.create(
            intake=current, generation=intake.generation, requests=mapping
        )
        current.photos.filter(id__in=[row["photo_id"] for row in mapping]).update(
            status="processing"
        )
    try:
        response = provider.submit(requests, name=f"book-intake-{intake.id}")
        batch_id = response.get("id")
        if not isinstance(batch_id, str) or not batch_id:
            raise ModelAPIError("submission_unknown")
        job.provider_batch_id = batch_id
        job.input_file_id = response.get("input_file_id", "")
        job.status = str(response.get("status", "validating")).lower()
        job.save()
        BookIntake.objects.filter(pk=intake.id, generation=intake.generation).update(
            lease_expires_at=None
        )
    except ModelAPIError as exc:
        job.status = "submission_unknown" if exc.code == "submission_unknown" else "failed"
        job.input_file_id = getattr(exc, "input_file_id", "") or ""
        job.errors = [error(exc.code, exc.message)]
        job.save()
        for row in mapping:
            save_photo_result(
                intake.id, intake.generation, row["photo_id"], problem=error(exc.code, exc.message)
            )
        finish(intake.id, intake.generation)


def claim_poll(job):
    with transaction.atomic():
        intake = (
            BookIntake.objects.select_for_update(skip_locked=True).filter(pk=job.intake_id).first()
        )
        if not intake or (intake.lease_expires_at and intake.lease_expires_at > timezone.now()):
            return False
        intake.lease_expires_at = timezone.now() + timedelta(minutes=5)
        intake.save(update_fields=["lease_expires_at", "updated_at"])
        return True


def poll_job(job):
    from ai.batch import BatchClient
    from ai.client import ModelAPIError

    if not claim_poll(job):
        return
    provider = BatchClient.from_settings()
    try:
        current = BookIntake.objects.get(pk=job.intake_id)
        obsolete = current.generation != job.generation or current.status in {
            "cancelled",
            "confirmed",
        }
        if obsolete and job.status not in TERMINAL_BATCH | {"cancelling"}:
            response = provider.cancel(job.provider_batch_id)
        else:
            response = provider.retrieve(job.provider_batch_id)
        status = str(response.get("status", "")).lower()
        if not status:
            raise ModelAPIError("invalid_batch_response")
        job.status = status
        job.output_file_id, job.error_file_id = (
            response.get("output_file_id") or "",
            response.get("error_file_id") or "",
        )
        job.save()
        if status not in TERMINAL_BATCH or obsolete:
            return
        rows = []
        for file_id in [job.output_file_id, job.error_file_id]:
            if file_id:
                rows.extend(provider.results(file_id))
        by_id = {
            row["custom_id"]: row
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("custom_id"), str)
        }
        for request in job.requests:
            row = by_id.get(request["custom_id"], {})
            response_row = row.get("response") or {}
            try:
                if row.get("error") or response_row.get("status_code") != 200:
                    save_photo_result(
                        job.intake_id,
                        job.generation,
                        request["photo_id"],
                        problem=error("batch_item_failed", "这张照片未成功识别，可单独重试"),
                    )
                else:
                    extracted = parse_response(response_row["body"])
                    save_photo_result(
                        job.intake_id, job.generation, request["photo_id"], extraction=extracted
                    )
            except (ModelAPIError, ValueError, KeyError, TypeError):
                save_photo_result(
                    job.intake_id,
                    job.generation,
                    request["photo_id"],
                    problem=error("invalid_photo_result", "识别结果无效，可重拍或手工修正"),
                )
        finish(job.intake_id, job.generation)
    except ModelAPIError as exc:
        job.errors = [error(exc.code, exc.message)]
        # Terminal output retrieval remains retryable without repeating inference.
        if job.status in TERMINAL_BATCH:
            job.status = "retrieving_results"
        job.save()
    finally:
        BookIntake.objects.filter(pk=job.intake_id).update(lease_expires_at=None)


def run_once():
    counts = {"submitted_or_processed": 0, "polled": 0}
    for job in IntakeBatch.objects.exclude(
        status__in=TERMINAL_BATCH | {"submission_unknown", "submitting"}
    ).exclude(provider_batch_id="")[:100]:
        poll_job(job)
        counts["polled"] += 1
    for _ in range(20):
        intake = claim_next()
        if not intake:
            break
        if intake.mode == "realtime":
            process_realtime(intake)
        else:
            process_batch_submission(intake)
        counts["submitted_or_processed"] += 1
    return copy.copy(counts)
