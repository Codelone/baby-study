from django.db import migrations

# 图书列表.xlsx 中已按单本描述的行。套装和级别行不在此表内。
BOOKS = (
    (26, "I am a Bunny 点读版", "english", ""),
    (28, "What's In the Witch's Kitchen 女巫的厨房有什么？", "english", ""),
    (31, "鸭子骑车记", "chinese", ""),
    (32, "情绪小怪兽", "chinese", ""),
    (34, "大大的和小小的", "chinese", ""),
    (35, "小金鱼逃走了", "chinese", ""),
    (39, "猜猜我有多爱你", "chinese", ""),
    (42, "Who Belongs谁属于这里", "english", ""),
    (43, "Open the Barn Door", "english", ""),
    (44, "All change 惊喜派对", "english", ""),
    (45, "jobs we do", "english", ""),
    (47, "From Head to Toe", "english", ""),
    (48, "My First Book of Words", "english", ""),
    (52, "Good Night Gorilla", "english", ""),
    (54, "super hammy 1", "english", "super hammy"),
    (55, "super hammy 2", "english", "super hammy"),
    (62, "心智麦田 莫奈的猫", "english", ""),
    (64, "鼹鼠太太我回来了", "unknown", ""),
    (65, "三只小鲨鱼", "unknown", ""),
)


def seed_books(apps, schema_editor):
    Book = apps.get_model("catalog", "Book")
    for row, title, language, series_name in BOOKS:
        Book.objects.using(schema_editor.connection.alias).get_or_create(
            source_file="图书列表.xlsx",
            source_row=row,
            defaults={"title": title, "language": language, "series_name": series_name},
        )


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_initial")]

    operations = [migrations.RunPython(seed_books, migrations.RunPython.noop)]
