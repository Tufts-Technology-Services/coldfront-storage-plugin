import logging
from coldfront_utils import units_to_bytes
from django_q.tasks import async_task
from coldfront.core.allocation.models import Allocation

from storage.utils import get_client_config

from .models import StorageHandler
from .constants import (QUOTA_ATTRIBUTE_NAME, 
                        STORAGE_PLUGIN_STORAGE_UNITS, 
                        GROUP_ATTRIBUTE_NAME, STORAGE_LOG_ONLY)

logger = logging.getLogger(__name__)


def get_storage_quotas_batch():
    """
    Task to get storage quotas from storage systems for all storage resources in Coldfront with StorageHandlers
    attribute names: quota_report_date, vast_path, Storage Path, Storage Quota (TB)
    """
    if STORAGE_LOG_ONLY:
        logger.info("STORAGE_LOG_ONLY is set to True. Skipping actual retrieval of storage quotas and just logging info.")
    
    handlers = StorageHandler.objects.all()

    for storage_type in handlers:
        get_quotas_task = storage_type.get_quotas_batch_task if storage_type else None
        if get_quotas_task is None:
            logger.warning(f"No quota task configured for storage system type '{storage_type.resource.name}'")
            continue
        logger.info(f"Getting quotas for resource {storage_type.resource.name}")
        
        if STORAGE_LOG_ONLY:
            logger.info(f"--STORAGE_LOG_ONLY")
            logger.info(f"Would call task '{get_quotas_task}' for resource {storage_type.resource.name} with client id: {storage_type.quota_client_id}")
            continue
        async_task(get_quotas_task, storage_type.resource.id,
                   get_client_config(storage_type.quota_client_id))
        

def set_storage_quota(allocation_pk: int, allocation_change_id=None, allocation_attribute_change_id=None):
    """
    Task to update storage quota for an allocation. This is intended to be called when an allocation is created or updated for a resource, in order to ensure that the storage quota for the allocation is updated in the storage system accordingly.
    """
    if STORAGE_LOG_ONLY:
        logger.info("STORAGE_LOG_ONLY is set to True. Skipping actual update of storage quota and just logging info.")
    storage_handler, allocation = get_storage_handler(allocation_pk)
    if storage_handler and storage_handler.set_quota_task and storage_handler.quota_client_id:
        client_config = get_client_config(storage_handler.quota_client_id)
        new_quota = allocation.allocationattribute_set.filter(allocation_attribute_type__name=QUOTA_ATTRIBUTE_NAME).first().value # todo: make sure this contains the new value. 
        new_quota_bytes = units_to_bytes(float(new_quota), units=STORAGE_PLUGIN_STORAGE_UNITS)
        native_path = allocation.allocationattribute_set.filter(allocation_attribute_type__name=client_config['native_path_attribute_name']).first().value
        if native_path and new_quota_bytes:
            if STORAGE_LOG_ONLY:
                logger.info(f"--STORAGE_LOG_ONLY")
                logger.info(f"Would call task '{storage_handler.set_quota_task}' for allocation {allocation_pk} with native path: {native_path} and new quota (bytes): {new_quota_bytes}")
                return
            async_task(storage_handler.set_quota_task, native_path, new_quota_bytes, client_config)
        else:
            logger.error(f"Missing required information to create share for allocation {allocation_pk}.")
            raise ValueError(f"Missing required information to create share for allocation {allocation_pk}.")
    else:
        logger.warning(f"No quota update task or client configured for resource {storage_handler.resource.name} associated with allocation {allocation_pk}. Cannot update storage quota.")



def create_share(allocation_pk: int):
    if STORAGE_LOG_ONLY:
        logger.info("STORAGE_LOG_ONLY is set to True. Skipping actual creation of storage share and just logging info.")
    storage_handler, allocation = get_storage_handler(allocation_pk)
    if storage_handler and storage_handler.create_share_task and storage_handler.create_client_id:
        client_config = get_client_config(storage_handler.create_client_id)
        native_path = allocation.allocationattribute_set.filter(allocation_attribute_type__name=client_config['native_path_attribute_name']).first().value
        quota_bytes = units_to_bytes(float(allocation.allocationattribute_set.filter(allocation_attribute_type__name=QUOTA_ATTRIBUTE_NAME).first().value), units=STORAGE_PLUGIN_STORAGE_UNITS)
        owner = allocation.project.pi.username
        group = None
        aa = allocation.allocation_attribute_set.filter(allocation_attribute_type__name=GROUP_ATTRIBUTE_NAME) # make sure the group attribute type exists
        if aa.exists():
            group = aa.first().value
        else:            
            pa = allocation.project.projectattribute_set.filter(project_attribute_type__name=GROUP_ATTRIBUTE_NAME)
            if pa.exists():
                group = pa.first().value
        if group and owner and native_path and quota_bytes:
            if STORAGE_LOG_ONLY:
                logger.info(f"--STORAGE_LOG_ONLY")
                logger.info(f"Would call task '{storage_handler.create_share_task}' for allocation {allocation_pk} with native path: {native_path}, quota (bytes): {quota_bytes}, owner: {owner}, and group: {group}")
                return
            async_task(storage_handler.create_share_task, native_path, quota_bytes, owner, group, client_config)
        else:
            logger.error(f"Missing required information to create share for allocation {allocation_pk}.")
            raise ValueError(f"Missing required information to create share for allocation {allocation_pk}.")
    else:
        logger.warning(f"No share creation task or client configured for resource {storage_handler.resource.name} associated with allocation {allocation_pk}. Cannot create share.")


def get_storage_handler(allocation_id):
    allocation = Allocation.objects.get(id=allocation_id)
    resources =  allocation.resources.filter(resource_type__name="Storage")
    if resources.count() != 1:
        logger.warning(f"Allocation {allocation.pk} is associated with {resources.count()} storage resources. Expected exactly 1. Cannot determine which resource to use to get storage handler.")
        return None
    
    storage_handler = StorageHandler.objects.filter(resource=resources.first()).first()
    if not storage_handler:
        logger.warning(f"No StorageHandler configured for resource(s) associated with allocation {allocation.pk}. Cannot get storage handler.")
        return None
    return storage_handler, allocation