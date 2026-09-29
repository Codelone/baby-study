from django.contrib import admin

from catalog.models import Book, BookSeries, CatalogImportRecord, SeriesMembership, SourceEvidence


class MembershipInline(admin.TabularInline):
    model = SeriesMembership
    autocomplete_fields = ("book",)
    fields = ("book", "position")
    extra = 0


class BookMembershipInline(MembershipInline):
    autocomplete_fields = ("series",)
    fields = ("series", "position")


class EvidenceInline(admin.TabularInline):
    model = SourceEvidence
    extra = 0


class ManualProtectionAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        editable = set(form.changed_data) - {"manual_fields", "series", "source_row", "source_file"}
        obj.manual_fields = sorted(set(obj.manual_fields) | editable)
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        changed = [item for item in formset.forms if item.has_changed()]
        super().save_formset(request, form, formset, change)
        parent = form.instance
        protected = set(parent.manual_fields)
        if changed and formset.model is SeriesMembership:
            protected.add("memberships")
            if isinstance(parent, BookSeries):
                protected.update(("expected_count", "completeness"))
                if (
                    parent.completeness == "complete"
                    and parent.expected_count != parent.books.count()
                ):
                    parent.completeness = "partial"
        if formset.model is SourceEvidence:
            protected.update(
                item.instance.field
                for item in changed
                if item.instance.pk and item.instance.status == "confirmed"
            )
        parent.manual_fields = sorted(protected)
        parent.save()


@admin.register(BookSeries)
class BookSeriesAdmin(ManualProtectionAdmin):
    list_display = ("name", "expected_count", "completeness", "publisher")
    list_filter = ("completeness",)
    search_fields = ("name", "aliases", "isbn", "family")
    inlines = (MembershipInline, EvidenceInline)
    exclude = ("books",)
    readonly_fields = ("identity_key",)


@admin.register(Book)
class BookAdmin(ManualProtectionAdmin):
    list_display = ("title", "language", "isbn", "publisher", "catalog_status")
    list_filter = ("language", "catalog_status", "groups")
    search_fields = ("title", "original_title", "aliases", "isbn", "groups__name")
    readonly_fields = ("series", "series_description", "source_file", "source_row", "identity_key")
    inlines = (BookMembershipInline, EvidenceInline)


@admin.register(CatalogImportRecord)
class ImportRecordAdmin(admin.ModelAdmin):
    list_display = ("source_file", "sheet", "row", "status")
    list_filter = ("status",)
    search_fields = ("raw", "candidates", "issues")
    readonly_fields = ("source_file", "sheet", "row", "raw", "updated_at")
    autocomplete_fields = ("books", "series")


@admin.register(SourceEvidence)
class SourceEvidenceAdmin(admin.ModelAdmin):
    list_display = ("book", "series", "field", "status", "source_label", "collected_at")
    list_filter = ("status", "field")
    search_fields = ("source_url", "source_label", "book__title", "series__name")
    autocomplete_fields = ("book", "series")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if change and obj.status == "confirmed":
            target = obj.book or obj.series
            target.manual_fields = sorted(set(target.manual_fields) | {obj.field})
            target.save(update_fields=["manual_fields"])
