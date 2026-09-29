import copy
from itertools import permutations

from django.test import SimpleTestCase

from catalog.intake.grouping import group_photos

ISBN_A = "9780375827785"
ISBN_B = "9780140328721"


def photo(
    photo_id,
    *,
    photo_type="cover",
    isbn_kind=None,
    identity_text="",
    model_name="fixed-image-model",
    issues=None,
    **fields,
):
    return {
        "id": photo_id,
        "extraction": {
            "fields": fields,
            "photo_type": photo_type,
            "isbn_kind": isbn_kind or ("single" if fields.get("isbn") else "none"),
            "identity_text": identity_text,
            "issues": issues or [],
            "model_name": model_name,
        },
    }


def memberships(result):
    return {frozenset(group["photo_ids"]) for group in result["groups"]}


class GroupingTests(SimpleTestCase):
    def test_mixed_unordered_cover_back_and_copyright_are_grouped_with_sources(self):
        photos = [
            photo("a-cover", title="Green Meadow", authors=["Alice River"]),
            photo("b-back", title="Blue Mountain", isbn=ISBN_B),
            photo(
                "a-copyright",
                photo_type="copyright_page",
                title="Green Meadow",
                authors=["Alice River"],
                publisher="Visible Press",
                isbn=ISBN_A,
                pages=32,
            ),
            photo("a-back", photo_type="back_cover", isbn=ISBN_A),
        ]
        baseline = group_photos(photos)
        self.assertEqual(
            memberships(baseline),
            {frozenset({"a-cover", "a-copyright", "a-back"}), frozenset({"b-back"})},
        )
        self.assertEqual(baseline["unassigned_photo_ids"], [])
        for ordered in permutations(photos):
            self.assertEqual(group_photos(list(ordered)), baseline)
        group = next(group for group in baseline["groups"] if "a-cover" in group["photo_ids"])
        self.assertEqual(group["fields"]["pages"], 32)
        self.assertEqual(group["fields"]["publisher"], "Visible Press")
        self.assertTrue(group["selected"])
        self.assertIsNone(group["existing_book_id"])
        sources = {(item["field"], item["photo_id"]) for item in group["evidence"]}
        self.assertIn(("title", "a-cover"), sources)
        self.assertIn(("isbn", "a-copyright"), sources)
        self.assertNotIn(("pages", "a-cover"), sources)
        for item in group["evidence"]:
            self.assertEqual(item["kind"], "photo")
            self.assertEqual(item["model_name"], "fixed-image-model")
            self.assertNotIn("source_url", item)
            self.assertIn(item["photo_id"], group["photo_ids"])
        copyright_evidence = next(
            item for item in group["evidence"] if item["photo_id"] == "a-copyright"
        )
        self.assertEqual(copyright_evidence["source_label"], "版权页识别")

    def test_isbn10_and13_photos_have_one_identity(self):
        result = group_photos(
            [photo("back", isbn="0-375-82778-1"), photo("cover", title="Green Meadow", isbn=ISBN_A)]
        )
        self.assertEqual(memberships(result), {frozenset({"back", "cover"})})
        self.assertEqual(result["groups"][0]["fields"]["isbn"], ISBN_A)

    def test_without_isbn_title_and_visible_author_can_cross_match_pages(self):
        result = group_photos(
            [
                photo("cover", title="Green Meadow", authors=["Alice River"]),
                photo(
                    "copyright",
                    photo_type="copyright_page",
                    identity_text="Green Meadow\nWritten by Alice River\nVisible Press",
                    publisher="Visible Press",
                ),
            ]
        )
        self.assertEqual(memberships(result), {frozenset({"cover", "copyright"})})
        self.assertEqual(result["groups"][0]["fields"]["isbn"], "")
        self.assertEqual(result["groups"][0]["fields"]["publisher"], "Visible Press")
        self.assertEqual(
            [
                item["photo_id"]
                for item in result["groups"][0]["evidence"]
                if item["field"] == "title"
            ],
            ["cover"],
        )

    def test_title_and_publisher_match_without_authors(self):
        result = group_photos(
            [
                photo("cover", title="森林里的旅行", publisher="可见出版社"),
                photo("page", title="森林里的旅行", publisher="可见出版社", pages=24),
            ]
        )
        self.assertEqual(memberships(result), {frozenset({"cover", "page"})})

    def test_title_alone_does_not_merge_and_copyright_without_identity_stays_unassigned(self):
        result = group_photos(
            [
                photo("cover1", title="Green Meadow"),
                photo("cover2", title="Green Meadow"),
                photo("page", photo_type="copyright_page", publisher="Visible Press", pages=32),
                photo("text-only", identity_text="Green Meadow"),
            ]
        )
        self.assertEqual(memberships(result), {frozenset({"cover1"}), frozenset({"cover2"})})
        self.assertEqual(result["unassigned_photo_ids"], ["page", "text-only"])
        self.assertTrue(all(not group["selected"] for group in result["groups"]))
        for group in result["groups"]:
            self.assertIn("ambiguous_match", [item["code"] for item in group["issues"]])

    def test_similar_series_titles_and_volume_numbers_do_not_merge(self):
        result = group_photos(
            [
                photo("one-cover", title="Forest Journey 1", authors=["Alice River"]),
                photo("two-cover", title="Forest Journey 2", authors=["Alice River"]),
                photo("one-page", title="Forest Journey 1", authors=["Alice River"], pages=24),
                photo("two-page", title="Forest Journey 2", authors=["Alice River"], pages=32),
            ]
        )
        self.assertEqual(
            memberships(result),
            {frozenset({"one-cover", "one-page"}), frozenset({"two-cover", "two-page"})},
        )

    def test_same_set_isbn_cannot_merge_volumes_or_become_group_identity(self):
        result = group_photos(
            [
                photo("one", title="Forest Journey 1", isbn=ISBN_A, isbn_kind="set"),
                photo("two", title="Forest Journey 2", isbn=ISBN_A, isbn_kind="set"),
                photo("box", isbn=ISBN_A, isbn_kind="set", photo_type="back_cover"),
            ]
        )
        self.assertEqual(memberships(result), {frozenset({"one"}), frozenset({"two"})})
        self.assertEqual(result["unassigned_photo_ids"], ["box"])
        for group in result["groups"]:
            self.assertEqual(group["fields"]["isbn"], "")
            self.assertFalse(any(item["field"] == "isbn" for item in group["evidence"]))
            self.assertEqual(group["issues"][0]["code"], "set_isbn")

    def test_same_title_different_isbn_or_known_version_fields_stay_separate(self):
        for field, a, b in (
            ("isbn", ISBN_A, ISBN_B),
            ("authors", ["Alice River"], ["Bob Mountain"]),
            ("publisher", "First Press", "Second Press"),
            ("edition", "First edition", "Second edition"),
            ("publication_date", "2020", "2024"),
            ("language", "english", "chinese"),
            ("binding", "hardback", "paperback"),
            ("pages", 24, 32),
        ):
            with self.subTest(field=field):
                common = {"title": "Green Meadow", "authors": ["Alice River"]}
                result = group_photos(
                    [photo("a", **{**common, field: a}), photo("b", **{**common, field: b})]
                )
                self.assertEqual(memberships(result), {frozenset({"a"}), frozenset({"b"})})

    def test_matching_isbn_never_overrides_conflicting_title_author_or_edition(self):
        for field, different in (
            ("title", "Blue Mountain"),
            ("authors", ["Bob Mountain"]),
            ("publisher", "Other Press"),
            ("edition", "Second edition"),
        ):
            with self.subTest(field=field):
                common = {
                    "title": "Green Meadow",
                    "authors": ["Alice River"],
                    "publisher": "Visible Press",
                    "edition": "First edition",
                    "isbn": ISBN_A,
                }
                result = group_photos(
                    [photo("a", **common), photo("b", **{**common, field: different})]
                )
                self.assertEqual(memberships(result), {frozenset({"a"}), frozenset({"b"})})
                for group in result["groups"]:
                    self.assertFalse(group["selected"])
                    self.assertIn("identity_conflict", [item["code"] for item in group["issues"]])

    def test_neutral_cover_matching_two_isbn_editions_stays_unassigned(self):
        photos = [
            photo("cover", title="Green Meadow", authors=["Alice River"]),
            photo("first", title="Green Meadow", authors=["Alice River"], isbn=ISBN_A, edition="1"),
            photo(
                "second", title="Green Meadow", authors=["Alice River"], isbn=ISBN_B, edition="2"
            ),
        ]
        baseline = group_photos(photos)
        self.assertEqual(memberships(baseline), {frozenset({"first"}), frozenset({"second"})})
        self.assertEqual(baseline["unassigned_photo_ids"], ["cover"])
        for ordered in permutations(photos):
            self.assertEqual(group_photos(list(ordered)), baseline)

    def test_transitive_text_bridge_cannot_combine_known_editions(self):
        photos = [
            photo("neutral", title="Green Meadow", authors=["Alice River"]),
            photo("first", title="Green Meadow", authors=["Alice River"], edition="1"),
            photo("second", title="Green Meadow", authors=["Alice River"], edition="2"),
        ]
        baseline = group_photos(photos)
        self.assertEqual(len(baseline["groups"]), 3)
        self.assertTrue(all(not group["selected"] for group in baseline["groups"]))
        for ordered in permutations(photos):
            self.assertEqual(group_photos(list(ordered)), baseline)

    def test_simultaneous_conflicting_text_attachments_have_no_order_based_winner(self):
        result = group_photos(
            [
                photo("anchor", title="Green Meadow", authors=["Alice River"], isbn=ISBN_A),
                photo("first", title="Green Meadow", authors=["Alice River"], edition="1"),
                photo("second", title="Green Meadow", authors=["Alice River"], edition="2"),
            ]
        )
        self.assertEqual(memberships(result), {frozenset({"anchor"})})
        self.assertEqual(result["unassigned_photo_ids"], ["first", "second"])

    def test_blurry_multi_book_and_marketing_photos_cannot_contaminate_group(self):
        result = group_photos(
            [photo("book", title="Green Meadow", isbn=ISBN_A)]
            + [
                photo(kind, photo_type=kind, title="Blue Mountain", isbn=ISBN_A, pages=999)
                for kind in ("blurry", "multiple_books", "marketing")
            ]
        )
        self.assertEqual(memberships(result), {frozenset({"book"})})
        self.assertEqual(result["unassigned_photo_ids"], ["blurry", "marketing", "multiple_books"])
        self.assertIsNone(result["groups"][0]["fields"]["pages"])

    def test_wrong_isbn_without_other_identity_stays_unassigned(self):
        result = group_photos([photo("wrong", isbn="9780375827786"), photo("valid", isbn=ISBN_A)])
        self.assertEqual(memberships(result), {frozenset({"valid"})})
        self.assertEqual(result["unassigned_photo_ids"], ["wrong"])
        self.assertFalse(result["groups"][0]["selected"])

    def test_duplicate_photo_ids_are_idempotent_and_duplicate_images_keep_sources(self):
        a = photo("a", title="Green Meadow", isbn=ISBN_A)
        b = photo("b", title="Green Meadow", isbn=ISBN_A)
        result = group_photos([a, b, a])
        self.assertEqual(memberships(result), {frozenset({"a", "b"})})
        self.assertEqual(result, group_photos([b, a]))
        title_evidence = [
            item for item in result["groups"][0]["evidence"] if item["field"] == "title"
        ]
        self.assertEqual([item["photo_id"] for item in title_evidence], ["a", "b"])

    def test_duplicate_id_with_conflicting_extraction_is_rejected(self):
        with self.assertRaises(ValueError):
            group_photos([photo("a", title="A"), photo("a", title="B")])

    def test_non_identity_field_conflict_is_preserved_in_evidence_and_blank_draft(self):
        result = group_photos(
            [
                photo("a", title="Green Meadow", isbn=ISBN_A, description="First visible text"),
                photo("b", title="Green Meadow", isbn=ISBN_A, description="Other visible text"),
            ]
        )
        group = result["groups"][0]
        self.assertEqual(group["fields"]["description"], "")
        self.assertFalse(group["selected"])
        descriptions = [
            item["value"] for item in group["evidence"] if item["field"] == "description"
        ]
        self.assertEqual(descriptions, ["First visible text", "Other visible text"])
        self.assertIn("field_conflict", [item["code"] for item in group["issues"]])

    def test_partial_author_list_can_supply_more_visible_names_with_original_evidence(self):
        result = group_photos(
            [
                photo("a", title="Green Meadow", authors=["Alice River"]),
                photo("b", title="Green Meadow", authors=["Alice River", "Bob Mountain"]),
            ]
        )
        self.assertEqual(memberships(result), {frozenset({"a", "b"})})
        self.assertEqual(result["groups"][0]["fields"]["authors"], ["Alice River", "Bob Mountain"])

    def test_case_and_punctuation_normalize_but_short_text_fragments_do_not_match(self):
        result = group_photos(
            [
                photo("cover", title="Green Meadow!", authors=["Alice River"]),
                photo("page", title="green meadow", authors=["ALICE RIVER"]),
                photo("fragment", identity_text="Meadow by River"),
                photo("short-cover", title="猫", authors=["甲"]),
                photo("short-page", identity_text="猫甲"),
            ]
        )
        self.assertEqual(
            memberships(result), {frozenset({"cover", "page"}), frozenset({"short-cover"})}
        )
        self.assertEqual(result["unassigned_photo_ids"], ["fragment", "short-page"])

    def test_extraction_issues_are_attributed_to_their_own_photo(self):
        result = group_photos(
            [
                photo(
                    "cover",
                    title="Green Meadow",
                    isbn=ISBN_A,
                    issues=[{"code": "cropped", "message": "出版社被裁掉", "field": "publisher"}],
                ),
                photo("page", isbn=ISBN_A, publisher="Visible Press"),
            ]
        )
        group = result["groups"][0]
        self.assertEqual(group["issues"][0]["photo_id"], "cover")
        publisher = next(item for item in group["evidence"] if item["field"] == "publisher")
        self.assertEqual(publisher["photo_id"], "page")

    def test_input_is_not_mutated_and_output_has_no_guessed_levels_or_series(self):
        photos = [
            photo("a", title="Green Meadow", reading_attributes={"lexile": "500L"}, members=[1])
        ]
        before = copy.deepcopy(photos)
        result = group_photos(photos)
        self.assertEqual(photos, before)
        self.assertNotIn("reading_attributes", result["groups"][0]["fields"])
        self.assertNotIn("members", result["groups"][0]["fields"])

    def test_empty_and_invalid_input(self):
        self.assertEqual(group_photos([]), {"groups": [], "unassigned_photo_ids": []})
        for photos in (None, {}, [None], [{"id": 1}], [{"id": ""}], [{"id": "a"}]):
            with self.subTest(photos=photos), self.assertRaises(ValueError):
                group_photos(photos)
