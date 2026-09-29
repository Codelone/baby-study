import re
from collections.abc import Mapping
from urllib.parse import urlparse

from rest_framework import serializers

from catalog.enrichment.importer import normalize_isbn
from catalog.models import Book, BookSeries, ShelfItem, SourceEvidence


class EvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceEvidence
        fields = (
            "field",
            "value",
            "source_url",
            "source_label",
            "origin_kind",
            "model_name",
            "status",
            "collected_at",
        )


class SeriesSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = BookSeries
        fields = ("id", "name", "cover_url", "expected_count", "completeness")


class BookSerializer(serializers.ModelSerializer):
    series_id = serializers.SerializerMethodField()
    series_name = serializers.SerializerMethodField()
    series_ids = serializers.SerializerMethodField()
    series = SeriesSummarySerializer(source="groups", many=True, read_only=True)
    is_on_shelf = serializers.SerializerMethodField()
    lexile_value = serializers.SerializerMethodField()
    lexile_source_status = serializers.SerializerMethodField()

    class Meta:
        model = Book
        fields = (
            "id",
            "title",
            "original_title",
            "aliases",
            "language",
            "series_id",
            "series_name",
            "series_ids",
            "series",
            "series_description",
            "isbn",
            "authors",
            "illustrators",
            "publisher",
            "edition",
            "cover_url",
            "cover_source_url",
            "description",
            "themes",
            "lexile_value",
            "lexile_source_status",
            "is_on_shelf",
            "catalog_status",
            "archived_at",
        )

    def primary(self, obj):
        groups = sorted(obj.groups.all(), key=lambda group: group.id)
        return next((g for g in groups if g.id == obj.series_id), groups[0] if groups else None)

    def get_series_id(self, obj):
        primary = self.primary(obj)
        return primary.id if primary else None

    def get_series_name(self, obj):
        primary = self.primary(obj)
        return primary.name if primary else ""

    def get_series_ids(self, obj):
        return sorted(group.id for group in obj.groups.all())

    def get_is_on_shelf(self, obj):
        return obj.id in self.context.get("owned_book_ids", set())

    def get_lexile_value(self, obj):
        claim = obj.reading_attributes.get("lexile", {})
        return claim.get("value") if claim.get("status") == "confirmed" else None

    def get_lexile_source_status(self, obj):
        return obj.reading_attributes.get("lexile", {}).get("status", "missing")


class BookDetailSerializer(BookSerializer):
    evidence = EvidenceSerializer(many=True, read_only=True)

    class Meta(BookSerializer.Meta):
        fields = BookSerializer.Meta.fields + (
            "translators",
            "publication_date",
            "binding",
            "pages",
            "reading_attributes",
            "evidence",
        )


class BookSeriesSerializer(serializers.ModelSerializer):
    books = serializers.SerializerMethodField()
    total_count = serializers.SerializerMethodField()
    owned_count = serializers.SerializerMethodField()
    completeness = serializers.SerializerMethodField()

    class Meta:
        model = BookSeries
        fields = (
            "id",
            "name",
            "description",
            "family",
            "aliases",
            "isbn",
            "edition",
            "publisher",
            "cover_url",
            "cover_source_url",
            "expected_count",
            "completeness",
            "books",
            "total_count",
            "owned_count",
        )

    def members(self, obj):
        positions = {member.book_id: member.position for member in obj.memberships.all()}
        return sorted(
            (book for book in obj.books.all() if book.catalog_status == "active"),
            key=lambda book: (positions.get(book.id, 0), book.id),
        )

    def get_books(self, obj):
        return BookSerializer(self.members(obj), many=True, context=self.context).data

    def get_total_count(self, obj):
        return len(self.members(obj))

    def get_owned_count(self, obj):
        owned = self.context.get("owned_book_ids", set())
        return sum(book.id in owned for book in self.members(obj))

    def get_completeness(self, obj):
        # A later manual edit must never leave a stale claim of complete membership.
        if obj.completeness == "complete" and obj.expected_count != len(self.members(obj)):
            return "partial" if obj.expected_count is not None else "unknown"
        return obj.completeness


class SeriesDetailSerializer(BookSeriesSerializer):
    evidence = EvidenceSerializer(many=True, read_only=True)

    class Meta(BookSeriesSerializer.Meta):
        fields = BookSeriesSerializer.Meta.fields + ("publication_date", "contents", "evidence")


class ShelfItemSerializer(serializers.ModelSerializer):
    book = serializers.SerializerMethodField()

    class Meta:
        model = ShelfItem
        fields = ("id", "book", "ownership_status", "reading_status", "updated_at")

    def get_book(self, obj):
        owned = {obj.book_id} if obj.ownership_status == ShelfItem.OwnershipStatus.OWNED else set()
        return BookSerializer(obj.book, context={"owned_book_ids": owned}).data


class ShelfUpdateSerializer(serializers.Serializer):
    ownership_status = serializers.ChoiceField(choices=ShelfItem.OwnershipStatus.choices)
    reading_status = serializers.ChoiceField(
        choices=ShelfItem.ReadingStatus.choices, allow_null=True
    )

    def validate(self, attrs):
        owned = attrs["ownership_status"] == ShelfItem.OwnershipStatus.OWNED
        if owned != (attrs["reading_status"] is not None):
            raise serializers.ValidationError(
                {"reading_status": "已拥有的图书需要阅读状态；未拥有或未知时应为 null"}
            )
        return attrs


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError(
                    {field: "不支持此字段" for field in sorted(unknown)}
                )
        return super().to_internal_value(data)


class TextField(serializers.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class PublicURLField(TextField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if value:
            serializers.URLField(max_length=1000).run_validation(value)
            if urlparse(value).scheme not in {"http", "https"}:
                raise serializers.ValidationError("出处必须是 HTTP 或 HTTPS 地址")
        return value


class PositiveCountField(serializers.IntegerField):
    def to_internal_value(self, data):
        if type(data) is not int:
            raise serializers.ValidationError("页数必须为正整数或 null")
        return super().to_internal_value(data)


class ExplicitBooleanField(serializers.BooleanField):
    def to_internal_value(self, data):
        if type(data) is not bool:
            raise serializers.ValidationError("请使用 true 或 false")
        return data


class BookCreateFieldsSerializer(StrictSerializer):
    title = TextField(max_length=255)
    original_title = TextField(max_length=255, required=False, allow_blank=True)
    language = serializers.ChoiceField(choices=Book.Language.choices, required=False)
    isbn = TextField(max_length=32, required=False, allow_blank=True)
    authors = serializers.ListField(child=TextField(max_length=255), required=False, max_length=100)
    illustrators = serializers.ListField(
        child=TextField(max_length=255), required=False, max_length=100
    )
    translators = serializers.ListField(
        child=TextField(max_length=255), required=False, max_length=100
    )
    publisher = TextField(max_length=255, required=False, allow_blank=True)
    publication_date = TextField(max_length=32, required=False, allow_blank=True)
    binding = TextField(max_length=100, required=False, allow_blank=True)
    pages = PositiveCountField(min_value=1, max_value=2147483647, required=False, allow_null=True)
    edition = TextField(max_length=500, required=False, allow_blank=True)
    description = TextField(required=False, allow_blank=True)
    themes = serializers.ListField(child=TextField(max_length=255), required=False, max_length=100)
    cover_url = PublicURLField(max_length=1000, required=False, allow_blank=True)
    cover_source_url = PublicURLField(max_length=1000, required=False, allow_blank=True)

    def validate_isbn(self, value):
        if not value:
            return ""
        compact = value.replace("-", "").replace(" ", "").upper()
        if not re.fullmatch(r"(?:[0-9]{9}[0-9X]|[0-9]{13})", compact):
            raise serializers.ValidationError("请输入有效的单本 ISBN-10 或 ISBN-13")
        try:
            return normalize_isbn(compact)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class BookCreateSerializer(StrictSerializer):
    fields = BookCreateFieldsSerializer()
    idempotency_key = TextField(max_length=255, required=False, allow_blank=True, default="")
    add_to_shelf = ExplicitBooleanField(required=False, default=False)


class BookEvidenceInputSerializer(StrictSerializer):
    field = serializers.ChoiceField(choices=tuple(BookCreateFieldsSerializer().fields))
    value = serializers.JSONField(allow_null=True)
    kind = serializers.ChoiceField(choices=("manual", "web", "photo"))
    source_url = PublicURLField(max_length=1000, required=False, allow_blank=True, default="")
    source_label = TextField(max_length=255, required=False, allow_blank=True, default="")
    model_name = TextField(max_length=100, required=False, allow_blank=True, default="")
    photo_id = serializers.UUIDField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        kind = attrs["kind"]
        if kind == "web" and not attrs["source_url"]:
            raise serializers.ValidationError({"source_url": "网页来源需要出处地址"})
        if kind == "photo" and not attrs["photo_id"]:
            raise serializers.ValidationError({"photo_id": "照片来源需要所属照片"})
        if kind != "photo" and attrs["photo_id"]:
            raise serializers.ValidationError({"photo_id": "此来源类型不能引用照片"})
        if kind != "web" and attrs["source_url"]:
            raise serializers.ValidationError({"source_url": "人工和照片来源不使用网页地址"})
        if kind == "manual" and attrs["model_name"]:
            raise serializers.ValidationError({"model_name": "人工来源不能声明模型提取"})
        return attrs
