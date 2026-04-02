import logging
from django.dispatch import receiver
from django_q.tasks import async_task
from coldfront.core.allocation.models import Allocation, AllocationAttribute
from coldfront.core.allocation.signals import (allocation_activate, allocation_attribute_changed, allocation_change_approved)
from coldfront.core.allocation.views import (AllocationCreateView, AllocationChangeView, AllocationChangeDetailView, AllocationAttributeEditView)

from .models import StorageHandler
from .constants import QUOTA_ATTRIBUTE_NAME

logger = logging.getLogger(__name__)


@receiver(allocation_activate, sender=AllocationCreateView)
#@receiver(allocation_change_approved, sender=AllocationChangeView)
def activate_storage_allocation(sender, **kwargs):
    allocation_pk = kwargs.get('allocation_pk')
    allocation = Allocation.objects.get(pk=allocation_pk)
    if allocation.status.name not in ['Active']:
        logger.debug(f"Allocation {allocation_pk} is not active. Skipping storage provisioning.")
        return
    storage_resource = allocation.resources.filter(resource_type__name='Storage')
    if storage_resource.exists():
        if storage_resource.count() > 1:
            logger.warning(f"Allocation {allocation_pk} is associated with multiple storage resources. Expected only one. Cannot determine which resource to use to update storage quota.")
            return
        handler = StorageHandler.objects.filter(resource=storage_resource.first()) # make sure there is a storage handler configured for this resource before attempting to provision storage
        if handler.exists():   
            # what could be here? 
            async_task('coldfront.plugins.storage.tasks.provision_or_update_storage', allocation_pk)


@receiver(allocation_attribute_changed, sender=AllocationChangeDetailView)
@receiver(allocation_attribute_changed, sender=AllocationAttributeEditView)
def allocation_attribute_changed_handler(sender, **kwargs):
    allocation_pk = kwargs.get('allocation_pk')
    attribute_pk = kwargs.get('attribute_pk')
    attribute_name = AllocationAttribute.objects.get(id=attribute_pk).allocation_attribute_type.name
    if attribute_name == QUOTA_ATTRIBUTE_NAME:
        # quota change
        storage_resource = Allocation.objects.get(id=allocation_pk).resources.filter(resource_type__name='Storage')
        if storage_resource.exists():
            if storage_resource.count() > 1:
                logger.warning(f"Allocation {allocation_pk} is associated with multiple storage resources. Cannot determine which resource to use to update storage quota.")
                return
            handler = StorageHandler.objects.filter(resource=storage_resource.first()) # make sure there is a storage handler configured for this resource before attempting to update storage quota
            if handler.exists():   
                async_task(handler.first().set_quota_task, allocation_pk, client_id=handler.first().quota_client_id)
        else:
            logger.warning(f"Allocation {allocation_pk} is not associated with any storage resources. Cannot update storage quota.")
