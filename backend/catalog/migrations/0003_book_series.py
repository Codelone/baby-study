import django.db.models.deletion
from django.db import migrations, models


def group_books(apps, schema_editor):
    Book = apps.get_model("catalog", "Book")
    BookSeries = apps.get_model("catalog", "BookSeries")
    database = schema_editor.connection.alias
    names = (
        Book.objects.using(database)
        .exclude(series_name="")
        .order_by()
        .values_list("series_name", flat=True)
        .distinct()
    )
    for name in names:
        series, _created = BookSeries.objects.using(database).get_or_create(name=name)
        Book.objects.using(database).filter(series_name=name).update(series_id=series.id)


def restore_series_names(apps, schema_editor):
    Book = apps.get_model("catalog", "Book")
    database = schema_editor.connection.alias
    for book in Book.objects.using(database).select_related("series"):
        if book.series_id:
            Book.objects.using(database).filter(pk=book.pk).update(series_name=book.series.name)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0002_seed_single_books")]

    operations = [
        migrations.CreateModel(
            name="BookSeries",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "name",
                    models.CharField(
                        help_text="按具体套装、级别或版本命名，例如：某系列第一阶·点读版。",
                        max_length=255,
                        unique=True,
                        verbose_name="系列名称",
                    ),
                ),
                ("description", models.TextField(blank=True, verbose_name="系列说明")),
            ],
            options={
                "ordering": ("name", "id"),
                "verbose_name": "图书系列",
                "verbose_name_plural": "图书系列",
            },
        ),
        migrations.AddField(
            model_name="book",
            name="series",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="books",
                to="catalog.bookseries",
                verbose_name="所属系列",
            ),
        ),
        migrations.RunPython(group_books, restore_series_names),
        migrations.RemoveField(model_name="book", name="series_name"),
        migrations.AlterField(
            model_name="book",
            name="series_description",
            field=models.TextField(blank=True, verbose_name="历史系列说明"),
        ),
    ]
