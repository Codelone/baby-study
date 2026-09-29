"""Manual series maintenance, using the catalog write lock and provenance rules."""

from django.db import transaction
from django.db.models import JSONField, Max, Value
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from catalog.models import Book, BookSeries, SeriesMembership, SourceEvidence
from catalog.serializers import BookCreateFieldsSerializer, SeriesFieldsSerializer
from catalog.services import (
    CatalogConflict,
    _canonical,
    _lock_catalog,
    _with_isbn,
    require_catalog_manager,
)


def validated_ids(value, field="series_ids"):
    if (
        not isinstance(value, list)
        or len(value) > 5000
        or any(type(pk) is not int or pk < 1 for pk in value)
        or len(value) != len(set(value))
    ):
        raise ValidationError({field: "请提交不重复的正整数 ID 列表（最多 5000 项）"})
    return value


def validate_series_ids(value):
    ids = validated_ids(value)
    if BookSeries.objects.filter(pk__in=ids).count() != len(ids):
        raise NotFound("所选系列不存在，请刷新后重新选择")
    return ids


def manual_claim(user, target, field, value):
    SourceEvidence.objects.create(
        **{"series" if isinstance(target, BookSeries) else "book": target},
        field=field,
        value=Value(None, output_field=JSONField()) if value is None else value,
        origin_kind="manual",
        source_label="管理员维护系列",
        confirmed_by=user,
        collected_at=timezone.now(),
    )


def protect_members(user, series, books, *, changed=True):
    series.manual_fields = sorted(
        set(series.manual_fields) | {"memberships", "expected_count", "completeness"}
    )
    if changed and series.completeness == "complete":
        series.completeness = "partial"
        manual_claim(user, series, "completeness", "partial")
    series.save(update_fields=("manual_fields", "completeness"))
    manual_claim(
        user,
        series,
        "memberships",
        list(series.memberships.order_by("position", "book_id").values_list("book_id", flat=True)),
    )
    for book in books:
        book.manual_fields = sorted(set(book.manual_fields) | {"memberships"})
        # Do not call Book.save(): its legacy primary FK may otherwise restore a removed link.
        Book.objects.filter(pk=book.pk).update(manual_fields=book.manual_fields)
        manual_claim(
            user, book, "memberships", list(book.groups.order_by("id").values_list("id", flat=True))
        )


def save_series(user, data, series_id=None):
    require_catalog_manager(user)
    serializer = SeriesFieldsSerializer(data=data, partial=series_id is not None)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    with transaction.atomic():
        _lock_catalog()
        series = BookSeries.objects.filter(pk=series_id).first() if series_id else BookSeries()
        if series is None:
            raise NotFound("系列不存在")
        if "name" in values:
            candidate = next(
                (
                    obj
                    for obj in BookSeries.objects.exclude(pk=series_id)
                    if _canonical(obj.name) == _canonical(values["name"])
                ),
                None,
            )
            if candidate:
                # Never merge different editions or silently overwrite existing metadata.
                raise ValidationError(
                    {
                        "name": (
                            f"已有同名系列：{candidate.name}（ID {candidate.pk}），请选择已有系列；"
                            "异版请补充级别或版本到名称"
                        )
                    }
                )
        isbn = values.get("isbn")
        if isbn and _with_isbn(Book.objects.all(), isbn).exists():
            raise ValidationError({"isbn": "此 ISBN 已用于单本，不能作为套装 ISBN 使用"})
        for field, value in values.items():
            setattr(series, field, value)
        count = series.books.filter(catalog_status="active").count() if series.pk else 0
        if series.completeness == "complete" and (
            series.expected_count is None or count != series.expected_count
        ):
            if values.get("completeness") == "complete":
                raise ValidationError(
                    {"completeness": "请先核实总册数与活动成员数一致，再标记完整"}
                )
            series.completeness = "partial"
            values["completeness"] = "partial"
        series.manual_fields = sorted(set(series.manual_fields) | set(values))
        series.save()
        for field, value in values.items():
            manual_claim(user, series, field, value)
        return series


def add_members(user, series_id, book_ids):
    require_catalog_manager(user)
    ids = validated_ids(book_ids, "book_ids")
    with transaction.atomic():
        _lock_catalog()
        try:
            series = BookSeries.objects.get(pk=series_id)
        except BookSeries.DoesNotExist as exc:
            raise NotFound("系列不存在") from exc
        books = {b.pk: b for b in Book.objects.filter(pk__in=ids, catalog_status="active")}
        if len(books) != len(ids):
            raise ValidationError({"book_ids": "只能关联存在的活动图书，请刷新书目"})
        existing = set(series.books.values_list("id", flat=True))
        position = series.memberships.aggregate(last=Max("position"))["last"] or 0
        added = []
        for pk in ids:
            book = books[pk]
            if (
                book.isbn
                and series.isbn
                and _with_isbn(
                    BookSeries.objects.filter(pk=series.pk),
                    BookCreateFieldsSerializer().validate_isbn(book.isbn),
                ).exists()
            ):
                raise ValidationError({"book_ids": "成员使用了套装 ISBN，请先核对单本 ISBN"})
            if pk not in existing:
                position += 1
                SeriesMembership.objects.create(series=series, book=book, position=position)
                added.append(book)
        if added:
            protect_members(user, series, added)
        return series


def associate_book(user, book, series_ids):
    ids = validate_series_ids(series_ids)
    for pk in ids:
        add_members(user, pk, [book.pk])


def remove_member(user, series_id, book_id):
    require_catalog_manager(user)
    with transaction.atomic():
        _lock_catalog()
        try:
            series = BookSeries.objects.get(pk=series_id)
        except BookSeries.DoesNotExist as exc:
            raise NotFound("系列不存在") from exc
        link = series.memberships.filter(book_id=book_id).select_related("book").first()
        if link:
            book = link.book
            link.delete()
            Book.objects.filter(pk=book_id, series_id=series_id).update(series=None)
            protect_members(user, series, [book])
        return series


def reorder_members(user, series_id, book_ids):
    require_catalog_manager(user)
    ids = validated_ids(book_ids, "book_ids")
    with transaction.atomic():
        _lock_catalog()
        try:
            series = BookSeries.objects.get(pk=series_id)
        except BookSeries.DoesNotExist as exc:
            raise NotFound("系列不存在") from exc
        members = {link.book_id: link for link in series.memberships.select_related("book")}
        if set(ids) != set(members):
            raise CatalogConflict("系列成员已变化，请刷新成员后重新排序", "series_members_conflict")
        for position, pk in enumerate(ids, 1):
            members[pk].position = position
        SeriesMembership.objects.bulk_update(members.values(), ["position"])
        protect_members(user, series, [link.book for link in members.values()], changed=False)
        return series
