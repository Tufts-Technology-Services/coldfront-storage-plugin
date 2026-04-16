import logging
from django.dispatch import receiver
from coldfront.core.allocation.models import Allocation, AllocationAttribute
from coldfront.core.allocation.signals import (allocation_activate, allocation_attribute_changed, allocation_change_approved)
from coldfront.core.allocation.views import (AllocationCreateView, AllocationChangeView, AllocationChangeDetailView, AllocationAttributeEditView)

from .constants import QUOTA_ATTRIBUTE_NAME
from .tasks import set_storage_quota, create_share

logger = logging.getLogger(__name__)


@receiver(allocation_activate, sender=AllocationCreateView)
#@receiver(allocation_change_approved, sender=AllocationChangeView)
def activate_storage_allocation(sender, **kwargs):
    allocation_pk = kwargs.get('allocation_pk')
    allocation = Allocation.objects.get(pk=allocation_pk)
    if allocation.status.name not in ['Active']:
        logger.debug(f"Allocation {allocation_pk} is not active. Skipping storage provisioning.")
        return
    create_share(allocation_pk) # create share first, since setting the quota may depend on the share existing in some storage system


@receiver(allocation_attribute_changed, sender=AllocationChangeDetailView)
@receiver(allocation_attribute_changed, sender=AllocationAttributeEditView)
def allocation_attribute_changed_handler(sender, **kwargs):
    allocation_pk = kwargs.get('allocation_pk')
    attribute_pk = kwargs.get('attribute_pk')
    attribute_name = AllocationAttribute.objects.get(id=attribute_pk).allocation_attribute_type.name
    if attribute_name == QUOTA_ATTRIBUTE_NAME:
        # quota change
        set_storage_quota(allocation_pk, allocation_attribute_change_id=attribute_pk)
