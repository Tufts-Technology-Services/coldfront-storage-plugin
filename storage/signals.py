import logging
from django.dispatch import receiver
from coldfront.core.allocation.models import Allocation, AllocationAttribute, AllocationUser
from coldfront.core.allocation.signals import (allocation_activate, 
                                               allocation_attribute_changed,
                                               allocation_activate_user,
                                               allocation_new, allocation_change_created)
from coldfront.core.allocation.views import (AllocationCreateView, AllocationDetailView, 
                                             AllocationChangeDetailView, 
                                             AllocationChangeView,
                                             AllocationAttributeEditView)

from .constants import QUOTA_ATTRIBUTE_NAME
from .tasks import (set_storage_quota, 
                    create_share, 
                    add_attributes_to_new_storage_allocation, 
                    create_directory)
from .context_storage import get_request_username
from .utils import (stamp_allocation_requester, stamp_allocation_approver, 
                    stamp_quota_requester, stamp_quota_approver)
logger = logging.getLogger(__name__)


@receiver(allocation_activate, sender=AllocationDetailView, dispatch_uid="activate-storage-allocation")
#@receiver(allocation_change_approved, sender=AllocationChangeView)
def activate_storage_allocation(sender, **kwargs):
    allocation_pk = kwargs.get('allocation_pk')
    allocation = Allocation.objects.get(pk=allocation_pk)
    if allocation.resources.first().resource_type.name == 'Storage':
        if allocation.status.name not in ['Active']:
            logger.debug(f"Allocation {allocation_pk} is not active. Skipping storage provisioning.")
            return
        stamp_allocation_approver(allocation, get_request_username() or 'unknown')
        create_share(allocation_pk)


@receiver(allocation_attribute_changed, sender=AllocationChangeDetailView)
@receiver(allocation_attribute_changed, sender=AllocationAttributeEditView)
def allocation_quota_changed_handler(sender, **kwargs):
    allocation_pk = kwargs.get('allocation_pk')
    attribute_pk = kwargs.get('attribute_pk')
    allocation = Allocation.objects.get(pk=allocation_pk)
    if allocation.resources.first().resource_type.name == 'Storage':
        attribute_name = AllocationAttribute.objects.get(id=attribute_pk).allocation_attribute_type.name
        if attribute_name == QUOTA_ATTRIBUTE_NAME:
            # quota change
            stamp_quota_approver(allocation, get_request_username() or 'unknown')
            set_storage_quota(allocation_pk, allocation_attribute_change_id=attribute_pk)


@receiver(allocation_new, sender=AllocationCreateView, dispatch_uid="add-requester-info")
def add_allocation_requester_info(sender, **kwargs):
    allocation_pk = kwargs.get('allocation_pk')
    allocation = Allocation.objects.get(pk=allocation_pk)
    if allocation.resources.first().resource_type.name == 'Storage':
        requester = get_request_username() or 'unknown'
        stamp_allocation_requester(allocation, requester)


@receiver(allocation_change_created, sender=AllocationChangeView, dispatch_uid="add-change-requester-info")
def add_change_requester_info(sender, **kwargs):
    allocation_pk = kwargs.get('allocation_pk')
    allocation = Allocation.objects.get(pk=allocation_pk)
    if allocation.resources.first().resource_type.name == 'Storage':
        requester = get_request_username() or 'unknown'
        stamp_quota_requester(allocation, requester)


@receiver(allocation_activate_user, dispatch_uid="add-folder-activate-user")
def allocation_activate_user_add_folder_creation_handler(sender, **kwargs):
    allocation_user_pk = kwargs.get('allocation_user_pk')
    allocation_user = AllocationUser.objects.get(pk=allocation_user_pk)
    allocation = allocation_user.allocation
    if allocation.resources.first().resource_type.name == 'Storage':
        dc = allocation.allocationattribute_set.filter(allocation_attribute_type__name="directory_creation")
        if not dc.exists():
            logger.debug(f"Directory creation attribute not set for allocation {allocation.id}. Skipping directory creation.")
            return
        if allocation.status.name not in ['Active']:
            logger.debug(f"Allocation {allocation.id} is not active. Skipping directory creation.")
            return
        create_directory(allocation.id, dc.first().value)


def enable_add_attributes():
    allocation_new.connect(add_attributes_to_new_storage_allocation_handler, sender=AllocationCreateView, dispatch_uid="add_storage_attributes_to_new_allocation")


def add_attributes_to_new_storage_allocation_handler(sender, **kwargs):
    allocation_pk = kwargs.get('allocation_pk')
    allocation = Allocation.objects.get(pk=allocation_pk)
    if allocation.resources.first().resource_type.name == 'Storage':
        add_attributes_to_new_storage_allocation(allocation_pk)
