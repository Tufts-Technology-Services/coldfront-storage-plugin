"""Test URLconf: ColdFront's own URLs plus the plugin's, mounted where the deployment mounts them."""

from coldfront.config.urls import urlpatterns
from django.urls import include, path

urlpatterns = [*urlpatterns, path("storage/", include("storage.urls"))]
