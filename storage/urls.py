from django.urls import path

from . import views

urlpatterns = [
    path(
        'allocation/<int:pk>/storage-request-details/',
        views.StorageAllocationRequestDetailsView.as_view(),
        name='storage-allocation-request-details',
    )
]