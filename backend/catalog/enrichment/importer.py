"""Import only reviewed field claims. Never infer edition identity from a title."""

from urllib.parse import urlparse

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime

from catalog.models import (
    Book,
    BookSeries,
    CatalogImportRecord,
    SeriesMembership,
    ShelfItem,
    SourceEvidence,
)

BOOK_FIELDS = {
    "title",
    "original_title",
    "aliases",
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
    "cover_url",
    "cover_source_url",
    "description",
    "themes",
    "reading_attributes",
}
SERIES_FIELDS = {
    "name",
    "aliases",
    "isbn",
    "family",
    "publisher",
    "publication_date",
    "edition",
    "cover_url",
    "cover_source_url",
    "description",
    "contents",
    "expected_count",
    "completeness",
}
EDITION_FIELDS = {
    "isbn",
    "pages",
    "binding",
    "publisher",
    "publication_date",
    "edition",
    "cover_url",
    "cover_source_url",
    "expected_count",
    "completeness",
}
LIST_FIELDS = {"aliases", "authors", "illustrators", "translators", "themes"}
COUNT_FIELDS = {"pages", "expected_count"}


def normalize_isbn(value):
    value = str(value).replace("-", "").replace(" ", "").upper()
    if len(value) == 10:
        if not value[:9].isdigit() or (not value[-1].isdigit() and value[-1] != "X"):
            raise ValueError("ISBN-10 格式错误")
        digits = [int(v) for v in value[:9]] + [10 if value[-1] == "X" else int(value[-1])]
        if sum((10 - index) * n for index, n in enumerate(digits)) % 11:
            raise ValueError("ISBN-10 校验位错误")
        value = "978" + value[:9]
        value += str((-sum(int(n) * (1 if i % 2 == 0 else 3) for i, n in enumerate(value))) % 10)
    if len(value) != 13 or not value.isdigit() or not value.startswith(("978", "979")):
        raise ValueError("ISBN-13 格式错误")
    if sum(int(n) * (1 if i % 2 == 0 else 3) for i, n in enumerate(value)) % 10:
        raise ValueError("ISBN-13 校验位错误")
    return value


def checked_values(entity):
    kind = entity.get("kind")
    if kind not in ("book", "series") or not entity.get("identity_key"):
        raise ValueError("必须指定单本/套装及已核实版本来源标识")
    if entity.get("reader_kind") == "attachment":
        raise ValueError("附属材料不能作为儿童读物导入")
    values = {}
    for field, claim in entity.get("fields", {}).items():
        if field not in (BOOK_FIELDS if kind == "book" else SERIES_FIELDS):
            raise ValueError(f"不允许导入字段：{field}")
        if claim.get("status") != "confirmed":
            raise ValueError(f"字段尚未确认：{field}")
        source_url = urlparse(claim.get("source_url", ""))
        if source_url.scheme not in ("http", "https") or not source_url.hostname:
            raise ValueError(f"字段缺少公开出处：{field}")
        if not parse_datetime(claim.get("collected_at", "")):
            raise ValueError(f"字段缺少采集时间：{field}")
        if field in EDITION_FIELDS and not entity.get("edition_verified"):
            raise ValueError(f"具体版本尚未确认：{field}")
        value = claim["value"]
        if field == "isbn" and value:
            value = normalize_isbn(value)
        if field in LIST_FIELDS and (
            not isinstance(value, list) or any(not isinstance(item, str) for item in value)
        ):
            raise ValueError(f"字段必须是文本列表：{field}")
        if field in COUNT_FIELDS and value is not None and (type(value) is not int or value < 1):
            raise ValueError(f"册数/页数必须为正整数或空值：{field}")
        if field not in LIST_FIELDS | COUNT_FIELDS | {"reading_attributes"} and not isinstance(
            value, str
        ):
            raise ValueError(f"字段必须是文本：{field}")
        if field == "reading_attributes":
            if not isinstance(value, dict):
                raise ValueError("阅读属性必须保留具名声明和证据")
            for name, reading in value.items():
                if not isinstance(reading, dict) or reading.get("status") not in (
                    "confirmed",
                    "candidate",
                ):
                    raise ValueError(f"阅读属性缺少核验状态：{name}")
                reading_source = urlparse(reading.get("source_url", ""))
                if reading_source.scheme not in ("http", "https") or not reading_source.hostname:
                    raise ValueError(f"阅读属性缺少出处：{name}")
                if (
                    name
                    in {
                        "point_reading",
                        "lexile",
                        "native_level",
                        "publisher_age",
                        "distributor_age",
                    }
                    and reading["status"] == "confirmed"
                    and not entity.get("edition_verified")
                ):
                    raise ValueError(f"阅读声明须核实具体版本：{name}")
        values[field] = value
    return values


def resolve_entity(entity, record):
    model = Book if entity["kind"] == "book" else BookSeries
    obj = model.objects.filter(identity_key=entity["identity_key"]).first()
    if not obj and entity.get("existing_source_row"):
        if model is not Book:
            raise ValueError("旧行号只能映射现有单本")
        obj = Book.objects.get(
            source_file=record["source_file"], source_row=entity["existing_source_row"]
        )
        if obj.identity_key and obj.identity_key != entity["identity_key"]:
            raise ValueError("原始记录已绑定不同版本，需人工核对")
    if not obj:
        obj = model(identity_key=entity["identity_key"])
    return obj


def validate_entity(entity, record, parent_isbn=None):
    values = checked_values(entity)
    obj = resolve_entity(entity, record)
    if values.get("isbn") and values["isbn"] == parent_isbn:
        raise ValueError("不可将套装 ISBN 复制到分册")
    if values.get("isbn") and obj.isbn and obj.isbn != values["isbn"]:
        raise ValueError("已有 ISBN 与来源版本冲突")
    if values.get("isbn"):
        model = type(obj)
        clashes = model.objects.filter(isbn=values["isbn"]).exclude(pk=obj.pk)
        if clashes.exists():
            # Explicit equivalence is required, even when ISBNs match (e.g. overlays).
            match = clashes.filter(identity_key=entity.get("equivalent_identity")).first()
            if not match or not entity.get("edition_verified"):
                raise ValueError("ISBN 已存在另一来源标识；需明确同版本映射，禁止按书名合并")
            obj = match
    if not obj.pk and not values.get("title" if isinstance(obj, Book) else "name"):
        raise ValueError("新对象缺少已确认名称")
    for field, value in values.items():
        if field not in obj.manual_fields:
            setattr(obj, field, value)
    obj.full_clean(exclude=["identity_key"], validate_unique=False, validate_constraints=False)
    if isinstance(obj, Book) and obj.catalog_status == "legacy":
        raise ValueError("历史套装映射不得重新作为单本导入")
    members = entity.get("members", [])
    if entity["kind"] == "book" and members:
        raise ValueError("单本/合订本/杂志期刊不能包含套装成员")
    if isinstance(obj, BookSeries):
        incoming = {member["identity_key"] for member in members}
        if len(incoming) != len(members):
            raise ValueError("套装重复分册")
        existing = set(obj.books.values_list("identity_key", flat=True)) if obj.pk else set()
        if "memberships" in obj.manual_fields:
            members = []
            incoming = set()
        else:
            for member in members:
                child = resolve_entity(member, record)
                if "memberships" in child.manual_fields and member["identity_key"] not in existing:
                    incoming.discard(member["identity_key"])
        count = len(incoming | existing)
        if obj.completeness == "complete" and obj.expected_count != count:
            raise ValueError("完整性声明与已核实总册数不一致")
        if obj.expected_count is not None and count > obj.expected_count:
            raise ValueError("成员超出已核实总册数")
        for member in members:
            validate_entity(member, record, obj.isbn or None)
    return obj, values


def save_entity(entity, record):
    obj, values = validate_entity(entity, record)
    if not obj.identity_key:
        obj.identity_key = entity["identity_key"]
    obj.save()
    for field, value in values.items():
        if field in obj.manual_fields:
            continue
        source = entity["fields"][field]
        target = {"book": obj} if isinstance(obj, Book) else {"series": obj}
        # Update the same evidence claim on a repeated run; preserve other sources/manual evidence.
        SourceEvidence.objects.update_or_create(
            **target,
            field=field,
            source_url=source["source_url"],
            status="confirmed",
            defaults={
                "value": value,
                "source_label": source.get("source_label", ""),
                "collected_at": parse_datetime(source["collected_at"]),
            },
        )
    if isinstance(obj, BookSeries) and "memberships" not in obj.manual_fields:
        for index, member in enumerate(entity.get("members", []), 1):
            book = save_entity(member, record)
            if "memberships" not in book.manual_fields:
                SeriesMembership.objects.get_or_create(
                    series=obj, book=book, defaults={"position": member.get("position", index)}
                )
    return obj


def import_records(records, apply=False):
    outcomes = []
    for record in records:
        try:
            for entity in record["entities"]:
                validate_entity(entity, record)
            if apply:
                with transaction.atomic():
                    row, _ = CatalogImportRecord.objects.update_or_create(
                        source_file=record["source_file"],
                        sheet=record["sheet"],
                        row=record["row"],
                        defaults={
                            "raw": record["raw"],
                            "candidates": record["candidates"],
                            "issues": record["issues"],
                            "status": "imported" if record["entities"] else record["status"],
                        },
                    )
                    for entity in record["entities"]:
                        obj = save_entity(entity, record)
                        if entity.get("archive_source_row"):
                            old = Book.objects.get(
                                source_file=record["source_file"],
                                source_row=entity["archive_source_row"],
                            )
                            row.books.add(old)
                            if "catalog_status" in old.manual_fields:
                                row.issues.append("历史记录状态经人工修正并锁定，保留现状")
                                row.save(update_fields=["issues"])
                            elif ShelfItem.objects.filter(book=old).exists():
                                row.issues.append("历史疑似套装有书架引用，保留原记录待核对")
                                row.save(update_fields=["issues"])
                            else:
                                old.catalog_status = "legacy"
                                old.save(update_fields=["catalog_status"])
                                source = next(iter(entity["fields"].values()))
                                SourceEvidence.objects.update_or_create(
                                    book=old,
                                    field="catalog_status",
                                    source_url=source["source_url"],
                                    defaults={
                                        "value": "已核实为套装，保留历史ID和来源映射",
                                        "collected_at": parse_datetime(source["collected_at"]),
                                        "status": "confirmed",
                                    },
                                )
                        (row.books if isinstance(obj, Book) else row.series).add(obj)
                        if isinstance(obj, BookSeries):
                            row.books.add(*obj.books.all())
            outcomes.append(
                {
                    "row": record["row"],
                    "status": "imported" if apply and record["entities"] else record["status"],
                    "entities": len(record["entities"]),
                }
            )
        except (ValueError, ValidationError, IntegrityError, ObjectDoesNotExist) as exc:
            outcomes.append({"row": record["row"], "status": "rejected", "reason": str(exc)})
    return outcomes
