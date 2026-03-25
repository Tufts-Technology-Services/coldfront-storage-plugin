
import datetime
import logging
from coldfront.core.allocation.models import Allocation
from coldfront.plugins.account_signup.utils import ttl_cache

from .constants import QUOTA_REPORT_DATE_ATTRIBUTE_NAME, QUOTA_ATTRIBUTE_NAME
from .utils import (units_to_bytes, bytes_to_units, update_allocation_attribute_value, get_client_config, validate_posix_path)

logger = logging.getLogger(__name__)


def get_quota_batch(resources, client_id):
    # allocations of this resource
    allocations = Allocation.objects.filter(resources__in=resources).distinct()
    # get allocation info from vast api and update allocation attributes in coldfront
    for allocation in allocations:
        vast_path_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name='vast_path').first()
        if vast_path_attr:
            vast_path = vast_path_attr.value
            try:
                q = get_quota(vast_path, client_id)
                current_quota = q['soft_limit']
                report_date = datetime.datetime.now() # VAST API does not provide a timestamp for when the quota information was last updated, so we will use the current time as the report date
                update_allocation_attribute_value(allocation, QUOTA_ATTRIBUTE_NAME, round(float(bytes_to_units(current_quota)), 2))
                update_allocation_attribute_value(allocation, QUOTA_REPORT_DATE_ATTRIBUTE_NAME, report_date.isoformat())

            except Exception as e:
                logger.error(f"Error getting quota info from VAST for allocation {allocation} with path {vast_path}: {e}")
        else:
            logger.warning(f"Allocation {allocation} does not have a vast_path attribute")


def get_quota(vast_path, client_id):
    all_quotas = get_all_quotas(client_id)  # This function is decorated with @ttl_cache, so it will return cached data if available
    res = [q for q in all_quotas if q['path'] == vast_path]
    if len(res) > 0:         
        return res[0]
    else:
        raise ValueError(f"No matching quota found in cached VAST quotas for path {vast_path}")


@ttl_cache(timeout=60*60)
def get_all_quotas(client_id):
    vc = get_vast_client(client_id)
    all_quotas = vc.get_quotas()
    return all_quotas


def set_quota(allocation_id, client_id):
    allocation = Allocation.objects.get(id=allocation_id)
    vc = get_vast_client(client_id)
    vast_path_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name='vast_path').first()
    quota_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name=QUOTA_ATTRIBUTE_NAME).first()
    
    if vast_path_attr and quota_attr:
        vast_path = vast_path_attr.value.strip() # remove any leading or trailing whitespace
        validate_posix_path(vast_path) # validate the path before using it to set the quota
        quota_bytes = units_to_bytes(float(quota_attr.value))
        vc.update_quota_size(vast_path, quota_bytes)
    else:
        logger.warning(f"Allocation {allocation} is missing a VAST Path attribute or quota attribute. Cannot set quota without these attributes.")


def create_share(allocation_id, client_id):
    allocation = Allocation.objects.get(id=allocation_id)
    vc = get_vast_client(client_id)
    params = get_vast_params(client_id)
    vast_path_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name='vast_path').first()
    quota_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name=QUOTA_ATTRIBUTE_NAME).first()
    
    if vast_path_attr and quota_attr:
        vast_path = vast_path_attr.value.strip() # remove any leading or trailing whitespace
        validate_posix_path(vast_path) # validate the path before using it to set the quota
        quota_bytes = units_to_bytes(float(quota_attr.value))
            # view policy NFSDefault id = 3, share_name is None for NFS
        # view create will create the directory
        view = vc.get_views(path=vast_path)
        if len(view) > 0:
            logger.warning(f"{vast_path} View already exists")
        else:
            share_name = None if not params.get("include_share") else f"{allocation.id}$"
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
            vc.add_protected_path(name=params.get("snapshot_name_template").format(vast_path.name), 
                                  source_dir=vast_path, 
                                  tenant_id=params.get("tenant_id"), 
                                  protection_policy_id=params.get("protection_policy_id"))
    else:
        logger.warning(f"Allocation {allocation} is missing a VAST Path attribute or quota attribute. Cannot set quota without these attributes.")


def get_vast_client(client_id):
    # pylint: disable=import-outside-toplevel
    from vast_api_client import VASTClient
    client_config = get_client_config(client_id)
    return VASTClient(host=client_config.get("host"), 
                    user=client_config.get("user"), 
                    password=client_config.get("password"))


def get_vast_params(client_id):
    """Helper function to extract and validate parameters from the client config for a given client_id.
    """
    # pylint: disable=import-outside-toplevel
    from vast_api_client import ProtocolEnum
    client_config = get_client_config(client_id)
    margin_percent = int(client_config.get("quota_margin_percent", 0))
    if margin_percent < 0 or margin_percent >= 100:
        logger.warning(f"Invalid quota margin percent {margin_percent} in VAST client config for client_id {client_id}. It should be between 0 and 100. Defaulting to 0.")
        margin_percent = 0
    protocols = client_config.get("protocols", [])
    valid_protocols = []
    for protocol in protocols:
        try:
            valid_protocols.append(ProtocolEnum(protocol))
        except ValueError as e:
            logger.warning(f"Invalid protocol {protocol} in VAST client config for client_id {client_id}.")
            raise e
    include_share = client_config.get("include_share", False)
    if not isinstance(include_share, bool):
        raise ValueError(f"Invalid include_share value {include_share} in VAST client config for client_id {client_id}. It should be a boolean.")
    return {
        "include_share": include_share,
        "view_policy_id": int(client_config.get("view_policy_id")),
        "protection_policy_id": int(client_config.get("protection_policy_id")),
        "tenant_id": int(client_config.get("tenant_id")),
        "protocols": valid_protocols,
        "quota_margin_percent": margin_percent,
        "snapshot_name_template": str(client_config.get("snapshot_name_template"))
    }