from rest_framework import serializers

from catalog.models import Book, BookSeries, ShelfItem, SourceEvidence


class EvidenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = SourceEvidence
        fields = ("field", "value", "source_url", "source_label", "status", "collected_at")


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
        return sorted(obj.books.all(), key=lambda book: (positions.get(book.id, 0), book.id))

    def get_books(self, obj):
        return BookSerializer(self.members(obj), many=True, context=self.context).data

    def get_total_count(self, obj):
        return len(obj.books.all())

    def get_owned_count(self, obj):
        owned = self.context.get("owned_book_ids", set())
        return sum(book.id in owned for book in obj.books.all())

    def get_completeness(self, obj):
        # A later manual edit must never leave a stale claim of complete membership.
        if obj.completeness == "complete" and obj.expected_count != len(obj.books.all()):
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
