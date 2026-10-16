import datetime
import logging

from coldfront.core.resource.models import Resource
from coldfront_utils import bytes_to_units, update_allocation_attribute_value, validate_posix_path

from .constants import (
    QUOTA_ATTRIBUTE_NAME,
    QUOTA_IN_BYTES_ATTRIBUTE_NAME,
    QUOTA_REPORT_DATE_ATTRIBUTE_NAME,
    STORAGE_PLUGIN_STORAGE_UNITS,
)
from .utils import (
    ActiveDirectoryNotFoundError,
    TaskRetryScheduled,
    get_client_config,
    lookup_ad_entries,
    records_quota_update_state,
    records_share_creation_state,
    schedule_retry,
)

logger = logging.getLogger(__name__)


@records_quota_update_state
def set_quota(native_path: str, quota_bytes: int, client_config_id: str, allocation_pk: int) -> None:
    """Task entry point: resize a quota. The decorator records the outcome on the allocation."""
    tc = get_truenas_client(client_config_id)
    truenas_path = native_path.strip()  # remove any leading or trailing whitespace
    validate_posix_path(truenas_path)  # validate the path before using it to set the quota
    share_details = tc.get_dataset_info(truenas_path, details=True)
    if not share_details:
        raise ValueError(f"Dataset {truenas_path} does not exist. Please create it first.")
    # truenas path looks like f"/mnt/{conf['parent_dataset']}/{project_name}"
    logger.info(f"Setting quota for path {truenas_path} to {quota_bytes / 10**12} TB")
    tc.update_quota(truenas_path, quota_bytes)


def get_quotas_batch(resource_id, client_config_id):
    tc = get_truenas_client(client_config_id)
    all_quotas = tc.get_all_datasets()

    resource = Resource.objects.get(id=resource_id)
    allocations = resource.allocation_set.distinct()
    # get allocation info from TrueNAS API and update allocation attributes in coldfront
    for allocation in allocations:
        storage_path_attr = allocation.allocationattribute_set.filter(
            allocation_attribute_type__name="truenas_path"
        ).first()
        if storage_path_attr:
            storage_path = storage_path_attr.value
            try:
                r = [q for q in all_quotas if q["mountpoint"] == storage_path]
                current_quota = r[0]["quota"]
                report_date = datetime.datetime.now()  # TrueNAS API does not provide a timestamp for when the quota information was last updated, so we will use the current time as the report date
                update_allocation_attribute_value(allocation, QUOTA_IN_BYTES_ATTRIBUTE_NAME, current_quota)
                update_allocation_attribute_value(
                    allocation,
                    QUOTA_ATTRIBUTE_NAME,
                    str(round(bytes_to_units(current_quota, STORAGE_PLUGIN_STORAGE_UNITS), 2)),
                )
                update_allocation_attribute_value(allocation, QUOTA_REPORT_DATE_ATTRIBUTE_NAME, report_date.isoformat())

            except Exception as e:
                logger.error(
                    f"Error getting quota info from TrueNAS for allocation {allocation} with path {storage_path}: {e}"
                )
        else:
            logger.warning(f"Allocation {allocation} does not have a Storage Path attribute")


@records_share_creation_state
def create_share(
    native_path: str, quota_bytes: int, owner: str, group: str, client_config_id: str, allocation_pk: int
) -> None:
    """Task entry point: provision a share. The decorator records the outcome on the allocation."""
    provision_share(native_path, quota_bytes, owner, group, client_config_id)


def provision_share(native_path: str, quota_bytes: int, owner: str, group: str, client_config_id: str) -> None:
    """Create the TrueNAS dataset, quota and shares for a path.

    Raises on failure and records no allocation state, so that callers which retry or do more work
    can own the share creation state for the whole of their own task.
    """
    truenas_path = native_path.strip()  # remove any leading or trailing whitespace
    validate_posix_path(truenas_path)  # validate the path before using it to set the quota

    tc = get_truenas_client(client_config_id)
    # check if share exists
    logger.debug("checking share details...")
    share_details = tc.check_share_details(truenas_path, quota_bytes, 0, 0)
    # based on share details, request share creation
    if (
        share_details["dataset_exists"]
        and share_details["quota_matches"]
        and share_details["starfish_share_exists"]
        and share_details["globus_share_exists"]
    ):
        logger.info(f"Share {truenas_path} already exists with quota {quota_bytes / 10**12} TB. No action needed.")
        return

    logger.info(f"creating/updating share on tier2 for path {truenas_path} with quota {quota_bytes / 10**12} TB...")
    # the owner's uid and the group's gid for the dataset
    user_entry, group_entry = lookup_ad_entries(owner, group)
    tc.create_project_share(
        truenas_path,
        quota_bytes,
        user_entry["uidNumber"][0],
        group_entry["gidNumber"][0],
        create_dataset=(not share_details["dataset_exists"]),
        create_globus_share=(not share_details["globus_share_exists"]),
        create_starfish_share=(not share_details["starfish_share_exists"]),
        create_gateway_share=(not share_details["gateway_share_exists"]),
    )
    logger.info(f"Share {truenas_path} created with quota {quota_bytes}")


@records_share_creation_state
def create_share_wait_for_group(
    native_path: str,
    quota_bytes: int,
    owner: str,
    group: str,
    client_config_id: str,
    allocation_pk: int,
    retries=5,
    wait=5,
):
    try:
        # the action, not the create_share task: nesting the tasks would nest their state handling
        provision_share(native_path, quota_bytes, owner, group, client_config_id)
    except ActiveDirectoryNotFoundError as e:
        schedule_retry(
            create_share_wait_for_group,
            e,
            retries=retries,
            wait=wait,
            native_path=native_path,
            quota_bytes=quota_bytes,
            owner=owner,
            group=group,
            client_config_id=client_config_id,
            allocation_pk=allocation_pk,
        )
        raise TaskRetryScheduled(str(e)) from e


def get_truenas_client(client_config_id):
    client_config = get_client_config(client_config_id)
    from truenas_utils import TrueNASClient

    cl = TrueNASClient(
        client_config["api_key"],
        client_config["host"],
        client_config["parent_dataset"],
        verify_ssl=client_config["verify_certs"],
    )
    cl.starfish_hosts = client_config.get("starfish_hosts", [])
    cl.globus_hosts = client_config.get("globus_hosts", [])
    cl.gateway_hosts = client_config.get("gateway_hosts", [])
    return cl
