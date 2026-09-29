import copy
import hashlib
import io
import json
import re
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from catalog.models import BookIntake, IntakePhoto, ShelfItem


class RevisionConflict(APIException):
    status_code = 409
    default_code = "revision_conflict"
    default_detail = "资料已更新，请刷新任务后再操作"


class IntakeStateConflict(APIException):
    status_code = 409
    default_code = "intake_state_conflict"
    default_detail = "任务当前状态不支持此操作"


def require_admin(request):
    if not (request.user.is_staff or request.user.is_superuser):
        raise PermissionDenied("只有管理员可以管理系统图书")


def owned_intake(request, intake_id, *, lock=False):
    require_admin(request)
    queryset = BookIntake.objects.filter(user=request.user)
    if lock:
        queryset = queryset.select_for_update()
    return get_object_or_404(queryset, pk=intake_id)


def check_revision(intake, data):
    revision = data.get("revision")
    if type(revision) is not int or revision != intake.revision:
        raise RevisionConflict()


def serialize_intake(intake):
    photos = []
    for photo in intake.photos.all():
        photos.append(
            {
                "id": str(photo.id),
                "original_name": photo.original_name,
                "status": photo.status,
                "extraction": photo.extraction,
                "error": photo.error,
                "url": reverse(
                    "intake-photo", kwargs={"intake_id": intake.id, "photo_id": photo.id}
                ),
            }
        )
    return {
        "id": str(intake.id),
        "mode": intake.mode,
        "status": intake.status,
        "revision": intake.revision,
        "generation": intake.generation,
        "groups": intake.groups,
        "unassigned_photo_ids": intake.unassigned_photo_ids,
        "errors": intake.errors,
        "progress": intake.progress,
        "confirmed_books": intake.confirmed_books,
        "photos": photos,
        "created_at": intake.created_at.isoformat(),
        "updated_at": intake.updated_at.isoformat(),
    }


def normalized_photo(upload):
    if upload.size > settings.INTAKE_MAX_PHOTO_BYTES:
        raise serializers.ValidationError({"files": "照片过大，请压缩后上传"})
    raw = upload.read(settings.INTAKE_MAX_PHOTO_BYTES + 1)
    if len(raw) > settings.INTAKE_MAX_PHOTO_BYTES:
        raise serializers.ValidationError({"files": "照片过大，请压缩后上传"})
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise serializers.ValidationError({"files": "请上传 JPEG、PNG 或 WebP 照片"})
            if image.width * image.height > 25_000_000:
                raise serializers.ValidationError({"files": "照片像素过大，请缩小后上传"})
            image.load()
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((2400, 2400))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=90)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise serializers.ValidationError({"files": "照片内容无效，请重新选择"}) from None


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def book_intakes(request):
    require_admin(request)
    if request.method == "GET":
        intakes = BookIntake.objects.filter(user=request.user).prefetch_related("photos")[:100]
        return Response({"intakes": [serialize_intake(intake) for intake in intakes]})
    mode = request.data.get("mode", "batch")
    if mode not in {"batch", "realtime"}:
        raise serializers.ValidationError({"mode": "请选择省钱批量或实时模式"})
    uploads = request.FILES.getlist("files")
    if not uploads or len(uploads) > settings.INTAKE_MAX_PHOTOS:
        raise serializers.ValidationError(
            {"files": f"请上传 1 至 {settings.INTAKE_MAX_PHOTOS} 张照片"}
        )
    prepared = []
    seen = set()
    for upload in uploads:
        raw = normalized_photo(upload)
        digest = hashlib.sha256(raw).hexdigest()
        if digest not in seen:
            seen.add(digest)
            name = re.sub(r"[\x00-\x1f\x7f]", "", Path(upload.name).name)[:255]
            prepared.append((raw, digest, name))
    saved_files = []
    try:
        with transaction.atomic():
            intake = BookIntake.objects.create(
                user=request.user,
                mode=mode,
                progress={"total": len(prepared), "completed": 0, "failed": 0},
            )
            for raw, digest, name in prepared:
                photo = IntakePhoto(intake=intake, sha256=digest, original_name=name)
                photo.file.save("photo.jpg", ContentFile(raw), save=False)
                saved_files.append(photo.file)
                photo.save()
    except Exception:
        for field_file in saved_files:
            field_file.delete(save=False)
        raise
    return Response({"intake": serialize_intake(intake)}, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def intake_detail(request, intake_id):
    return Response({"intake": serialize_intake(owned_intake(request, intake_id))})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def intake_photo(request, intake_id, photo_id):
    intake = owned_intake(request, intake_id)
    photo = get_object_or_404(IntakePhoto, intake=intake, pk=photo_id)
    if not photo.file or not photo.file.storage.exists(photo.file.name):
        from rest_framework.exceptions import NotFound

        raise NotFound("照片已过期清理")
    response = FileResponse(photo.file.open("rb"), content_type="image/jpeg")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def normalized_groups(intake, data):
    groups = data.get("groups")
    unassigned = data.get("unassigned_photo_ids", [])
    if not isinstance(groups, list) or not isinstance(unassigned, list):
        raise serializers.ValidationError({"groups": "分组格式无效"})
    if len(groups) > settings.INTAKE_MAX_PHOTOS:
        raise serializers.ValidationError({"groups": "分组数量超过限制"})
    allowed_ids = set(str(photo.id) for photo in intake.photos.all())
    seen_ids, seen_groups = set(), set()
    result = []
    previous = {group["id"]: group for group in intake.groups}
    allowed_fields = {
        "title",
        "original_title",
        "language",
        "isbn",
        "authors",
        "illustrators",
        "translators",
        "publisher",
        "publication_date",
        "binding",
        "pages",
        "edition",
        "description",
        "themes",
        "cover_url",
        "cover_source_url",
    }
    for group in groups:
        if not isinstance(group, dict):
            raise serializers.ValidationError({"groups": "分组格式无效"})
        group_id, photo_ids, fields = group.get("id"), group.get("photo_ids"), group.get("fields")
        if not isinstance(group_id, str) or not group_id or len(group_id) > 100:
            raise serializers.ValidationError({"groups": "分组 ID 无效"})
        if group_id in seen_groups or not isinstance(photo_ids, list) or not photo_ids:
            raise serializers.ValidationError({"groups": "分组重复或缺少照片"})
        if not isinstance(fields, dict) or set(fields) - allowed_fields:
            raise serializers.ValidationError({"groups": "资料字段无效"})
        if len(json.dumps(fields, ensure_ascii=False)) > 20000:
            raise serializers.ValidationError({"groups": "资料内容过长"})
        for photo_id in photo_ids:
            if not isinstance(photo_id, str) or photo_id not in allowed_ids or photo_id in seen_ids:
                raise serializers.ValidationError({"groups": "照片不属于任务或被重复分组"})
            seen_ids.add(photo_id)
        seen_groups.add(group_id)
        old = previous.get(group_id, {})
        claims = old.get("evidence", [])
        candidates = old.get("enrichment_candidates", [])
        web_claims = [claim for candidate in candidates for claim in candidate.get("evidence", [])]
        evidence = []
        proposed = group.get("evidence", [])
        if not isinstance(proposed, list):
            raise serializers.ValidationError({"groups": "资料来源格式无效"})
        for field, value in fields.items():
            matching = [
                claim
                for claim in claims
                if claim.get("field") == field and claim.get("value") == value
            ]
            matching += [
                claim
                for claim in proposed
                if claim in web_claims
                and claim.get("field") == field
                and claim.get("value") == value
            ]
            matching = [
                claim
                for claim in matching
                if claim.get("kind") != "photo" or str(claim.get("photo_id")) in photo_ids
            ]
            if matching:
                evidence.extend(matching)
            elif value not in (None, "", []):
                evidence.append(
                    {"field": field, "value": value, "kind": "manual", "source_label": "管理员修正"}
                )
        existing_id = group.get("existing_book_id")
        if existing_id is not None and (type(existing_id) is not int or existing_id <= 0):
            raise serializers.ValidationError({"groups": "已有图书 ID 无效"})
        result.append(
            {
                "id": group_id,
                "photo_ids": photo_ids,
                "fields": fields,
                "evidence": evidence,
                "issues": old.get("issues", []),
                "existing_book_id": existing_id,
                "selected": bool(group.get("selected", True)),
                "enrichment_candidates": candidates,
            }
        )
    for photo_id in unassigned:
        if not isinstance(photo_id, str) or photo_id not in allowed_ids or photo_id in seen_ids:
            raise serializers.ValidationError({"groups": "未归属照片无效或重复"})
        seen_ids.add(photo_id)
    if seen_ids != allowed_ids:
        raise serializers.ValidationError({"groups": "请保留全部照片的分组或未归属记录"})
    return result, unassigned


@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def intake_groups(request, intake_id):
    with transaction.atomic():
        intake = owned_intake(request, intake_id, lock=True)
        check_revision(intake, request.data)
        if intake.status not in {"awaiting_confirmation", "partial_failure", "failed"}:
            raise IntakeStateConflict()
        intake.groups, intake.unassigned_photo_ids = normalized_groups(intake, request.data)
        intake.has_manual_edits = True
        intake.revision += 1
        intake.save()
    return Response({"intake": serialize_intake(intake)})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def intake_enrichment(request, intake_id):
    from catalog.intake.enrichment import search_candidates

    intake = owned_intake(request, intake_id)
    check_revision(intake, request.data)
    if intake.status not in {"awaiting_confirmation", "partial_failure", "failed"}:
        raise IntakeStateConflict()
    ids = request.data.get("group_ids")
    if not isinstance(ids, list) or not ids or len(ids) > 10:
        raise serializers.ValidationError({"group_ids": "请选择 1 至 10 本书补全"})
    groups = {group["id"]: group for group in intake.groups}
    if any(not isinstance(group_id, str) or group_id not in groups for group_id in ids):
        raise serializers.ValidationError({"group_ids": "请选择有效分组"})
    candidates = {group_id: search_candidates(groups[group_id]["fields"]) for group_id in ids}
    with transaction.atomic():
        current = owned_intake(request, intake_id, lock=True)
        check_revision(current, request.data)
        updated = copy.deepcopy(current.groups)
        for group in updated:
            if group["id"] in candidates:
                group["enrichment_candidates"] = candidates[group["id"]].get("candidates", [])
        current.groups = updated
        current.save()
    return Response({"intake": serialize_intake(current), "candidates": candidates})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def intake_confirm(request, intake_id):
    from catalog.services import create_book

    ids = request.data.get("group_ids")
    add_to_shelf = request.data.get("add_to_shelf", False)
    if not isinstance(ids, list) or not ids or any(not isinstance(value, str) for value in ids):
        raise serializers.ValidationError({"group_ids": "请选择要确认入库的图书"})
    if type(add_to_shelf) is not bool or len(set(ids)) != len(ids):
        raise serializers.ValidationError({"group_ids": "确认参数无效"})
    with transaction.atomic():
        intake = owned_intake(request, intake_id, lock=True)
        if intake.status == "confirmed":
            if set(ids) != {row["group_id"] for row in intake.confirmed_books}:
                raise IntakeStateConflict("任务已确认，不能更换入库内容")
            return Response({"intake": serialize_intake(intake), "books": intake.confirmed_books})
        check_revision(intake, request.data)
        if intake.status not in {"awaiting_confirmation", "partial_failure", "failed"}:
            raise IntakeStateConflict()
        groups = {group["id"]: group for group in intake.groups}
        if any(group_id not in groups for group_id in ids):
            raise serializers.ValidationError({"group_ids": "分组不存在"})
        result = []
        for group_id in ids:
            group = groups[group_id]
            book, created = create_book(
                request.user,
                group["fields"],
                idempotency_key=f"intake:{intake.id}:{group_id}",
                evidence=group.get("evidence", []),
                existing_book_id=group.get("existing_book_id"),
            )
            if add_to_shelf:
                item, _ = ShelfItem.objects.get_or_create(
                    user=request.user,
                    book=book,
                    defaults={
                        "ownership_status": "owned",
                        "reading_status": "unread",
                    },
                )
                if item.ownership_status != "owned":
                    item.ownership_status, item.reading_status = "owned", "unread"
                    item.save()
            result.append({"group_id": group_id, "book_id": book.id, "created": created})
        intake.confirmed_books, intake.status = result, "confirmed"
        intake.revision += 1
        intake.save()
    return Response({"intake": serialize_intake(intake), "books": result})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def intake_cancel(request, intake_id):
    with transaction.atomic():
        intake = owned_intake(request, intake_id, lock=True)
        check_revision(intake, request.data)
        if intake.status == "confirmed":
            raise IntakeStateConflict()
        intake.status = "cancelled"
        intake.generation += 1
        intake.revision += 1
        intake.lease_expires_at = None
        intake.save()
    # The worker reconciles/cancels provider batches; a cancelled local draft never auto-saves.
    return Response({"intake": serialize_intake(intake)})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def intake_retry(request, intake_id):
    with transaction.atomic():
        intake = owned_intake(request, intake_id, lock=True)
        check_revision(intake, request.data)
        if intake.status not in {"partial_failure", "failed"}:
            raise IntakeStateConflict()
        if intake.batches.filter(status__in=["submitting", "submission_unknown"]).exists():
            raise IntakeStateConflict("提交状态不确定，请先在 MiMo 控制台核对，避免重复计费")
        failed = intake.photos.filter(status="failed")
        if not failed.exists():
            raise IntakeStateConflict("没有需要重试的失败照片")
        failed.update(status="pending", error={})
        intake.generation += 1
        intake.revision += 1
        intake.status, intake.errors, intake.lease_expires_at = "queued", [], None
        intake.save()
    return Response({"intake": serialize_intake(intake)})
