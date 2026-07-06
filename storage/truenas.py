import datetime
import logging

from coldfront.core.resource.models import Resource
from coldfront.core.allocation.models import Allocation
from coldfront_utils import (bytes_to_units, 
                             update_allocation_attribute_value, 
                             validate_posix_path)
from coldfront_utils.util.ad_search import ADSearch
from .utils import update_allocation_attribute_value, get_client_config
from .constants import (QUOTA_ATTRIBUTE_NAME, QUOTA_IN_BYTES_ATTRIBUTE_NAME, 
                        QUOTA_REPORT_DATE_ATTRIBUTE_NAME, QUOTA_UPDATE_STATE_ATTRIBUTE_NAME, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 
                        STORAGE_PLUGIN_STORAGE_UNITS)

logger = logging.getLogger(__name__)


def set_quota(native_path: str, quota_bytes: int, client_config_id: str, allocation_pk: int) -> None:
    try:
        allocation = Allocation.objects.get(id=allocation_pk)
        tc = get_truenas_client(client_config_id)
        truenas_path = native_path.strip() # remove any leading or trailing whitespace
        validate_posix_path(truenas_path) # validate the path before using it to set the quota
        share_details = tc.get_dataset_info(truenas_path, details=True)
        if not share_details:
            raise ValueError(f"Dataset {truenas_path} does not exist. Please create it first.")
        # truenas path looks like f"/mnt/{conf['parent_dataset']}/{project_name}"
        logger.info(f"Setting quota for path {truenas_path} to {quota_bytes / 10**12} TB")
        tc.update_quota(truenas_path, quota_bytes)
        update_allocation_attribute_value(allocation, QUOTA_UPDATE_STATE_ATTRIBUTE_NAME, 'success')
    except Exception as e:
        logger.error(f"Error setting quota for path {native_path} in TrueNAS: {e}")
        update_allocation_attribute_value(allocation, QUOTA_UPDATE_STATE_ATTRIBUTE_NAME, 'failed')
        raise e



def get_quotas_batch(resource_id, client_config_id):
    tc = get_truenas_client(client_config_id)
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
                update_allocation_attribute_value(allocation, 
                                                  QUOTA_IN_BYTES_ATTRIBUTE_NAME, 
                                                  current_quota)
                update_allocation_attribute_value(allocation, QUOTA_ATTRIBUTE_NAME, str(round(bytes_to_units(current_quota, STORAGE_PLUGIN_STORAGE_UNITS), 2))) 
                update_allocation_attribute_value(allocation, QUOTA_REPORT_DATE_ATTRIBUTE_NAME, report_date.isoformat())

            except Exception as e:
                logger.error(f"Error getting quota info from TrueNAS for allocation {allocation} with path {storage_path}: {e}")
        else:
            logger.warning(f"Allocation {allocation} does not have a Storage Path attribute")


def create_share(native_path: str, quota_bytes: int, owner: str, group: str, client_config_id: str, allocation_pk: int) -> None:
    try:
        truenas_path = native_path.strip() # remove any leading or trailing whitespace
        validate_posix_path(truenas_path) # validate the path before using it to set the quota
        
        tc = get_truenas_client(client_config_id)
        # check if share exists
        logger.debug("checking share details...")
        share_details = tc.check_share_details(truenas_path, quota_bytes, 0, 0)
        # based on share details, request share creation
        if share_details['dataset_exists'] and share_details['quota_matches'] and share_details['starfish_share_exists'] and share_details['globus_share_exists']:
            logger.info(f"Share {truenas_path} already exists with quota {quota_bytes / 10**12} TB. No action needed.")
        else:
            logger.info(f"creating/updating share on tier2 for path {truenas_path} with quota {quota_bytes / 10**12} TB...")
            # get uid, gid, and quota for this allocation
            ad_search = ADSearch('', '')
            owner_results = ad_search.get_ad_user(owner)
            uid = owner_results.get('uidNumber', None)
            if uid is None:
                raise ValueError(f"Could not find UID for owner {owner} in AD")
            group_results = ad_search.get_ad_group(group)
            gid = group_results.get('gidNumber', None)
            if gid is None:
                raise ValueError(f"Could not find GID for group {group} in AD")
            tc.create_project_share(truenas_path, quota_bytes, uid, gid, create_dataset=(not share_details['dataset_exists']),
                                    create_globus_share=(not share_details['globus_share_exists']),
                                    create_starfish_share=(not share_details['starfish_share_exists']),
                                    create_gateway_share=(not share_details['gateway_share_exists']))
            logger.info(f"Share {truenas_path} created with quota {quota_bytes}")
        update_allocation_attribute_value(Allocation.objects.get(id=allocation_pk), SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'success')
    except Exception as e:
        logger.error(f"Error creating share for path {native_path} in TrueNAS: {e}")
        update_allocation_attribute_value(Allocation.objects.get(id=allocation_pk), SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'failed')
        raise e


def get_truenas_client(client_config_id):
    client_config = get_client_config(client_config_id)
    from truenas_utils import TrueNASClient 
    cl = TrueNASClient(client_config['api_key'], client_config['host'], client_config['parent_dataset'],
                        verify_ssl=client_config['verify_certs'])
    cl.starfish_hosts = client_config.get('starfish_hosts', [])
    cl.globus_hosts = client_config.get('globus_hosts', [])
    cl.gateway_hosts = client_config.get('gateway_hosts', [])
    return cl
