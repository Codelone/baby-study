from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from catalog.models import BookSeries
from catalog.serializers import BookSerializer, BookSeriesSerializer
from catalog.series_services import add_members, remove_member, reorder_members, save_series
from catalog.services import require_catalog_manager
from catalog.views import ownership_context, series_queryset


def result(request, series_id):
    series = get_object_or_404(series_queryset(), pk=series_id)
    return BookSeriesSerializer(series, context=ownership_context(request)).data


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def series_collection(request):
    if request.method == "POST":
        data = request.data.copy()
        book_ids = data.pop("book_ids", None)
        with transaction.atomic():
            series = save_series(request.user, data)
            if book_ids is not None:
                add_members(request.user, series.pk, book_ids)
        return Response(result(request, series.pk), status=201)
    query = request.query_params.get("q", "").strip()
    series = series_queryset()
    if query:
        series = series.filter(
            Q(name__icontains=query)
            | Q(aliases__icontains=query)
            | Q(family__icontains=query)
            | Q(isbn__icontains=(query.replace("-", "").replace(" ", "") or query))
        )
    return Response(
        {"series": BookSeriesSerializer(series, many=True, context=ownership_context(request)).data}
    )


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def series_members(request, series_id):
    require_catalog_manager(request.user)
    series = get_object_or_404(BookSeries, pk=series_id)
    if request.method == "POST":
        add_members(request.user, series_id, request.data.get("book_ids"))
        return Response(result(request, series_id))
    context = ownership_context(request)
    links = series.memberships.select_related("book").prefetch_related("book__groups")
    return Response(
        {
            "members": [
                {"book": BookSerializer(link.book, context=context).data, "position": link.position}
                for link in links
            ]
        }
    )


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def series_member(request, series_id, book_id):
    remove_member(request.user, series_id, book_id)
    return Response(status=204)


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def series_member_order(request, series_id):
    reorder_members(request.user, series_id, request.data.get("book_ids"))
    return Response(result(request, series_id))
