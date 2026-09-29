from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from catalog.models import Book, BookSeries, ShelfItem
from catalog.serializers import (
    BookDetailSerializer,
    BookSerializer,
    BookSeriesSerializer,
    SeriesDetailSerializer,
    ShelfItemSerializer,
    ShelfUpdateSerializer,
)


def book_queryset():
    return (
        Book.objects.filter(catalog_status="active")
        .select_related("series")
        .prefetch_related("groups")
    )


def series_queryset():
    return BookSeries.objects.prefetch_related(
        Prefetch("books", queryset=book_queryset()), "memberships"
    )


def ownership_context(request):
    return {
        "owned_book_ids": set(
            ShelfItem.objects.filter(user=request.user, ownership_status="owned").values_list(
                "book_id", flat=True
            )
        )
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def book_detail(request, book_id):
    book = get_object_or_404(book_queryset().prefetch_related("evidence"), pk=book_id)
    return Response(BookDetailSerializer(book, context=ownership_context(request)).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def series_detail(request, series_id):
    series = get_object_or_404(series_queryset().prefetch_related("evidence"), pk=series_id)
    return Response(SeriesDetailSerializer(series, context=ownership_context(request)).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def books(request):
    queryset = book_queryset()
    query = request.query_params.get("q", "").strip()
    language = request.query_params.get("language", "").strip()
    if query:
        queryset = queryset.filter(
            Q(title__icontains=query)
            | Q(aliases__icontains=query)
            | Q(original_title__icontains=query)
            | Q(groups__name__icontains=query)
            | Q(groups__aliases__icontains=query)
        )
    if language:
        if language not in Book.Language.values:
            return Response(
                {
                    "error": {
                        "code": "invalid_input",
                        "message": "语言筛选值无效",
                        "details": {"language": ["无效的语言"]},
                    }
                },
                status=400,
            )
        queryset = queryset.filter(language=language)
    queryset = queryset.distinct()
    owned_book_ids = set(
        ShelfItem.objects.filter(
            user=request.user, ownership_status=ShelfItem.OwnershipStatus.OWNED
        ).values_list("book_id", flat=True)
    )
    series = series_queryset().filter(books__in=queryset).distinct()
    if query and not language:
        series = (
            series_queryset()
            .filter(Q(books__in=queryset) | Q(name__icontains=query) | Q(aliases__icontains=query))
            .distinct()
        )
    elif not query and not language:
        series = series_queryset()
    context = {"owned_book_ids": owned_book_ids}
    return Response(
        {
            "books": BookSerializer(queryset, many=True, context=context).data,
            "series": BookSeriesSerializer(series, many=True, context=context).data,
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def shelf(request):
    items = (
        ShelfItem.objects.select_related("book__series")
        .prefetch_related("book__groups")
        .filter(user=request.user, ownership_status=ShelfItem.OwnershipStatus.OWNED)
        .order_by("book__title", "book_id")
    )
    return Response({"items": ShelfItemSerializer(items, many=True).data})


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def shelf_book(request, book_id):
    book = get_object_or_404(book_queryset(), pk=book_id)
    serializer = ShelfUpdateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        item, _created = ShelfItem.objects.update_or_create(
            user=request.user,
            book=book,
            defaults=serializer.validated_data,
        )
    return Response(ShelfItemSerializer(item).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def shelf_series(request, series_id):
    series = get_object_or_404(BookSeries, pk=series_id)
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        book_ids = list(series.books.filter(catalog_status="active").values_list("id", flat=True))
        if not book_ids:
            raise serializers.ValidationError({"series": "该系列尚未录入分册，无法加入书架"})
        added_count = 0
        for book_id in book_ids:
            item, created = ShelfItem.objects.get_or_create(
                user=request.user,
                book_id=book_id,
                defaults={
                    "ownership_status": ShelfItem.OwnershipStatus.OWNED,
                    "reading_status": ShelfItem.ReadingStatus.UNREAD,
                },
            )
            if created:
                added_count += 1
            elif item.ownership_status != ShelfItem.OwnershipStatus.OWNED:
                item.ownership_status = ShelfItem.OwnershipStatus.OWNED
                item.reading_status = ShelfItem.ReadingStatus.UNREAD
                item.save(update_fields=("ownership_status", "reading_status", "updated_at"))
                added_count += 1
    return Response(
        {
            "added_count": added_count,
            "already_owned_count": len(book_ids) - added_count,
            "total_count": len(book_ids),
        }
    )
