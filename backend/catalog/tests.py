import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from catalog.models import Book, BookSeries, ShelfItem


class CatalogTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="parent", password="test-password"
        )

    def test_initial_catalog_has_only_selected_single_books(self):
        seeded = Book.objects.filter(source_file="图书列表.xlsx")
        self.assertEqual(seeded.count(), 19)
        self.assertEqual(
            set(seeded.values_list("source_row", flat=True)),
            {26, 28, 31, 32, 34, 35, 39, 42, 43, 44, 45, 47, 48, 52, 54, 55, 62, 64, 65},
        )
        self.assertFalse(seeded.filter(title__contains="套装").exists())
        self.assertEqual(seeded.get(source_row=64).language, Book.Language.UNKNOWN)
        self.assertFalse(ShelfItem.objects.exists())
        series = BookSeries.objects.get(name="super hammy")
        self.assertEqual(list(series.books.values_list("source_row", flat=True)), [54, 55])

    def test_catalog_requires_login_and_searches_title_or_series(self):
        anonymous = self.client.get(reverse("books"))
        self.assertEqual(anonymous.status_code, 403)
        self.assertEqual(anonymous.json()["error"]["code"], "not_authenticated")
        book = Book.objects.get(source_row=26)
        anonymous_write = self.client.put(
            reverse("shelf-book", args=[book.id]),
            data=json.dumps({"ownership_status": "owned", "reading_status": "unread"}),
            content_type="application/json",
        )
        self.assertEqual(anonymous_write.status_code, 403)
        self.assertFalse(ShelfItem.objects.exists())

        self.client.force_login(self.user)
        by_title = self.client.get(reverse("books"), {"q": "Bunny"})
        self.assertEqual(len(by_title.json()["books"]), 1)
        self.assertIsNone(by_title.json()["books"][0]["lexile_value"])
        by_series = self.client.get(reverse("books"), {"q": "super hammy"})
        self.assertEqual(len(by_series.json()["books"]), 2)
        self.assertEqual(by_series.json()["series"][0]["total_count"], 2)
        self.assertEqual(by_series.json()["series"][0]["owned_count"], 0)
        chinese = self.client.get(reverse("books"), {"language": "chinese"})
        self.assertEqual(len(chinese.json()["books"]), 5)
        invalid = self.client.get(reverse("books"), {"language": "invalid"})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["error"]["code"], "invalid_input")

    def test_shelf_add_update_remove_and_user_isolation(self):
        self.client.force_login(self.user)
        book = Book.objects.get(source_row=26)
        url = reverse("shelf-book", args=[book.id])

        def put(ownership, reading):
            return self.client.put(
                url,
                data=json.dumps({"ownership_status": ownership, "reading_status": reading}),
                content_type="application/json",
            )

        self.assertEqual(put("owned", "unread").status_code, 200)
        self.assertEqual(put("owned", "unread").status_code, 200)
        self.assertEqual(ShelfItem.objects.filter(user=self.user, book=book).count(), 1)
        self.assertEqual(put("owned", "reading").json()["reading_status"], "reading")
        shelf_items = self.client.get(reverse("shelf")).json()["items"]
        self.assertEqual(len(shelf_items), 1)
        self.assertTrue(shelf_items[0]["book"]["is_on_shelf"])
        self.assertTrue(
            self.client.get(reverse("books"), {"q": "Bunny"}).json()["books"][0]["is_on_shelf"]
        )

        another = get_user_model().objects.create_user(username="another", password="test-password")
        self.client.force_login(another)
        self.assertEqual(self.client.get(reverse("shelf")).json()["items"], [])
        self.client.force_login(self.user)

        self.assertEqual(put("not_owned", None).status_code, 200)
        self.assertEqual(self.client.get(reverse("shelf")).json()["items"], [])
        self.assertEqual(ShelfItem.objects.get(user=self.user, book=book).reading_status, None)

    def test_shelf_rejects_invalid_status_and_missing_book(self):
        self.client.force_login(self.user)
        book = Book.objects.get(source_row=26)
        url = reverse("shelf-book", args=[book.id])
        invalid = self.client.put(
            url,
            data=json.dumps({"ownership_status": "owned", "reading_status": None}),
            content_type="application/json",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("details", invalid.json()["error"])
        missing = self.client.put(
            reverse("shelf-book", args=[999999]),
            data=json.dumps({"ownership_status": "owned", "reading_status": "unread"}),
            content_type="application/json",
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "not_found")

    def test_shelf_write_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        book = Book.objects.get(source_row=26)
        url = reverse("shelf-book", args=[book.id])
        body = json.dumps({"ownership_status": "owned", "reading_status": "unread"})
        self.assertEqual(client.put(url, body, content_type="application/json").status_code, 403)
        client.get("/admin/login/")
        token = client.cookies["csrftoken"].value
        accepted = client.put(url, body, content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(accepted.status_code, 200)

    def test_only_staff_can_add_books_in_admin(self):
        add_url = reverse("admin:catalog_book_add")
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(add_url).status_code, 302)
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        self.client.force_login(self.user)
        series = BookSeries.objects.create(name="示例系列第一阶·点读版", description="系列说明")
        response = self.client.post(
            add_url,
            {
                "title": "系列中的一本",
                "language": "bilingual",
                "catalog_status": "active",
                "memberships-TOTAL_FORMS": "1",
                "memberships-INITIAL_FORMS": "0",
                "memberships-0-series": series.id,
                "memberships-0-position": "1",
                "evidence-TOTAL_FORMS": "0",
                "evidence-INITIAL_FORMS": "0",
                "source_file": "",
                "source_row": "",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        book = Book.objects.get(title="系列中的一本")
        self.assertEqual(list(book.groups.all()), [series])
        self.assertEqual(book.series_name, series.name)
        self.assertEqual(book.groups.get().description, "系列说明")


class SeriesTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="series-parent")
        self.client.force_login(self.user)
        self.series = BookSeries.objects.create(
            name="测试系列第一阶·点读版", description="分组说明"
        )
        self.first = Book.objects.create(
            title="Apple Volume", language="english", series=self.series
        )
        self.second = Book.objects.create(title="中文分册", language="chinese", series=self.series)
        self.url = reverse("shelf-series", args=[self.series.id])

    def add_series(self, **kwargs):
        return self.client.post(self.url, {}, content_type="application/json", **kwargs)

    def test_search_returns_complete_series_members_and_user_counts(self):
        ShelfItem.objects.create(
            user=self.user, book=self.second, ownership_status="owned", reading_status="familiar"
        )
        for query in ("测试系列", "apple"):
            with self.subTest(query=query):
                response = self.client.get(reverse("books"), {"q": query, "language": "english"})
                data = response.json()
                self.assertEqual([book["id"] for book in data["books"]], [self.first.id])
                self.assertEqual(data["books"][0]["series_id"], self.series.id)
                self.assertEqual(data["books"][0]["series_name"], self.series.name)
                group = data["series"][0]
                self.assertEqual(group["description"], "分组说明")
                self.assertEqual(group["total_count"], 2)
                self.assertEqual(group["owned_count"], 1)
                self.assertEqual(
                    {book["id"] for book in group["books"]}, {self.first.id, self.second.id}
                )
                flags = {book["id"]: book["is_on_shelf"] for book in group["books"]}
                self.assertEqual(flags, {self.first.id: False, self.second.id: True})

        standalone = self.client.get(reverse("books"), {"q": "Bunny"}).json()
        self.assertEqual(standalone["series"], [])
        self.assertIsNone(standalone["books"][0]["series_id"])
        missing = self.client.get(reverse("books"), {"q": "no match"}).json()
        self.assertEqual(missing, {"books": [], "series": []})
        no_language = self.client.get(
            reverse("books"), {"q": "测试系列", "language": "bilingual"}
        ).json()
        self.assertEqual(no_language, {"books": [], "series": []})
        self.client.force_login(get_user_model().objects.create_user(username="series-other"))
        group = self.client.get(reverse("books"), {"q": "测试系列"}).json()["series"][0]
        self.assertEqual(group["owned_count"], 0)
        self.assertFalse(any(book["is_on_shelf"] for book in group["books"]))

    def test_catalog_query_count_is_independent_of_series_members(self):
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            self.client.get(reverse("books"), {"q": "测试系列"})
        count = len(queries)
        self.assertLessEqual(count, 10)
        for number in range(8):
            Book.objects.create(title=f"额外分册 {number}", series=self.series)
        with self.assertNumQueries(count):
            response = self.client.get(reverse("books"), {"q": "测试系列"})
        self.assertEqual(response.json()["series"][0]["total_count"], 10)

    def test_first_and_repeated_add_is_idempotent_and_preserves_timestamps(self):
        self.assertEqual(
            self.add_series().json(),
            {"added_count": 2, "already_owned_count": 0, "total_count": 2},
        )
        before = list(ShelfItem.objects.filter(user=self.user).values())
        self.assertEqual(
            self.add_series().json(),
            {"added_count": 0, "already_owned_count": 2, "total_count": 2},
        )
        self.assertEqual(list(ShelfItem.objects.filter(user=self.user).values()), before)
        items = self.client.get(reverse("shelf")).json()["items"]
        self.assertEqual(len(items), 2)
        self.assertTrue(all(item["reading_status"] == "unread" for item in items))
        self.assertTrue(all(item["book"]["series_id"] == self.series.id for item in items))

    def test_partial_removed_and_unknown_members_are_added_without_resetting_owned_books(self):
        owned = ShelfItem.objects.create(
            user=self.user, book=self.first, ownership_status="owned", reading_status="reading"
        )
        original_updated_at = owned.updated_at
        ShelfItem.objects.create(
            user=self.user, book=self.second, ownership_status="not_owned", reading_status=None
        )
        third = Book.objects.create(title="第三分册", series=self.series)
        ShelfItem.objects.create(user=self.user, book=third, ownership_status="unknown")
        unrelated = Book.objects.create(
            title="另一套装", series=BookSeries.objects.create(name="第二阶")
        )
        self.assertEqual(
            self.add_series().json(),
            {"added_count": 2, "already_owned_count": 1, "total_count": 3},
        )
        owned.refresh_from_db()
        self.assertEqual(owned.reading_status, "reading")
        self.assertEqual(owned.updated_at, original_updated_at)
        for book in (self.second, third):
            item = ShelfItem.objects.get(user=self.user, book=book)
            self.assertEqual((item.ownership_status, item.reading_status), ("owned", "unread"))
        self.assertFalse(ShelfItem.objects.filter(user=self.user, book=unrelated).exists())

    def test_future_member_requires_explicit_top_up_and_other_users_are_unchanged(self):
        another = get_user_model().objects.create_user(username="another-series-owner")
        item = ShelfItem.objects.create(
            user=another, book=self.first, ownership_status="owned", reading_status="paused"
        )
        original_updated_at = item.updated_at
        self.add_series()
        third = Book.objects.create(title="新增分册", series=self.series)
        self.assertFalse(ShelfItem.objects.filter(user=self.user, book=third).exists())
        self.assertEqual(self.add_series().json()["added_count"], 1)
        item.refresh_from_db()
        self.assertEqual(item.reading_status, "paused")
        self.assertEqual(item.updated_at, original_updated_at)
        self.assertEqual(ShelfItem.objects.filter(user=another).count(), 1)

    def test_missing_empty_series_and_authentication(self):
        missing = self.client.post(reverse("shelf-series", args=[999999]))
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "not_found")
        empty = BookSeries.objects.create(name="空系列")
        self.assertEqual(
            self.client.get(reverse("books"), {"q": empty.name}).json()["series"][0]["total_count"],
            0,
        )
        response = self.client.post(reverse("shelf-series", args=[empty.id]))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_input")
        self.assertFalse(ShelfItem.objects.filter(user=self.user).exists())
        self.client.logout()
        response = self.add_series()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "not_authenticated")

    def test_series_write_requires_csrf_and_adds_all_languages(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url).status_code, 403)
        self.assertFalse(ShelfItem.objects.filter(user=self.user).exists())
        client.get("/admin/login/")
        response = client.post(self.url, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["added_count"], 2)
        self.assertEqual(
            set(ShelfItem.objects.filter(user=self.user).values_list("book_id", flat=True)),
            {self.first.id, self.second.id},
        )

    def test_series_failure_rolls_back_created_and_reactivated_items(self):
        removed = ShelfItem.objects.create(
            user=self.user, book=self.first, ownership_status="not_owned", reading_status=None
        )
        before = removed.updated_at
        intermediate = Book.objects.create(title="Banana Volume", series=self.series)
        original_save = ShelfItem.save

        def fail_second(item, *args, **kwargs):
            if item.book_id == self.second.id:
                raise RuntimeError("simulated write failure")
            return original_save(item, *args, **kwargs)

        with patch.object(ShelfItem, "save", fail_second), self.assertRaises(RuntimeError):
            self.add_series()
        removed.refresh_from_db()
        self.assertEqual((removed.ownership_status, removed.reading_status), ("not_owned", None))
        self.assertEqual(removed.updated_at, before)
        self.assertFalse(ShelfItem.objects.filter(user=self.user, book=self.second).exists())
        self.assertFalse(ShelfItem.objects.filter(user=self.user, book=intermediate).exists())

    def test_staff_can_manage_series_and_members(self):
        series_add = reverse("admin:catalog_bookseries_add")
        self.assertEqual(self.client.get(series_add).status_code, 302)
        self.user.is_staff = True
        self.user.is_superuser = True
        self.user.save()
        member = Book.objects.create(title="后台分册", language="english")
        response = self.client.post(
            series_add,
            {
                "name": "后台系列第二阶·纸质版",
                "description": "后台维护说明",
                "completeness": "unknown",
                "memberships-TOTAL_FORMS": "1",
                "memberships-INITIAL_FORMS": "0",
                "memberships-0-book": member.id,
                "memberships-0-position": "1",
                "evidence-TOTAL_FORMS": "0",
                "evidence-INITIAL_FORMS": "0",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        group = BookSeries.objects.get(name="后台系列第二阶·纸质版")
        self.assertEqual(group.description, "后台维护说明")
        self.assertEqual(group.books.get().title, "后台分册")


class SeriesConcurrencyTests(TransactionTestCase):
    def test_concurrent_overlapping_series_and_single_update_preserve_state_and_uniqueness(self):
        user = get_user_model().objects.create_user(username="concurrent-parent")
        series = BookSeries.objects.create(name="并发系列")
        books = [
            Book.objects.create(title=f"并发分册 {index}", series=series) for index in range(3)
        ]
        other_series = BookSeries.objects.create(name="共享并发套装")
        for book in books:
            other_series.books.add(book)
        ShelfItem.objects.create(
            user=user, book=books[0], ownership_status="owned", reading_status="reading"
        )
        clients = [Client() for _ in range(3)]
        for client in clients:
            client.force_login(user)
        barrier = Barrier(3)

        def write(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                if index == 2:
                    response = clients[index].put(
                        reverse("shelf-book", args=[books[0].id]),
                        json.dumps({"ownership_status": "owned", "reading_status": "familiar"}),
                        content_type="application/json",
                    )
                else:
                    target = series if index == 0 else other_series
                    response = clients[index].post(reverse("shelf-series", args=[target.id]))
                return response.status_code, response.json()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=3) as executor:
            results = list(executor.map(write, range(3)))
        self.assertTrue(all(status == 200 for status, _data in results))
        self.assertEqual(sum(data["added_count"] for _status, data in results[:2]), 2)
        self.assertEqual(ShelfItem.objects.filter(user=user).count(), 3)
        self.assertEqual(ShelfItem.objects.get(user=user, book=books[0]).reading_status, "familiar")


class SeriesMigrationTests(TransactionTestCase):
    def test_forward_and_reverse_migration_preserve_book_and_shelf_data(self):
        old_target = [("catalog", "0002_seed_single_books")]
        new_target = [("catalog", "0003_book_series")]
        executor = MigrationExecutor(connection)
        executor.migrate(old_target)
        try:
            old_apps = executor.loader.project_state(old_target).apps
            OldBook = old_apps.get_model("catalog", "Book")
            OldShelf = old_apps.get_model("catalog", "ShelfItem")
            OldUser = old_apps.get_model("auth", "User")
            user = OldUser.objects.create(username="migration-parent")
            for number, name in enumerate(("迁移系列", "迁移系列", "")):
                book = OldBook.objects.create(
                    title=f"迁移书 {number}",
                    series_name=name,
                    series_description=f"原始说明 {number}",
                    source_file="migration-test",
                    source_row=number + 1,
                )
                OldShelf.objects.create(
                    user=user, book=book, ownership_status="owned", reading_status="paused"
                )
            fields = (
                "id",
                "title",
                "language",
                "series_description",
                "source_file",
                "source_row",
                "created_at",
                "updated_at",
            )
            before_books = list(OldBook.objects.order_by("id").values_list(*fields))
            before_names = list(
                OldBook.objects.order_by("id").values_list("series_name", flat=True)
            )
            before_shelf = list(OldShelf.objects.order_by("id").values())
            executor = MigrationExecutor(connection)
            executor.migrate(new_target)
            new_apps = executor.loader.project_state(new_target).apps
            NewBook = new_apps.get_model("catalog", "Book")
            NewShelf = new_apps.get_model("catalog", "ShelfItem")
            self.assertEqual(
                list(NewBook.objects.order_by("id").values_list(*fields)), before_books
            )
            self.assertEqual(list(NewShelf.objects.order_by("id").values()), before_shelf)
            actual_names = [
                book.series.name if book.series_id else ""
                for book in NewBook.objects.select_related("series").order_by("id")
            ]
            self.assertEqual(actual_names, before_names)
            self.assertEqual(NewBook.objects.filter(series__name="迁移系列").count(), 2)
            executor = MigrationExecutor(connection)
            executor.migrate(old_target)
            restored_apps = executor.loader.project_state(old_target).apps
            RestoredBook = restored_apps.get_model("catalog", "Book")
            self.assertEqual(
                list(RestoredBook.objects.order_by("id").values_list("series_name", flat=True)),
                before_names,
            )
            self.assertEqual(
                list(RestoredBook.objects.order_by("id").values_list(*fields)), before_books
            )
        finally:
            MigrationExecutor(connection).migrate([("catalog", "0006_evidence_uniqueness")])
