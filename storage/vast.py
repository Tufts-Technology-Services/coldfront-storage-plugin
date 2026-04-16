import datetime
import logging
import os
from pathlib import Path
from coldfront.core.resource.models import Resource
from coldfront_utils import ttl_cache, bytes_to_units, update_allocation_attribute_value, validate_posix_path
from .constants import QUOTA_ATTRIBUTE_NAME, QUOTA_REPORT_DATE_ATTRIBUTE_NAME, STORAGE_PLUGIN_STORAGE_UNITS

logger = logging.getLogger(__name__)


def get_quota_batch(resource_id, client_config):
    # get allocation info from vast api and update allocation attributes in coldfront
    resource = Resource.objects.get(id=resource_id)
    allocations = resource.allocation_set.distinct()
    for allocation in allocations:
        native_path_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name=client_config['native_path_attribute_name']).first()
        if native_path_attr:
            vast_path = native_path_attr.value.strip() # remove any leading or trailing whitespace
            validate_posix_path(vast_path)
            logger.info(f"Getting quota for allocation {allocation.pk} with path {vast_path}")
            try:
                q = get_quota(vast_path, client_config)
                current_quota = q['soft_limit']
                report_date = datetime.datetime.now() # VAST API does not provide a timestamp for when the quota information was last updated, so we will use the current time as the report date
                update_allocation_attribute_value(allocation, 
                                                  QUOTA_ATTRIBUTE_NAME, 
                                                  round(bytes_to_units(current_quota, STORAGE_PLUGIN_STORAGE_UNITS), 2))
                update_allocation_attribute_value(allocation, 
                                                  QUOTA_REPORT_DATE_ATTRIBUTE_NAME, 
                                                  report_date.isoformat())

            except Exception as e:
                logger.error(f"Error getting quota info for allocation {allocation} with path {vast_path}: {e}")
        else:
            logger.warning(f"Allocation {allocation} does not have a vast_path attribute and will be skipped in quota retrieval task")


def get_quota(native_path: str, client_config: dict) -> dict:
    all_quotas = get_all_quotas(client_config)  # This function is decorated with @ttl_cache, so it will return cached data if available
    vast_path = native_path.strip() # remove any leading or trailing whitespace
    validate_posix_path(vast_path)
    logger.info(f"Looking for quota with id {vast_path} in VAST quotas data")
    res = [q for q in all_quotas if q['path'] == vast_path]
    if len(res) > 0:      
        return res[0]
    else:
        raise ValueError(f"No matching quota found in cached VAST quotas for path {vast_path}")


@ttl_cache(timeout=60*60)
def get_all_quotas(client_config: dict) -> list:
    vc = get_vast_client(client_config)
    all_quotas = vc.get_quotas()
    retained_fields = ['path', 'soft_limit', 'hard_limit', 'pretty_state']
    return [{field: i[field] for field in retained_fields} for i in all_quotas]


def set_quota(native_path: str, quota_bytes: int, client_config: dict) -> None:
    vc = get_vast_client(client_config)
    if native_path and quota_bytes:
        vast_path = native_path.strip() # remove any leading or trailing whitespace
        validate_posix_path(vast_path) # validate the path before using it to set the quota
        quota_match = vc.get_quotas(path=Path(vast_path))
        if len(quota_match) == 0:
            logger.error(f"No existing quota found for path {vast_path}. Cannot set quota for this path.")
            raise ValueError(f"No existing quota found for path {vast_path}. Cannot set quota for this path.")
        logger.info(f"Updating quota for path {vast_path} to {quota_bytes} bytes")
        logger.info(f"Quota match details: {quota_match[0]}")
        vc.update_quota_size(quota_match[0]['id'], quota_bytes)
    else:
        logger.warning(f"Missing a VAST Path attribute or quota attribute. Cannot set quota without these attributes.")


def create_share(native_path: str, quota_bytes: int, owner: str, group: str, client_config: dict) -> None:
    vc = get_vast_client(client_config)
    params = get_vast_params(client_config)
    
    if native_path and quota_bytes:
        vast_path = native_path.strip() # remove any leading or trailing whitespace
        validate_posix_path(vast_path) # validate the path before using it to set the quota
        # view create will create the directory
        view = vc.get_views(path=vast_path)
        if len(view) > 0:
            logger.warning(f"{vast_path} View already exists")
        else:
            share_name = None if not params.get("include_share") else f"{os.path.basename(vast_path)}$"
            vc.add_view(path=vast_path, protocols=params.get("protocols"),
                        policy_id=params.get("view_policy_id"), share_name=share_name)
        quota_obj = vc.get_quotas(path=vast_path)
        if len(quota_obj) > 0:
            logger.warning(f"{vast_path} Quota already exists")
        else:
            soft_limit = quota_bytes           
            margin_percent = params.get("quota_margin_percent", 0)
            if margin_percent > 0:
                soft_limit = int(quota_bytes * (100 - margin_percent) / 100)
            vc.add_quota(name=vast_path.name,
                         path=vast_path,
                        hard_limit=quota_bytes,
                        soft_limit=soft_limit)
        protected_path = vc.get_protected_paths(source_dir=vast_path)
        if len(protected_path) > 0:
            logger.warning(f"{vast_path} Protected path already exists")
        else:
            vc.add_protected_path(name=params.get("snapshot_name_template").format(os.path.basename(vast_path)),
                                  source_dir=vast_path,
                                  tenant_id=params.get("tenant_id"),
                                  protection_policy_id=params.get("protection_policy_id"))
    else:
        logger.warning(f"Missing a VAST Path attribute or quota attribute. Cannot set quota without these attributes.")


def get_vast_client(client_config: dict):
    from vast_api_client import VASTClient
    return VASTClient(host=client_config.get("host"),
                    user=client_config.get("user"),
                    password=client_config.get("password"))


def get_vast_params(client_config: dict):
    """Helper function to extract and validate parameters from the client config for a given client_id.
    """
    from vast_api_client import ProtocolEnum
    margin_percent = int(client_config.get("quota_margin_percent", 0))
    if margin_percent < 0 or margin_percent >= 100:
        logger.warning(f"Invalid quota margin percent {margin_percent} in VAST client config. It should be between 0 and 100. Defaulting to 0.")
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
        "snapshot_name_template": str(client_config.get("snapshot_name_template"))
    }
