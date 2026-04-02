import logging
from coldfront.core.allocation.models import Allocation, AllocationAttributeType, AllocationPermission, AllocationStatusChoice
from coldfront.core.allocation.views import AllocationCreateView as ColdfrontAllocationCreateView
from coldfront.core.allocation.views import AllocationAttributeEditView as ColdfrontAllocationAttributeEditView
from coldfront.plugins.allocation_blueprint.tasks import apply_blueprint
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import FormView

from .constants import QUOTA_ATTRIBUTE_NAME, STORAGE_PLUGIN_STORAGE_UNITS
from .forms import AllocationAttributeEditForm, StorageAllocationRequestDetailsForm

logger = logging.getLogger(__name__)


class AllocationCreateView(ColdfrontAllocationCreateView):
    """Allocation create view override that redirects to quota page for storage allocations."""

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.object.resources.first().resource_type.name == 'Storage':
            return redirect('storage-allocation-request-details', pk=self.object.pk)
        return response


class StorageAllocationRequestDetailsView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    """View for specifying allocation details to complete a storage allocation request."""

    allocation: Allocation
    form_class = StorageAllocationRequestDetailsForm
    template_name = 'allocation/storage_request_details.html'

    def test_func(self):
        allocation = get_object_or_404(Allocation, pk=self.kwargs['pk'])
        if self.request.user.has_perm('allocation.can_view_all_allocations'):
            return True
        return allocation.has_perm(self.request.user, AllocationPermission.USER)

    def dispatch(self, request, *args, **kwargs):
        self.allocation = get_object_or_404(
            Allocation.objects.select_related('project').prefetch_related(
                'resources__resource_type'
            ),
            pk=kwargs['pk'],
        )
        if self.allocation.allocationattribute_set.filter(
            allocation_attribute_type__name=QUOTA_ATTRIBUTE_NAME,
        ).exists():
            messages.info(request, 'Storage quota is already set for this allocation.')
            return redirect(reverse('project-detail', kwargs={'pk': self.allocation.project_id}))
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs) | {
            'allocation': self.allocation,
            'resource': self.allocation.get_parent_resource
        }

    def form_valid(self, form):
        quota = form.cleaned_data['quota_request']

        try:
            attr_type = AllocationAttributeType.objects.get(name=QUOTA_ATTRIBUTE_NAME)
            self.allocation.allocationattribute_set.create(
                allocation_attribute_type=attr_type,
                value=str(quota),
            )
            apply_blueprint(self.allocation)
        except AllocationAttributeType.DoesNotExist:
            messages.error(
                self.request,
                'Unable to save quota. Please contact support.',
            )
            return redirect('allocation-detail', pk=self.allocation.pk)
        else:
            msg = f'Storage quota of {quota} {STORAGE_PLUGIN_STORAGE_UNITS} has been set. Your allocation request is complete.'
            messages.success(self.request, msg)
            return redirect(reverse('project-detail', kwargs={'pk': self.allocation.project_id}))


class AllocationAttributeEditView(ColdfrontAllocationAttributeEditView):
    formset_class = AllocationAttributeEditForm
    
    def get_allocation_attributes_to_change(self, allocation_obj):
        attributes_to_change = allocation_obj.allocationattribute_set.select_related("allocation_attribute_type").all()

        attributes_to_change = [
            {
                "attribute_pk": attribute.pk,
                "name": attribute.allocation_attribute_type.name,
                "orig_value": attribute.value,
                "value": attribute.value,
                "is_private": attribute.allocation_attribute_type.is_private
            }
            for attribute in attributes_to_change
        ]

        return attributes_to_change
