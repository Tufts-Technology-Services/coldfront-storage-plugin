import logging
from django.conf import settings
from coldfront.coldfront.plugins.storage.utils import units_to_bytes
from django_q.tasks import async_task
from coldfront.core.resource.models import ResourceAttribute
from coldfront.core.allocation.models import Allocation
from coldfront.config.env import ENV

from .models import StorageHandler

logger = logging.getLogger(__name__)

#USAGE_IN_BYTES_ATTRIBUTE_NAME = "reported_usage_bytes"
#QUOTA_ATTRIBUTE_NAME = "Storage Quota (TB)"
#USAGE_REPORT_DATE_ATTRIBUTE_NAME = "usage_report_date"
#QUOTA_REPORT_DATE_ATTRIBUTE_NAME = "quota_report_date"
#QUOTA_ID_ATTRIBUTE_NAME = "vast_path"




def get_storage_quotas_batch():
    """
    Task to get storage quotas from storage systems for all storage resources in Coldfront with StorageHandlers
    attribute names: quota_report_date, vast_path, Storage Path, Storage Quota (TB)
    """
    handlers = StorageHandler.objects.all()

    for storage_type in handlers:
        update_task = storage_type.get_quota_task if storage_type else None
        if update_task is None:
            logger.warning(f"No quota task configured for storage system type '{storage_type.resource.name}'")
            continue   
        client_config = settings.STORAGE_PLUGIN_CONFIG.get("clients", {}).get(storage_type.quota_client_key, None)
        if client_config:
            resources = storage_type.resource_set.all()
            async_task(update_task, resources, client_config) # this task must be able to handle receiving a list of resources and the client config, and then update the quotas for all allocations of those resources accordingly
        else:
            logger.warning(f"Storage resources have unrecognized quota_client_key '{storage_type.quota_client_key}'. Add client configuration for this key in STORAGE_PLUGIN_CONFIG.")


def set_storage_quota(allocation_pk: int, allocation_change_id=None, allocation_attribute_change_id=None):
    """
    Task to update storage quota for an allocation. This is intended to be called when an allocation is created or updated for a resource, in order to ensure that the storage quota for the allocation is updated in the storage system accordingly.
    """
    allocation = Allocation.objects.get(pk=allocation_pk)
    # make sure this allocation is associated with only one storage resource
    resources =  allocation.resources.filter(resource_type__name="Storage")
    if resources.count() != 1:
        logger.warning(f"Allocation {allocation.pk} is associated with {resources.count()} storage resources. Expected exactly 1. Cannot determine which resource to use to update storage quota.")
        return
    
    #  and that resource has a StorageHandler configured with a set_quota_task and quota_client_key. 
    storage_handler = StorageHandler.objects.filter(resource=resources.first())
    if not storage_handler.exists():
        logger.warning(f"No StorageHandler configured for resource(s) associated with allocation {allocation.pk}. Cannot update storage quota.")
        return
    
    if storage_handler and storage_handler.set_quota_task and storage_handler.quota_client_key:
        client_config = settings.STORAGE_PLUGIN_CONFIG.get("clients", {}).get(storage_handler.quota_client_key, None)
        if client_config:
            new_quota = allocation.allocationattribute_set.filter(allocation_attribute_type__name=ENV.str('QUOTA_ATTRIBUTE_NAME')).first().value # todo: make sure this contains the new value. 
            new_quota_bytes = units_to_bytes(float(new_quota))
            quota_id = allocation.allocationattribute_set.filter(allocation_attribute_type__name=ENV.str('QUOTA_ID_ATTRIBUTE_NAME')).first().value
            async_task(storage_handler.set_quota_task, quota_id, new_quota_bytes, client_config)
        else:
            logger.warning(f"Storage resources for Allocation ({allocation.pk}) have unrecognized quota_client_key '{storage_handler.quota_client_key}'. Add client configuration for this key in STORAGE_PLUGIN_CONFIG.")
    else:
        logger.warning(f"Resource for Allocation ({allocation.pk}) does not have a valid StorageHandler with set_quota_task and quota_client_key configured")


def update_storage_quota(allocation, new_quota):
    storage_system = ResourceAttribute.objects.filter(
        resource__in=allocation.resources.all(),
        resource_attribute_type__name='storage_system').first().value
    update_task = settings.STORAGE_PLUGIN_CONFIG.get(storage_system, {}).get("update_quota", None)
    client_config = settings.STORAGE_PLUGIN_CONFIG.get(storage_system, {}).get("client_config", None)
    if update_task and client_config:
        async_task(update_task, allocation, client_config)
    else:
        logger.warning(f"Storage resources have unrecognized storage system '{storage_system}'")






