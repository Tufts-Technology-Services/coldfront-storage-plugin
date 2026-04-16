from datetime import datetime
import logging

from coldfront.core.allocation.models import AllocationAttribute
from coldfront.core.resource.models import Resource
from coldfront_utils import ttl_cache
from .utils import update_allocation_usage, get_client_config

logger = logging.getLogger(__name__)


def get_storage_usage_batch(resource_id=None, client_config=None):
    """
    Task to get storage usage for all storage resources in Coldfront with valid 
    Starfish volume paths associated with their allocations
    This task will look for all allocation attributes with the name specified in 
    config['native_path_attribute_name'] (default: "sf_vol_path"), 
    which should contain the volume path for a Starfish volume in the format 
    "volume_name:path/to/subfolder". It will then group these attributes by volume name, 
    query the Starfish API for usage information for all subfolders of that volume, 
    and update the usage for each allocation accordingly.
    """
    resource = Resource.objects.get(id=resource_id)
    # to take full advantage of caching, we need to group by volume
    path_attr = client_config['native_path_attribute_name']

    # log a warning for Allocations of this resource that are missing a native path attribute 
    resource_allocations = resource.allocation_set.distinct()
    for alloc in resource_allocations:
        if not alloc.allocationattribute_set.filter(allocation_attribute_type__name=path_attr).exists():
            logger.warning(f"Allocation {alloc.pk} of resource {resource.name} is missing required native path attribute '{path_attr}' and will be skipped in Starfish usage retrieval task.")
        else:
            # check value of native path attribute and log a warning if it does not match expected format of "volume_name:path/to/subfolder"
            native_path_value = alloc.allocationattribute_set.filter(allocation_attribute_type__name=path_attr).first().value
            try:
                validate_starfish_path(native_path_value)
            except ValueError as e:
                logger.warning(f"Allocation {alloc.pk} of resource {resource.name} has native path attribute value '{native_path_value}' that is invalid: {e}. This allocation will be skipped in Starfish usage retrieval task.")

    # now filter to only include allocations with valid native path attributes
    sf_attrs = AllocationAttribute.objects.filter(allocation__resource=resource,
        allocation_attribute_type__name=path_attr)
    # get a set of all unique volumes
    volumes = set(i.split(':')[0] for i in sf_attrs.values_list("value", flat=True).distinct())

    for vol in volumes:
        vol_attributes = sf_attrs.filter(value__startswith=f"{vol}:")
        volume_data = get_starfish_usage_data_by_volume(vol, client_config['client_key'])
        for vol_path in vol_attributes:
            try:
                validate_starfish_path(vol_path.value)
            except ValueError as e:
                logger.warning(f"Skipping allocation {vol_path.allocation.pk} with invalid Starfish path '{vol_path.value}': {e}")
                continue
            usage, report_date = get_path_usage_data(volume_data, vol_path.value)
            if usage and report_date:
                logger.info(f"Updating usage for allocation {vol_path.allocation.pk} with usage {usage} bytes and report date {report_date}")
                update_allocation_usage(vol_path.allocation, usage, report_date)
            else:
                logger.warning(f"No matching subfolder found for allocation attribute with vol_path value {vol_path.value}")
    return True


@ttl_cache(timeout=60*60)
def get_starfish_usage_data_by_volume(volume: str, client_key: str) -> list:
    """
    Helper function to query Starfish API for usage data for all subfolders of a given volume. 
    Caches results to avoid redundant API calls.
    """
    from starfish_api_client import StarfishAPIClient # pylint: ignore=import-outside-toplevel
    client_config = get_client_config(client_key)
    sf = StarfishAPIClient(host=client_config['host'], token=client_config['api_key'])
    subfolder_response = sf.request_subfolder_query(volume)
    # we only need certain fields from the response, so we will extract those and store them in a list of dictionaries
    retained_fields = ['vol_path', 'logical_size', 'sync']
    subfolder_response = [{field: i[field] for field in retained_fields} for i in subfolder_response]
    return subfolder_response


def get_path_usage_data(volume_data, vol_path) -> tuple:
    """
    Helper function to extract usage data for a specific volume path from the results 
    of a Starfish API query for all subfolders of a volume.
    """
    match = [i for i in volume_data if i['vol_path'].lower() == vol_path.lower()]
    if match:
        usage = match[0]['logical_size']
        report_date = datetime.fromtimestamp(match[0]['sync'])
        return usage, report_date
    else:
        return None, None


def validate_starfish_path(vol_path):
    if not vol_path:
        raise ValueError("Volume path cannot be empty")
    if ':' not in vol_path:
        raise ValueError(f"Invalid Starfish volume path '{vol_path}'. Expected format is 'volume_name:path/to/subfolder'")
    volume, subfolder = vol_path.split(':', 1)
    if not volume or not subfolder:
        raise ValueError(f"Invalid Starfish volume path '{vol_path}'. Expected format is 'volume_name:path/to/subfolder'")
