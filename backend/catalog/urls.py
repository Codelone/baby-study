from django.urls import path

from catalog import intake_api
from catalog.views import (
    book_detail,
    books,
    catalog_capabilities,
    series_detail,
    shelf,
    shelf_book,
    shelf_series,
)

urlpatterns = [
    path("catalog-capabilities", catalog_capabilities, name="catalog-capabilities"),
    path("book-intakes", intake_api.book_intakes, name="book-intakes"),
    path("book-intakes/<uuid:intake_id>", intake_api.intake_detail, name="intake-detail"),
    path("book-intakes/<uuid:intake_id>/groups", intake_api.intake_groups, name="intake-groups"),
    path(
        "book-intakes/<uuid:intake_id>/enrichment",
        intake_api.intake_enrichment,
        name="intake-enrichment",
    ),
    path("book-intakes/<uuid:intake_id>/confirm", intake_api.intake_confirm, name="intake-confirm"),
    path("book-intakes/<uuid:intake_id>/cancel", intake_api.intake_cancel, name="intake-cancel"),
    path("book-intakes/<uuid:intake_id>/retry", intake_api.intake_retry, name="intake-retry"),
    path(
        "book-intakes/<uuid:intake_id>/photos/<uuid:photo_id>",
        intake_api.intake_photo,
        name="intake-photo",
    ),
    path("books", books, name="books"),
    path("books/<int:book_id>", book_detail, name="book-detail"),
    path("series/<int:series_id>", series_detail, name="series-detail"),
    path("shelf", shelf, name="shelf"),
    path("shelf/<int:book_id>", shelf_book, name="shelf-book"),
    path("shelf/series/<int:series_id>", shelf_series, name="shelf-series"),
]
