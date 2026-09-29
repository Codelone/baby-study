from django.contrib import admin
from django.urls import include, path

from health.views import health

urlpatterns = [
    path("admin/", admin.site.urls),
    path("baby/study/health", health, name="health"),
    path("baby/study/", include("catalog.urls")),
    path("baby/study/", include("assessment.urls")),
]
