import datetime
import logging
from pathlib import Path

import requests
from coldfront.core.resource.models import Resource
from coldfront_utils import bytes_to_units, ttl_cache, update_allocation_attribute_value, validate_posix_path

from storage.constants import (
    QUOTA_ATTRIBUTE_NAME,
    QUOTA_IN_BYTES_ATTRIBUTE_NAME,
    QUOTA_REPORT_DATE_ATTRIBUTE_NAME,
    STORAGE_PLUGIN_STORAGE_UNITS,
)
from storage.utils import (
    ActiveDirectoryNotFoundError,
    TaskRetryScheduled,
    get_client_config,
    lookup_ad_entries,
    records_quota_update_state,
    records_share_creation_state,
    schedule_retry,
)
from storage.vast.errors import VastViewNotFoundError

logger = logging.getLogger(__name__)


def get_quotas_batch(resource_id, client_config_id):
    # get allocation info from vast api and update allocation attributes in coldfront
    resource = Resource.objects.get(id=resource_id)
    client_config = get_client_config(client_config_id)
    allocations = resource.allocation_set.distinct()
    for allocation in allocations:
        native_path_attr = allocation.allocationattribute_set.filter(
            allocation_attribute_type__name=client_config["native_path_attribute_name"]
        ).first()
        if native_path_attr:
            vast_path = native_path_attr.value.strip()  # remove any leading or trailing whitespace
            validate_posix_path(vast_path)
            logger.info(f"Getting quota for allocation {allocation.pk} with path {vast_path}")
            try:
                q = get_quota(vast_path, client_config_id)
                if "soft_limit" in q and q["soft_limit"] is not None:
                    current_quota = q["soft_limit"]
                elif "hard_limit" in q and q["hard_limit"] is not None:
                    current_quota = q["hard_limit"]
                else:
                    current_quota = 0
                report_date = datetime.datetime.now()  # VAST API does not provide a timestamp for when the quota information was last updated, so we will use the current time as the report date
                update_allocation_attribute_value(allocation, QUOTA_IN_BYTES_ATTRIBUTE_NAME, current_quota)
                update_allocation_attribute_value(
                    allocation,
                    QUOTA_ATTRIBUTE_NAME,
                    round(bytes_to_units(current_quota, STORAGE_PLUGIN_STORAGE_UNITS), 2),
                )
                update_allocation_attribute_value(allocation, QUOTA_REPORT_DATE_ATTRIBUTE_NAME, report_date.isoformat())

            except Exception as e:
                logger.error(f"Error getting quota info for allocation {allocation} with path {vast_path}: {e}")
        else:
            logger.warning(
                f"Allocation {allocation} does not have a vast_path attribute and will be skipped in quota retrieval task"
            )


def get_quota(native_path: str, client_config_id: str) -> dict:
    all_quotas = get_all_quotas(
        client_config_id
    )  # This function is decorated with @ttl_cache, so it will return cached data if available
    vast_path = native_path.strip()  # remove any leading or trailing whitespace
    validate_posix_path(vast_path)
    logger.info(f"Looking for quota with id {vast_path} in VAST quotas data")
    res = [q for q in all_quotas if q["path"] == vast_path]
    if len(res) > 0:
        return res[0]
    else:
        raise ValueError(f"No matching quota found in cached VAST quotas for path {vast_path}")


@ttl_cache(timeout=60 * 60)
def get_all_quotas(client_config_id: str) -> list:
    vc = get_vast_client(client_config_id)
    all_quotas = vc.get_quotas()
    retained_fields = ["path", "soft_limit", "hard_limit", "pretty_state"]
    return [{field: i[field] for field in retained_fields} for i in all_quotas]


@records_quota_update_state
def set_quota(native_path: str, quota_bytes: int, client_config_id: str, allocation_pk: int) -> None:
    """Task entry point: resize a quota. The decorator records the outcome on the allocation."""
    if not native_path or quota_bytes is None:
        raise ValueError("Missing a VAST Path attribute or quota attribute. Cannot set quota without these attributes.")

    vc = get_vast_client(client_config_id)
    vast_path = native_path.strip()  # remove any leading or trailing whitespace
    validate_posix_path(vast_path)  # validate the path before using it to set the quota
    quota_match = vc.get_quotas(path=Path(vast_path))
    if len(quota_match) == 0:
        raise ValueError(f"No existing quota found for path {vast_path}. Cannot set quota for this path.")
    logger.info(f"Updating quota for path {vast_path} to {quota_bytes / 10**12} TB")
    logger.debug(f"Quota match details: {quota_match[0]}")
    vc.update_quota_size(quota_match[0]["id"], quota_bytes)


@records_share_creation_state
def create_share(
    native_path: str, quota_bytes: int, owner: str, group: str, client_config_id: str, allocation_pk: int
) -> None:
    """Task entry point: provision a share. The decorator records the outcome on the allocation."""
    provision_share(native_path, quota_bytes, client_config_id)


def provision_share(native_path: str, quota_bytes: int, client_config_id: str) -> None:
    """Create the VAST view, quota and protected path for a path.

    Raises on failure and records no allocation state, so that callers which do more work after
    provisioning can own the share creation state for the whole of their own task.
    """
    vc = get_vast_client(client_config_id)
    params = get_vast_params(client_config_id)

    if not native_path or not quota_bytes:
        raise ValueError(
            "Missing a VAST Path attribute or quota attribute. Cannot create share without these attributes."
        )

    validate_posix_path(native_path.strip())  # validate the path before using it to set the quota
    vast_path = Path(
        native_path.strip()
    )  # convert to Path object for easier manipulation and to ensure consistent formatting
    # view create will create the directory
    view = vc.get_views(path=vast_path)
    if len(view) > 0:
        logger.warning(f"{vast_path} View already exists")
    else:
        share_name = None if not params.get("include_share") else f"{vast_path.name}$"
        logger.info(f"Creating view {vast_path}")
        vc.add_view(
            path=vast_path,
            protocols=params.get("protocols"),
            policy_id=params.get("view_policy_id"),
            share_name=share_name,
        )
    add_quota_and_protected_path(vc, params, vast_path, quota_bytes)


def add_quota_and_protected_path(vc, params: dict, vast_path: Path, quota_bytes: int) -> None:
    """Give a view its quota and protected path, if it does not already have them."""
    quota_obj = vc.get_quotas(path=vast_path)
    if len(quota_obj) > 0:
        logger.warning(f"{vast_path} Quota already exists")
    else:
        soft_limit = quota_bytes
        margin_percent = params.get("quota_margin_percent", 0)
        if margin_percent > 0:
            soft_limit = int(quota_bytes * (100 - margin_percent) / 100)
        logger.info(
            f"Creating quota for {vast_path} with hard limit {quota_bytes / 10**12} TB and soft limit {soft_limit / 10**12} TB"
        )
        vc.add_quota(name=vast_path.name, path=vast_path, hard_limit=quota_bytes, soft_limit=soft_limit)
    protected_path = vc.get_protected_paths(source_dir=vast_path)
    if len(protected_path) > 0:
        logger.warning(f"{vast_path} Protected path already exists")
    else:
        logger.info(
            f"Creating protected path for {vast_path} with snapshot name {params.get('snapshot_name_template').format(vast_path.name)}"
        )
        vc.add_protected_path(
            name=params.get("snapshot_name_template").format(vast_path.name),
            source_dir=vast_path,
            tenant_id=params.get("tenant_id"),
            protection_policy_id=params.get("protection_policy_id"),
        )


@records_share_creation_state
def create_smb_share(
    native_path: str,
    quota_bytes: int,
    owner: str,
    group: str,
    client_config_id: str,
    allocation_pk: int,
    retries=5,
    wait=5,
) -> None:
    """Task entry point: provision an SMB share. The decorator records the outcome.

    Waits for the group to reach AD, as the NFS share tasks do: it is retried every `wait` minutes,
    up to `retries` times, while the group or its GID isn't there yet.
    """
    try:
        provision_smb_share(native_path, quota_bytes, group, client_config_id)
    except ActiveDirectoryNotFoundError as e:
        schedule_retry(
            create_smb_share,
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


def provision_smb_share(native_path: str, quota_bytes: int, group: str, client_config_id: str) -> None:
    """Create the VAST SMB view, quota and protected path for a path.

    Raises on failure and records no allocation state, for the same reason as provision_share.
    """
    vc = get_vast_client(client_config_id)
    params = get_vast_params(client_config_id)
    _, group_info = lookup_ad_entries(group=group)
    if not native_path or not quota_bytes:
        raise ValueError(
            "Missing a VAST Path attribute or quota attribute. Cannot create share without these attributes."
        )

    validate_posix_path(native_path.strip())  # validate the path before using it to set the quota
    vast_path = Path(
        native_path.strip()
    )  # convert to Path object for easier manipulation and to ensure consistent formatting
    # view create will create the directory
    view = vc.get_views(path=vast_path)
    if len(view) > 0:
        logger.warning(f"{vast_path} View already exists")
    else:
        share_name = f"{vast_path.name}$"
        # create acls
        acls = [vc.create_acl_from_str(**params["smb_admin_acls"])]
        acls.append(
            vc.create_acl_from_str(
                **{
                    "perm": "FULL",
                    "grantee": "groups",
                    "sid_str": group_info.get("objectSid")[0],
                    "uid_or_gid": group_info.get("gidNumber", [None])[0],
                }
            )
        )
        logger.info(f"Creating SMB view {vast_path}")
        vc.add_view(
            path=vast_path,
            protocols=params.get("protocols"),
            policy_id=params.get("view_policy_id"),
            share_name=share_name,
            acls=acls,
        )
    add_quota_and_protected_path(vc, params, vast_path, quota_bytes)


def check_folder(native_path: str, client_config_id: str):
    vast_path = native_path.strip()  # remove any leading or trailing whitespace
    validate_posix_path(vast_path)  # validate the path before using it to get the folder
    vc = get_vast_client(client_config_id)
    try:
        return vc.get_folder(vast_path)
    except requests.exceptions.HTTPError as e:
        logger.error(f"Error checking folder for path {native_path} in VAST: {e}")
        raise VastViewNotFoundError(f"Folder not found for path {native_path} in VAST: {e}")


def get_vast_client(client_config_id: str):
    from vast_api_client import VASTClient

    client_config = get_client_config(client_config_id)
    return VASTClient(
        host=client_config.get("host"), user=client_config.get("user"), password=client_config.get("password")
    )


def get_vast_params(client_config_id: str):
    """Helper function to extract and validate parameters from the client config for a given client_id."""
    from vast_api_client import ProtocolEnum

    client_config = get_client_config(client_config_id)
    margin_percent = int(client_config.get("quota_margin_percent", 0))
    if margin_percent < 0 or margin_percent >= 100:
        logger.warning(
            f"Invalid quota margin percent {margin_percent} in VAST client config. It should be between 0 and 100. Defaulting to 0."
        )
        margin_percent = 0
    protocols = client_config.get("protocols", [])
    valid_protocols = []
    for protocol in protocols:
        try:
            valid_protocols.append(ProtocolEnum(protocol))
        except ValueError as e:
            logger.warning(f"Invalid protocol {protocol} in VAST client config.")
            raise e
    include_share = client_config.get("include_share", False)
    if not isinstance(include_share, bool):
        raise ValueError(f"Invalid include_share value {include_share} in VAST client config. It should be a boolean.")
    return {
        "include_share": include_share,
        "view_policy_id": int(client_config.get("view_policy_id")),
        "protection_policy_id": int(client_config.get("protection_policy_id")),
        "tenant_id": int(client_config.get("tenant_id")),
        "protocols": valid_protocols,
        "quota_margin_percent": margin_percent,
        "snapshot_name_template": str(client_config.get("snapshot_name_template")),
        "cluster_path_template": str(client_config.get("cluster_path_template", "/cluster/{directory_name}")),
        "smb_admin_acls": client_config.get("smb_admin_acls", {}),
    }


def native_path_to_cluster_path(vast_path: str, client_config_id: str) -> str:
    vast_params = get_vast_params(client_config_id=client_config_id)
    cluster_path_template = vast_params.get("cluster_path_template")
    return cluster_path_template.format(directory_name=Path(vast_path).name.lstrip("/").lower())
