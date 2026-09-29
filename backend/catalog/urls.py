from django.urls import path

from catalog.views import book_detail, books, series_detail, shelf, shelf_book, shelf_series

urlpatterns = [
    path("books", books, name="books"),
    path("books/<int:book_id>", book_detail, name="book-detail"),
    path("series/<int:series_id>", series_detail, name="series-detail"),
    path("shelf", shelf, name="shelf"),
    path("shelf/<int:book_id>", shelf_book, name="shelf-book"),
    path("shelf/series/<int:series_id>", shelf_series, name="shelf-series"),
]
