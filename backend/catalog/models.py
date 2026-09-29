import uuid

from django.conf import settings
from django.db import models


class BookSeries(models.Model):
    name = models.CharField(
        "系列名称",
        max_length=255,
        unique=True,
        help_text="按具体套装、级别或版本命名，例如：某系列第一阶·点读版。",
    )
    description = models.TextField("系列说明", blank=True)
    family = models.CharField("品牌/系列归属", max_length=255, blank=True)
    aliases = models.JSONField("别名", default=list, blank=True)
    isbn = models.CharField("套装 ISBN", max_length=13, blank=True)
    cover_url = models.URLField("套装封面", max_length=1000, blank=True)
    cover_source_url = models.URLField("封面出处", max_length=1000, blank=True)
    publisher = models.CharField("出版社", max_length=255, blank=True)
    publication_date = models.CharField("出版时间", max_length=32, blank=True)
    edition = models.CharField("版本说明", max_length=500, blank=True)
    contents = models.TextField("内容及附属材料", blank=True)
    expected_count = models.PositiveIntegerField("已核实读物总册数", null=True, blank=True)
    completeness = models.CharField(
        "成员完整性",
        max_length=16,
        choices=(("unknown", "待核实"), ("partial", "部分收录"), ("complete", "已完整核实")),
        default="unknown",
    )
    manual_fields = models.JSONField("禁止自动覆盖的字段", default=list, blank=True)
    identity_key = models.CharField(max_length=255, unique=True, null=True, blank=True)
    books = models.ManyToManyField("Book", through="SeriesMembership", related_name="groups")

    class Meta:
        ordering = ("name", "id")
        verbose_name = "图书系列"
        verbose_name_plural = "图书系列"

    def __str__(self):
        return self.name


class Book(models.Model):
    class Language(models.TextChoices):
        ENGLISH = "english", "英文"
        CHINESE = "chinese", "中文"
        BILINGUAL = "bilingual", "双语"
        UNKNOWN = "unknown", "未知"

    title = models.CharField("书名", max_length=255)
    language = models.CharField(
        "语言", max_length=16, choices=Language.choices, default=Language.UNKNOWN
    )
    series = models.ForeignKey(
        BookSeries,
        verbose_name="所属系列",
        related_name="legacy_books",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    series_description = models.TextField("历史系列说明", blank=True)
    source_file = models.CharField("来源文件", max_length=255, blank=True)
    source_row = models.PositiveIntegerField("来源行号", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    original_title = models.CharField("原文名", max_length=255, blank=True)
    aliases = models.JSONField("别名", default=list, blank=True)
    isbn = models.CharField("单本 ISBN", max_length=13, blank=True)
    authors = models.JSONField("作者", default=list, blank=True)
    illustrators = models.JSONField("绘者", default=list, blank=True)
    translators = models.JSONField("译者", default=list, blank=True)
    publisher = models.CharField("出版社", max_length=255, blank=True)
    publication_date = models.CharField("出版时间", max_length=32, blank=True)
    binding = models.CharField("装帧", max_length=100, blank=True)
    pages = models.PositiveIntegerField("页数", null=True, blank=True)
    edition = models.CharField("版本说明", max_length=500, blank=True)
    cover_url = models.URLField("封面", max_length=1000, blank=True)
    cover_source_url = models.URLField("封面出处", max_length=1000, blank=True)
    description = models.TextField("简介", blank=True)
    themes = models.JSONField("主题", default=list, blank=True)
    reading_attributes = models.JSONField("阅读属性及证据", default=dict, blank=True)
    identity_key = models.CharField(max_length=255, unique=True, null=True, blank=True)
    catalog_status = models.CharField(
        "记录状态",
        max_length=16,
        default="active",
        choices=(("active", "实际读物"), ("legacy", "历史导入映射"), ("archived", "已归档")),
    )
    archived_at = models.DateTimeField("归档时间", null=True, blank=True)
    manual_fields = models.JSONField("禁止自动覆盖的字段", default=list, blank=True)

    class Meta:
        ordering = ("title", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("source_file", "source_row"), name="unique_catalog_source_row"
            )
        ]

    def __str__(self):
        return self.title

    @property
    def series_name(self):
        groups = sorted(self.groups.all(), key=lambda group: group.id)
        primary = next((group for group in groups if group.id == self.series_id), None)
        if primary:
            return primary.name
        return groups[0].name if groups else ""

    def save(self, *args, **kwargs):
        changed_primary = self.series_id and (
            self._state.adding
            or type(self).objects.filter(pk=self.pk).values_list("series_id", flat=True).first()
            != self.series_id
        )
        super().save(*args, **kwargs)
        # Old integrations may still write the primary FK; all reads use memberships.
        if changed_primary:
            SeriesMembership.objects.get_or_create(series_id=self.series_id, book=self)


class SeriesMembership(models.Model):
    series = models.ForeignKey(BookSeries, on_delete=models.CASCADE, related_name="memberships")
    book = models.ForeignKey(Book, on_delete=models.PROTECT, related_name="memberships")
    position = models.PositiveIntegerField("分册顺序", default=0)

    class Meta:
        ordering = ("position", "book_id")
        constraints = [
            models.UniqueConstraint(fields=("series", "book"), name="unique_series_book")
        ]

    def __str__(self):
        return f"{self.series}: {self.book}"


class CatalogImportRecord(models.Model):
    source_file = models.CharField(max_length=255)
    sheet = models.CharField(max_length=255, default="Sheet1")
    row = models.PositiveIntegerField()
    raw = models.JSONField(default=dict)
    status = models.CharField(max_length=32, default="pending")
    candidates = models.JSONField(default=list, blank=True)
    issues = models.JSONField(default=list, blank=True)
    books = models.ManyToManyField(Book, blank=True, related_name="import_records")
    series = models.ManyToManyField(BookSeries, blank=True, related_name="import_records")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("source_file", "sheet", "row")
        constraints = [
            models.UniqueConstraint(
                fields=("source_file", "sheet", "row"), name="unique_import_row"
            )
        ]

    def __str__(self):
        return f"{self.source_file}:{self.sheet}:{self.row}"


class SourceEvidence(models.Model):
    book = models.ForeignKey(
        Book, null=True, blank=True, on_delete=models.CASCADE, related_name="evidence"
    )
    series = models.ForeignKey(
        BookSeries, null=True, blank=True, on_delete=models.CASCADE, related_name="evidence"
    )
    field = models.CharField("字段", max_length=100)
    value = models.JSONField("来源声明")
    source_url = models.URLField("出处", max_length=1000, blank=True)
    source_label = models.CharField(max_length=255, blank=True)
    origin_kind = models.CharField(
        max_length=16,
        choices=(("web", "网页来源"), ("manual", "人工录入"), ("photo", "照片识别")),
        default="web",
    )
    photo = models.ForeignKey("IntakePhoto", null=True, blank=True, on_delete=models.SET_NULL)
    model_name = models.CharField(max_length=100, blank=True)
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    status = models.CharField(
        max_length=16,
        default="confirmed",
        choices=(("confirmed", "已核实"), ("candidate", "待核对")),
    )
    collected_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("book", "field", "source_url", "status"),
                condition=models.Q(book__isnull=False, origin_kind="web"),
                name="unique_book_evidence_claim",
            ),
            models.UniqueConstraint(
                fields=("series", "field", "source_url", "status"),
                condition=models.Q(series__isnull=False, origin_kind="web"),
                name="unique_series_evidence_claim",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(book__isnull=False, series__isnull=True)
                    | models.Q(book__isnull=True, series__isnull=False)
                ),
                name="evidence_one_target",
            ),
        ]
        ordering = ("field", "id")


class BookIntake(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    mode = models.CharField(
        max_length=16, choices=(("batch", "省钱批量"), ("realtime", "实时")), default="batch"
    )
    status = models.CharField(max_length=32, default="queued")
    revision = models.PositiveIntegerField(default=1)
    generation = models.PositiveIntegerField(default=1)
    groups = models.JSONField(default=list, blank=True)
    unassigned_photo_ids = models.JSONField(default=list, blank=True)
    errors = models.JSONField(default=list, blank=True)
    progress = models.JSONField(default=dict, blank=True)
    confirmed_books = models.JSONField(default=list, blank=True)
    has_manual_edits = models.BooleanField(default=False)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)


def intake_photo_path(instance, filename):
    # UUID paths never expose a user supplied filename or create a public media URL.
    return f"intakes/{instance.intake_id}/{instance.id}.jpg"


class IntakePhoto(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intake = models.ForeignKey(BookIntake, on_delete=models.CASCADE, related_name="photos")
    file = models.FileField(upload_to=intake_photo_path)
    original_name = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=16, default="pending")
    extraction = models.JSONField(default=dict, blank=True)
    error = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")
        constraints = [
            models.UniqueConstraint(fields=("intake", "sha256"), name="unique_intake_photo_hash")
        ]


class IntakeBatch(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intake = models.ForeignKey(BookIntake, on_delete=models.CASCADE, related_name="batches")
    generation = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=32, default="submitting")
    input_file_id = models.CharField(max_length=255, blank=True)
    provider_batch_id = models.CharField(max_length=255, blank=True)
    output_file_id = models.CharField(max_length=255, blank=True)
    error_file_id = models.CharField(max_length=255, blank=True)
    requests = models.JSONField(default=list, blank=True)
    errors = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at",)


class ShelfItem(models.Model):
    class OwnershipStatus(models.TextChoices):
        OWNED = "owned", "已拥有"
        NOT_OWNED = "not_owned", "未拥有"
        UNKNOWN = "unknown", "未知"

    class ReadingStatus(models.TextChoices):
        UNREAD = "unread", "未读"
        READING = "reading", "近期在读"
        FAMILIAR = "familiar", "熟悉"
        PAUSED = "paused", "暂停"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    book = models.ForeignKey(Book, on_delete=models.PROTECT)
    ownership_status = models.CharField(
        max_length=16, choices=OwnershipStatus.choices, default=OwnershipStatus.UNKNOWN
    )
    reading_status = models.CharField(
        max_length=16, choices=ReadingStatus.choices, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("user", "book"), name="unique_user_book_shelf"),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        ownership_status="owned",
                        reading_status__in=("unread", "reading", "familiar", "paused"),
                    )
                    | models.Q(ownership_status="not_owned", reading_status__isnull=True)
                    | models.Q(ownership_status="unknown", reading_status__isnull=True)
                ),
                name="shelf_status_pair_valid",
            ),
        ]

    def __str__(self):
        return f"{self.user}: {self.book}"
