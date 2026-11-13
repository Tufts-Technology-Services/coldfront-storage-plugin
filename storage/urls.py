from django.urls import path

from . import views

urlpatterns = [
    path(
        "allocation/export/",
        views.StorageAllocationExportView.as_view(),
        name="storage-allocation-export",
    ),
]
