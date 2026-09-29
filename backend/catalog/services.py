"""Administrator catalog writes; confirmed existing books are never edited here."""

import hashlib
import json
import re
import unicodedata

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import JSONField, Value
from django.db.models.functions import Replace, Upper
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from catalog.models import Book, BookSeries, CatalogImportRecord, IntakePhoto, SourceEvidence
from catalog.serializers import BookCreateFieldsSerializer, BookEvidenceInputSerializer, TextField

VERSION_FIELDS = ("language", "publisher", "publication_date", "binding", "edition", "pages")
IDENTITY_FIELDS = ("title", "original_title", *VERSION_FIELDS, "authors", "translators")


class CatalogConflict(APIException):
    status_code = 409
    default_code = "book_conflict"

    def __init__(self, message, code="book_conflict"):
        self.default_code = code
        super().__init__(message, code=code)


def can_manage_catalog(user):
    return bool(user.is_authenticated and (user.is_staff or user.is_superuser))


def require_catalog_manager(user):
    if not can_manage_catalog(user):
        raise PermissionDenied("只有管理员可以管理系统书目")


def _lock_catalog():
    # ISBN is deliberately not unique in the legacy schema. Serialize these short
    # transactions across administrators, including inside an intake confirmation.
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [4, 2])


def _canonical(value):
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if isinstance(value, list):
        return sorted(_canonical(item) for item in value)
    return value


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def evidence_supports_value(claim, value, field=None):
    if claim == value:
        return True
    if field in {"cover_url", "cover_source_url", "isbn"}:
        return claim == value

    def normalized(item):
        if isinstance(item, str):
            return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", item).casefold()))
        return item

    if isinstance(claim, list) and isinstance(value, list):
        return bool(claim) and all(
            normalized(item) in [normalized(other) for other in value] for item in claim
        )
    return normalized(claim) == normalized(value)


def _isbn_variants(isbn):
    variants = [isbn]
    if isbn.startswith("978"):
        core = isbn[3:12]
        check = (-sum((10 - i) * int(n) for i, n in enumerate(core))) % 11
        variants.append(core + ("X" if check == 10 else str(check)))
    return variants


def _with_isbn(queryset, isbn):
    return queryset.annotate(
        compact_isbn=Upper(Replace(Replace("isbn", Value("-"), Value("")), Value(" "), Value("")))
    ).filter(compact_isbn__in=_isbn_variants(isbn))


def _assert_compatible(book, fields):
    if book.catalog_status != "active":
        raise CatalogConflict("此版本已归档或为历史映射，不能重新入库", "book_unavailable")
    if fields.get("isbn") and book.isbn:
        if not _with_isbn(Book.objects.filter(pk=book.pk), fields["isbn"]).exists():
            raise CatalogConflict("所选图书的 ISBN 与录入版本不同")
    names = [book.title, book.original_title, *book.aliases]
    if _canonical(fields["title"]) not in {_canonical(name) for name in names if name}:
        raise CatalogConflict("ISBN 或所选图书的书名存在冲突，请核对具体版本")
    for field in VERSION_FIELDS:
        old, new = getattr(book, field), fields.get(field)
        if field == "language" and Book.Language.UNKNOWN in (old, new):
            continue
        if old and new and _canonical(old) != _canonical(new):
            raise CatalogConflict(
                f"已有图书的{Book._meta.get_field(field).verbose_name}与录入版本冲突"
            )


def _edition_identity(fields):
    # A title alone never establishes edition equivalence. ISBN-less deduplication
    # needs publisher plus an explicit edition or publication date.
    if not fields.get("publisher") or not (fields.get("edition") or fields.get("publication_date")):
        return None
    defaults = {"language": Book.Language.UNKNOWN, "pages": None, "authors": [], "translators": []}
    return {
        field: _canonical(fields.get(field, defaults.get(field, ""))) for field in IDENTITY_FIELDS
    }


def _resolve_book(fields, existing_book_id):
    isbn = fields.get("isbn")
    if isbn and _with_isbn(BookSeries.objects.all(), isbn).exists():
        raise ValidationError({"isbn": "此 ISBN 属于套装，不能作为单本 ISBN 使用"})
    matches = list(_with_isbn(Book.objects.select_for_update(), isbn)) if isbn else []
    if existing_book_id is not None:
        if type(existing_book_id) is not int or existing_book_id < 1:
            raise ValidationError({"existing_book_id": "请选择有效的现有图书"})
        try:
            book = Book.objects.select_for_update().get(pk=existing_book_id)
        except Book.DoesNotExist as exc:
            raise NotFound("所选图书不存在") from exc
        if not book.isbn and any(match.pk != book.pk for match in matches):
            raise CatalogConflict("此 ISBN 还对应其他书目记录，请核对具体版本")
        _assert_compatible(book, fields)
        return book
    if matches:
        if len(matches) != 1:
            raise CatalogConflict("此 ISBN 对应多个书目记录，请先核对具体版本")
        _assert_compatible(matches[0], fields)
        return matches[0]
    identity = _edition_identity(fields)
    if identity:
        # Include older imports/manual entries whose identity keys use other schemes.
        candidates = Book.objects.select_for_update().filter(
            title__iexact=fields["title"]
        ) | Book.objects.select_for_update().filter(
            identity_key=f"catalog:edition:{_digest(identity)}"
        )
        matches = [
            book
            for book in candidates
            if not book.isbn
            and _edition_identity({field: getattr(book, field) for field in IDENTITY_FIELDS})
            == identity
        ]
        if len(matches) > 1:
            raise CatalogConflict("此版本对应多个书目记录，请先选择已有记录")
        if matches:
            _assert_compatible(matches[0], fields)
            return matches[0]
    return None


def _prepare_evidence(user, fields, explicit_fields, evidence):
    if evidence is None:
        evidence = []
    if not isinstance(evidence, list) or len(evidence) > 600:
        raise ValidationError({"evidence": "来源必须是列表，最多 600 条"})
    serializer = BookEvidenceInputSerializer(data=evidence, many=True)
    serializer.is_valid(raise_exception=True)
    claims = serializer.validated_data
    photo_ids = {claim["photo_id"] for claim in claims if claim["photo_id"]}
    photos = {
        photo.pk: photo for photo in IntakePhoto.objects.filter(pk__in=photo_ids, intake__user=user)
    }
    if photo_ids != set(photos):
        raise ValidationError({"evidence": "照片不存在或不属于当前管理员的录入任务"})
    covered = set()
    covered_values = {}
    manual_fields = set()
    prepared = []
    for claim in claims:
        field = claim["field"]
        if field not in explicit_fields:
            raise ValidationError({"evidence": f"来源字段未提交：{field}"})
        # Normalize using exactly the same field rules as the confirmed values.
        normalized_value = BookCreateFieldsSerializer().fields[field].run_validation(claim["value"])
        if field == "isbn":
            normalized_value = BookCreateFieldsSerializer().validate_isbn(normalized_value)
        if not evidence_supports_value(normalized_value, fields[field], field):
            raise ValidationError({"evidence": f"来源声明与确认值不一致：{field}"})
        if claim["kind"] == "manual":
            manual_fields.add(field)
        covered.add(field)
        if isinstance(normalized_value, list):
            covered_values.setdefault(field, []).extend(normalized_value)
        prepared.append(
            {
                "field": field,
                "value": normalized_value,
                "origin_kind": claim["kind"],
                "source_url": claim["source_url"],
                "source_label": claim["source_label"]
                or {"manual": "人工录入", "photo": "照片识别", "web": "网页来源"}[claim["kind"]],
                "model_name": claim["model_name"],
                "photo": photos.get(claim["photo_id"]),
            }
        )
    partly_covered = {
        field
        for field, values in covered_values.items()
        if any(not evidence_supports_value([value], values) for value in fields[field])
    }
    for field in sorted((explicit_fields - covered) | partly_covered):
        manual_fields.add(field)
        prepared.append(
            {
                "field": field,
                "value": fields[field],
                "origin_kind": "manual",
                "source_url": "",
                "source_label": "人工录入",
                "model_name": "",
                "photo": None,
            }
        )
    return prepared, sorted(manual_fields)


def create_book(
    user, fields, idempotency_key="", evidence=None, existing_book_id=None, series_ids=None
):
    from catalog.series_services import associate_book, validate_series_ids

    require_catalog_manager(user)
    serializer = BookCreateFieldsSerializer(data=fields)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    key = TextField(max_length=255, allow_blank=True).run_validation(idempotency_key)
    claims, manual_fields = _prepare_evidence(user, values, set(fields), evidence)
    request_data = {
        "fields": values,
        "existing_book_id": existing_book_id,
        "evidence": [
            {**claim, "photo": str(claim["photo"].pk) if claim["photo"] else None}
            for claim in claims
        ],
    }
    if series_ids is not None:
        request_data["series_ids"] = sorted(validate_series_ids(series_ids))
    with transaction.atomic():
        _lock_catalog()
        get_user_model().objects.select_for_update().get(pk=user.pk)
        record = None
        if key:
            # Reuse the import provenance ledger to retain request/result mappings
            # even when a new request resolves to a pre-existing, untouched Book.
            record, _ = CatalogImportRecord.objects.get_or_create(
                source_file=f"catalog-management:{user.pk}",
                sheet=_digest(key),
                row=1,
                defaults={"raw": request_data},
            )
            if record.raw != request_data:
                raise CatalogConflict(
                    "此请求标识已用于不同的录入内容，请重新核对", "idempotency_conflict"
                )
            book = record.books.first()
            if book:
                _assert_compatible(book, values)
                return book, False
        book = _resolve_book(values, existing_book_id)
        created = book is None
        if created:
            isbn = values.get("isbn")
            identity = _edition_identity(values)
            identity_key = (
                f"catalog:isbn:{isbn}"
                if isbn
                else (f"catalog:edition:{_digest(identity)}" if identity else None)
            )
            if identity_key and Book.objects.filter(identity_key=identity_key).exists():
                raise CatalogConflict("此版本标识已用于其他书目记录，请核对已有记录")
            book = Book.objects.create(
                **values, identity_key=identity_key, manual_fields=manual_fields
            )
            for claim in claims:
                stored_claim = {
                    **claim,
                    # JSON null is a valid unknown count; SQL NULL is disallowed.
                    "value": Value(None, output_field=JSONField())
                    if claim["value"] is None
                    else claim["value"],
                }
                defaults = {"collected_at": timezone.now()}
                if claim["origin_kind"] == "web":
                    # A repeated webpage claim must respect the existing unique
                    # key even if its human-readable label differs.
                    for field in ("source_label", "model_name"):
                        defaults[field] = stored_claim.pop(field)
                SourceEvidence.objects.get_or_create(
                    **stored_claim,
                    book=book,
                    confirmed_by=user,
                    status="confirmed",
                    defaults=defaults,
                )
        if series_ids is not None:
            associate_book(user, book, series_ids)
        if record:
            record.books.add(book)
            record.status = "confirmed"
            record.save(update_fields=("status", "updated_at"))
        return book, created


def archive_book(user, book_id):
    require_catalog_manager(user)
    with transaction.atomic():
        _lock_catalog()
        try:
            book = Book.objects.select_for_update().exclude(catalog_status="legacy").get(pk=book_id)
        except Book.DoesNotExist as exc:
            raise NotFound("图书不存在") from exc
        if book.catalog_status != "archived":
            book.catalog_status = "archived"
            book.archived_at = timezone.now()
            book.manual_fields = sorted(set(book.manual_fields) | {"catalog_status"})
            book.save(
                update_fields=("catalog_status", "archived_at", "manual_fields", "updated_at")
            )
        return book
