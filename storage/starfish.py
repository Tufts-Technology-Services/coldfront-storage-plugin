from datetime import datetime
import logging

from coldfront.plugins.account_signup.utils import ttl_cache
from coldfront.core.allocation.models import AllocationAttribute

from .utils import update_allocation_usage
from .constants import (STARFISH_HOST, 
                        STARFISH_TOKEN, 
                        STORAGE_PLUGIN_STARFISH_VOL_PATH_ATTRIBUTE_NAME)
logger = logging.getLogger(__name__)


def get_storage_usage_batch():
    """
    Task to get storage usage for all storage resources in Coldfront with valid 
    Starfish volume paths associated with their allocations
    This task will look for all allocation attributes with the name specified in 
    STORAGE_PLUGIN_STARFISH_VOL_PATH_ATTRIBUTE_NAME (default: "sf_vol_path"), 
    which should contain the volume path for a Starfish volume in the format 
    "volume_name:path/to/subfolder". It will then group these attributes by volume name, 
    query the Starfish API for usage information for all subfolders of that volume, 
    and update the usage for each allocation accordingly.
    """
    sf_allocations = AllocationAttribute.objects.filter(
        allocation_attribute_type__name=STORAGE_PLUGIN_STARFISH_VOL_PATH_ATTRIBUTE_NAME)
    volumes = set(i.split(':')[0] for i in sf_allocations.values_list("value", flat=True).distinct())
    for vol in volumes:
        vol_attributes = sf_allocations.filter(value__startswith=f"{vol}:")
        volume_data = get_starfish_usage_data_by_volume(vol)
        for vol_path in vol_attributes:
            usage, report_date = get_path_usage_data(volume_data, vol_path)
            if usage and report_date:
                update_allocation_usage(vol_path.allocation, usage, report_date)
            else:
                logger.warning(f"No matching subfolder found for allocation attribute with vol_path value {vol_path.value}")
    return True


@ttl_cache(timeout=60*60)
def get_starfish_usage_data_by_volume(volume: str) -> list:
    """
    Helper function to query Starfish API for usage data for all subfolders of a given volume. 
    Caches results to avoid redundant API calls.
    """
    from starfish_api_client import StarfishAPIClient # pylint: ignore=import-outside-toplevel
    
    sf = StarfishAPIClient(host=STARFISH_HOST, token=STARFISH_TOKEN)
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
    match = [i for i in volume_data if i['vol_path'] == vol_path]
    if match:
        usage = match[0]['logical_size']
        report_date = datetime.fromtimestamp(match[0]['sync'])
        return usage, report_date
    else:
        return None, None