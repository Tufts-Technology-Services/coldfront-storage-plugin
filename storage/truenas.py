import datetime
import logging

from coldfront.core.resource.models import Resource
from coldfront_utils import bytes_to_units, update_allocation_attribute_value, validate_posix_path

from .utils import update_allocation_attribute_value
from .constants import QUOTA_ATTRIBUTE_NAME, QUOTA_REPORT_DATE_ATTRIBUTE_NAME, STORAGE_PLUGIN_STORAGE_UNITS

logger = logging.getLogger(__name__)


def set_quota(native_path: str, quota_bytes: int, client_config: dict):
    tc = get_truenas_client(client_config)
    truenas_path = native_path.strip() # remove any leading or trailing whitespace
    validate_posix_path(truenas_path) # validate the path before using it to set the quota
    share_details = tc.get_dataset_info(truenas_path, details=True)
    if not share_details:
        raise ValueError(f"Dataset {truenas_path} does not exist. Please create it first.")
    # truenas path looks like f"/mnt/{conf['parent_dataset']}/{project_name}"
    tc.update_quota(truenas_path, quota_bytes)


def get_quotas_batch(resource_id, client_config):
    tc = get_truenas_client(client_config)
    all_quotas = tc.get_all_datasets()

    resource = Resource.objects.get(id=resource_id)
    allocations = resource.allocation_set.distinct()
    # get allocation info from TrueNAS API and update allocation attributes in coldfront
    for allocation in allocations:
        storage_path_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name='truenas_path').first()
        if storage_path_attr:
            storage_path = storage_path_attr.value
            try:
                r = [q for q in all_quotas if q['mountpoint'] == storage_path]
                current_quota = r[0]['quota']
                report_date = datetime.datetime.now() # TrueNAS API does not provide a timestamp for when the quota information was last updated, so we will use the current time as the report date
                update_allocation_attribute_value(allocation, QUOTA_ATTRIBUTE_NAME, str(round(bytes_to_units(current_quota, STORAGE_PLUGIN_STORAGE_UNITS), 2))) 
                update_allocation_attribute_value(allocation, QUOTA_REPORT_DATE_ATTRIBUTE_NAME, report_date.isoformat())

            except Exception as e:
                logger.error(f"Error getting quota info from TrueNAS for allocation {allocation} with path {storage_path}: {e}")
        else:
            logger.warning(f"Allocation {allocation} does not have a Storage Path attribute")


def create_share(native_path: str, quota_bytes: int, owner: str, group: str, client_config: dict):
    truenas_path = native_path.strip() # remove any leading or trailing whitespace
    validate_posix_path(truenas_path) # validate the path before using it to set the quota
    
    tc = get_truenas_client(client_config)
    # check if share exists
    logger.debug("checking share details...")
    share_details = tc.check_share_details(truenas_path, quota_bytes, 0, 0)
    # based on share details, request share creation
    if share_details['dataset_exists'] and share_details['quota_matches'] and share_details['starfish_share_exists'] and share_details['globus_share_exists']:
        logger.info(f"Share {truenas_path} already exists with quota {quota_bytes}. No action needed.")
    else:
        logger.info("creating/updating share on tier2...")
        # get uid, gid, and quota for this allocation
        uid = get_user_id(owner)
        gid = get_group_id(group)
        tc.create_project_share(truenas_path, quota_bytes, uid, gid, create_dataset=(not share_details['dataset_exists']),
                                create_globus_share=(not share_details['globus_share_exists']),
                                create_starfish_share=(not share_details['starfish_share_exists']))
        logger.info(f"Share {truenas_path} created with quota {quota_bytes}")


def get_truenas_client(client_config):
    from truenas_utils import TrueNASClient 
    return TrueNASClient(client_config['api_key'], client_config['host'], client_config['parent_dataset'],
                        verify_ssl=client_config['verify_certs'], starfish_hosts=client_config['starfish_hosts'],
                        globus_hosts=client_config['globus_hosts'])
