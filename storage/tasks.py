import logging

from coldfront.core.allocation.models import Allocation, AllocationAttribute, AllocationAttributeType, AttributeType
from coldfront_utils import units_to_bytes, update_allocation_attribute_value
from django_q.tasks import async_task

from storage.constants import (
    QUOTA_ATTRIBUTE_NAME,
    QUOTA_UPDATE_TASK_ID_ATTRIBUTE_NAME,
    SHARE_CREATION_TASK_ID_ATTRIBUTE_NAME,
    STORAGE_LOG_ONLY,
    STORAGE_PLUGIN_STORAGE_UNITS,
    STORAGE_RESOURCE_TYPE_NAME,
    ProvisionStatus,
)
from storage.directory_structure.tasks import create_folders
from storage.models import StorageHandler
from storage.utils import (
    get_allocation_group,
    get_attribute_value,
    get_client_config,
    set_quota_update_state,
    set_share_creation_state,
)

logger = logging.getLogger(__name__)


def get_storage_usage_batch():
    """
    Task to get storage usage from storage systems for all storage resources in Coldfront with StorageHandlers
    attribute names: usage_in_bytes, usage_report_date, vast_path, Storage Path
    """
    if STORAGE_LOG_ONLY:
        logger.info(
            "STORAGE_LOG_ONLY is set to True. Skipping actual retrieval of storage usage and just logging info."
        )

    handlers = StorageHandler.objects.all()

    for storage_type in handlers:
        get_usage_task = storage_type.get_usage_batch_task if storage_type else None
        if get_usage_task is None:
            logger.warning(f"No usage retrieval task configured for storage system type '{storage_type.resource.name}'")
            continue
        logger.info(f"Getting usage for resource {storage_type.resource.name}")

        if STORAGE_LOG_ONLY:
            logger.info("--STORAGE_LOG_ONLY")
            logger.info(
                f"Would call task '{get_usage_task}' for resource {storage_type.resource.name} with client id: {storage_type.usage_client_id}"
            )
            continue
        async_task(get_usage_task, storage_type.resource.id, storage_type.usage_client_id)


def get_storage_quotas_batch():
    """
    Task to get storage quotas from storage systems for all storage resources in Coldfront with StorageHandlers
    attribute names: quota_report_date, vast_path, Storage Path, Storage Quota (TB)
    """
    if STORAGE_LOG_ONLY:
        logger.info(
            "STORAGE_LOG_ONLY is set to True. Skipping actual retrieval of storage quotas and just logging info."
        )

    handlers = StorageHandler.objects.all()

    for storage_type in handlers:
        get_quotas_task = storage_type.get_quotas_batch_task if storage_type else None
        if get_quotas_task is None:
            logger.warning(f"No quota task configured for storage system type '{storage_type.resource.name}'")
            continue
        logger.info(f"Getting quotas for resource {storage_type.resource.name}")

        if STORAGE_LOG_ONLY:
            logger.info("--STORAGE_LOG_ONLY")
            logger.info(
                f"Would call task '{get_quotas_task}' for resource {storage_type.resource.name} with client id: {storage_type.quota_client_id}"
            )
            continue
        async_task(get_quotas_task, storage_type.resource.id, storage_type.quota_client_id)


def set_storage_quota(allocation_pk: int, allocation_change_id=None, allocation_attribute_change_id=None):
    """
    Task to update storage quota for an allocation. This is intended to be called when an allocation is created or updated for a resource, in order to ensure that the storage quota for the allocation is updated in the storage system accordingly.
    """
    if STORAGE_LOG_ONLY:
        logger.info("STORAGE_LOG_ONLY is set to True. Skipping actual update of storage quota and just logging info.")

    allocation = Allocation.objects.get(id=allocation_pk)
    set_quota_update_state(allocation, ProvisionStatus.STARTED, sender=set_storage_quota)
    storage_handler = get_storage_handler(allocation)
    if storage_handler and storage_handler.set_quota_task and storage_handler.quota_client_id:
        client_config = get_client_config(storage_handler.quota_client_id)
        new_quota = (
            allocation.allocationattribute_set.filter(allocation_attribute_type__name=QUOTA_ATTRIBUTE_NAME)
            .first()
            .value
        )  # todo: make sure this contains the new value.
        new_quota_bytes = units_to_bytes(float(new_quota), units=STORAGE_PLUGIN_STORAGE_UNITS)
        native_path = (
            allocation.allocationattribute_set.filter(
                allocation_attribute_type__name=client_config["native_path_attribute_name"]
            )
            .first()
            .value
        )
        if native_path and new_quota_bytes:
            if STORAGE_LOG_ONLY:
                set_quota_update_state(allocation, ProvisionStatus.SUCCESSFUL, sender=set_storage_quota)
                logger.info("--STORAGE_LOG_ONLY")
                logger.info(
                    f"Would call task '{storage_handler.set_quota_task}' for allocation {allocation_pk} with native path: {native_path} and new quota (bytes): {new_quota_bytes}"
                )
                return
            task_id = async_task(
                storage_handler.set_quota_task,
                native_path,
                new_quota_bytes,
                storage_handler.quota_client_id,
                allocation_pk,
            )
            update_allocation_attribute_value(allocation, QUOTA_UPDATE_TASK_ID_ATTRIBUTE_NAME, task_id)
            logger.debug(f"Started async task {task_id} to update storage quota for allocation {allocation_pk}.")
        else:
            logger.error(f"Missing required information to update quota for allocation {allocation_pk}.")
            error = ValueError(f"Missing required information to update quota for allocation {allocation_pk}.")
            set_quota_update_state(
                allocation, ProvisionStatus.FAILED, "missing information", sender=set_storage_quota, exception=error
            )
            raise error
    else:
        # storage_handler may be None here, so the message can't name its resource
        logger.warning(
            f"No quota update task or client configured for the storage resource of allocation {allocation_pk}. Cannot update storage quota."
        )
        set_quota_update_state(
            allocation, ProvisionStatus.FAILED, "no quota update task or client configured", sender=set_storage_quota
        )


def create_share(allocation_pk: int):
    if STORAGE_LOG_ONLY:
        logger.info("STORAGE_LOG_ONLY is set to True. Skipping actual creation of storage share and just logging info.")
    allocation = Allocation.objects.get(id=allocation_pk)
    set_share_creation_state(allocation, ProvisionStatus.STARTED, sender=create_share)
    storage_handler = get_storage_handler(allocation)

    if storage_handler and storage_handler.create_share_task and storage_handler.create_client_id:
        client_config = get_client_config(storage_handler.create_client_id)
        native_path = (
            allocation.allocationattribute_set.filter(
                allocation_attribute_type__name=client_config["native_path_attribute_name"]
            )
            .first()
            .value
        )
        quota_bytes = units_to_bytes(
            float(
                allocation.allocationattribute_set.filter(allocation_attribute_type__name=QUOTA_ATTRIBUTE_NAME)
                .first()
                .value
            ),
            units=STORAGE_PLUGIN_STORAGE_UNITS,
        )
        owner = allocation.project.pi.username
        group = get_allocation_group(allocation)
        if group and owner and native_path and quota_bytes:
            if STORAGE_LOG_ONLY:
                set_share_creation_state(allocation, ProvisionStatus.SUCCESSFUL, sender=create_share)
                logger.info("--STORAGE_LOG_ONLY")
                logger.info(
                    f"Would call task '{storage_handler.create_share_task}' for allocation {allocation_pk} with native path: {native_path}, quota (bytes): {quota_bytes}, owner: {owner}, and group: {group}"
                )
                return
            task_id = async_task(
                storage_handler.create_share_task,
                native_path,
                quota_bytes,
                owner,
                group,
                storage_handler.create_client_id,
                allocation_pk,
            )
            update_allocation_attribute_value(allocation, SHARE_CREATION_TASK_ID_ATTRIBUTE_NAME, task_id)
            logger.debug(f"Started async task {task_id} to create share for allocation {allocation_pk}.")
        else:
            logger.error(f"Missing required information to create share for allocation {allocation_pk}.")
            error = ValueError(f"Missing required information to create share for allocation {allocation_pk}.")
            set_share_creation_state(
                allocation, ProvisionStatus.FAILED, "missing information", sender=create_share, exception=error
            )
            raise error
    else:
        set_share_creation_state(
            allocation, ProvisionStatus.FAILED, "no share creation task or client configured", sender=create_share
        )
        logger.warning(
            f"No share creation task or client configured for the storage resource of allocation {allocation_pk}. Cannot create share."
        )


def create_directory(allocation_pk: int, structure_type: str):
    task_id = async_task(create_folders, allocation_pk, structure_type)
    logger.debug(f"Started async task {task_id} to create directory for allocation {allocation_pk}.")


def get_storage_handler(allocation):
    resources = allocation.resources.filter(resource_type__name=STORAGE_RESOURCE_TYPE_NAME)
    if resources.count() != 1:
        logger.warning(
            f"Allocation {allocation.pk} is associated with {resources.count()} storage resources. Expected exactly 1. Cannot determine which resource to use to get storage handler."
        )
        return None

    storage_handler = StorageHandler.objects.filter(resource=resources.first()).first()
    if not storage_handler:
        logger.warning(
            f"No StorageHandler configured for resource(s) associated with allocation {allocation.pk}. Cannot get storage handler."
        )
        return None
    return storage_handler


def add_attributes_to_new_storage_allocation(allocation_pk: int):
    allocation = Allocation.objects.get(id=allocation_pk)
    storage_handler = get_storage_handler(allocation)
    client_ids = set(
        [storage_handler.usage_client_id, storage_handler.quota_client_id, storage_handler.create_client_id]
    )
    for client_id in client_ids:
        client_config = get_client_config(client_id)
        try:
            attribute_type = AllocationAttributeType.objects.get(name=client_config["native_path_attribute_name"])
        except AllocationAttributeType.DoesNotExist:
            logger.warning(
                f"AllocationAttributeType '{client_config['native_path_attribute_name']}' does not exist. Creating a new one."
            )
            attribute_type = AllocationAttributeType.objects.create(
                name=client_config["native_path_attribute_name"],
                attribute_type=AttributeType.objects.get(name="Text"),
                is_required=True,
                is_unique=True,
                is_changeable=False,
                is_private=True,
            )  # create the attribute type if it does not exist

        attribute_template = client_config.get("native_path_attribute_template", None)
        if not AllocationAttribute.objects.filter(
            allocation=allocation, allocation_attribute_type=attribute_type
        ).exists():
            if attribute_template:
                AllocationAttribute.objects.create(
                    allocation_attribute_type=attribute_type,
                    value=get_attribute_value(allocation.id, attribute_template),
                    allocation=allocation,
                )
            else:
                AllocationAttribute.objects.create(
                    allocation_attribute_type=attribute_type, value="", allocation=allocation
                )
