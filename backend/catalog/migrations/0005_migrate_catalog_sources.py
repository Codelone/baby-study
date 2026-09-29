from django.db import migrations


def migrate_sources(apps, schema_editor):
    Book = apps.get_model("catalog", "Book")
    Member = apps.get_model("catalog", "SeriesMembership")
    Record = apps.get_model("catalog", "CatalogImportRecord")
    for book in Book.objects.order_by("id"):
        if book.series_id:
            Member.objects.get_or_create(series_id=book.series_id, book_id=book.id)
        if book.source_file and book.source_row:
            record, _ = Record.objects.get_or_create(
                source_file=book.source_file,
                sheet="Sheet1",
                row=book.source_row,
                defaults={"raw": {"书本名称": book.title}, "status": "legacy-mapped"},
            )
            record.books.add(book)
            if book.series_id:
                record.series.add(book.series_id)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0004_catalog_metadata")]
    operations = [migrations.RunPython(migrate_sources, migrations.RunPython.noop)]
