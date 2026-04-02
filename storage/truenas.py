import datetime

import logging

from coldfront.core.allocation.models import Allocation
from coldfront_utils import units_to_bytes, update_allocation_attribute_value, validate_posix_path

from .utils import update_allocation_attribute_value, get_client_config
from .constants import QUOTA_ATTRIBUTE_NAME, QUOTA_REPORT_DATE_ATTRIBUTE_NAME, STORAGE_PLUGIN_STORAGE_UNITS

logger = logging.getLogger(__name__)


def set_quota(allocation_id, client_id):
    allocation = Allocation.objects.get(id=allocation_id)
    tc = get_truenas_client(client_id)
    path_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name='truenas_path').first()
    quota_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name=QUOTA_ATTRIBUTE_NAME).first()
    
    if path_attr and quota_attr:
        truenas_path = path_attr.value.strip() # remove any leading or trailing whitespace
        validate_posix_path(truenas_path) # validate the path before using it to set the quota
        share_details = tc.get_dataset_info(truenas_path, details=True)
        if not share_details:
            raise ValueError(f"Dataset {truenas_path} does not exist. Please create it first.")
        # todo: check existing quota
        # truenas path looks like f"/mnt/{conf['parent_dataset']}/{project_name}"
        tc.update_quota(truenas_path, units_to_bytes(float(quota_attr.value), STORAGE_PLUGIN_STORAGE_UNITS))
    else:
        logger.warning(f"Allocation {allocation} is missing a TrueNAS Path attribute or quota attribute. Cannot set quota without these attributes.")


def get_quotas_batch(resources, client_config):
    tc = get_truenas_client(client_config)
    all_quotas = tc.get_all_datasets()

    # allocations of these resources
    allocations = Allocation.objects.filter(resources__in=resources).distinct()
    # get allocation info from TrueNAS API and update allocation attributes in coldfront
    for allocation in allocations:
        storage_path_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name='truenas_path').first()
        if storage_path_attr:
            storage_path = storage_path_attr.value
            try:
                r = [q for q in all_quotas if q['mountpoint'] == storage_path]
                current_quota = r[0]['quota']
                report_date = datetime.datetime.now() # TrueNAS API does not provide a timestamp for when the quota information was last updated, so we will use the current time as the report date
                update_allocation_attribute_value(allocation, QUOTA_ATTRIBUTE_NAME, str(round(units_to_bytes(current_quota, STORAGE_PLUGIN_STORAGE_UNITS), 2))) 
                update_allocation_attribute_value(allocation, QUOTA_REPORT_DATE_ATTRIBUTE_NAME, report_date.isoformat())

            except Exception as e:
                logger.error(f"Error getting quota info from TrueNAS for allocation {allocation} with path {storage_path}: {e}")
        else:
            logger.warning(f"Allocation {allocation} does not have a Storage Path attribute")


def create_share(allocation_id, client_config):
    allocation = Allocation.objects.get(id=allocation_id)
    path_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name='truenas_path').first()
    quota_attr = allocation.allocationattribute_set.filter(allocation_attribute_type__name=QUOTA_ATTRIBUTE_NAME).first()
    
    if not (path_attr and quota_attr):
        logger.warning(f"Allocation {allocation} is missing a TrueNAS Path attribute or quota attribute. Cannot create share without these attributes.")
        return

    truenas_path = path_attr.value.strip() # remove any leading or trailing whitespace
    validate_posix_path(truenas_path) # validate the path before using it to set the quota
    quota_bytes = units_to_bytes(float(quota_attr.value), STORAGE_PLUGIN_STORAGE_UNITS)
    
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
        project_owner = allocation.project.pi.user.username
        uid = get_user_id(project_owner)
        group = allocation.project.projectattribute_set.filter(proj_attr_type__name='Group').first().value
        gid = get_group_id(group)
        tc.create_project_share(truenas_path, quota_bytes, uid, gid, create_dataset=(not share_details['dataset_exists']),
                                create_globus_share=(not share_details['globus_share_exists']),
                                create_starfish_share=(not share_details['starfish_share_exists']))
        logger.info(f"Share {truenas_path} created with quota {quota_bytes}")
        # Update the allocation with the new share path
        update_allocation_attribute_value(allocation, 'truenas_path', truenas_path)


def get_truenas_client(client_id):
    from truenas_utils import TrueNASClient 
    client_config = get_client_config(client_id)
    return TrueNASClient(client_config['api_key'], client_config['host'], client_config['parent_dataset'],
                        verify_ssl=client_config['verify_certs'], starfish_hosts=client_config['starfish_hosts'],
                        globus_hosts=client_config['globus_hosts'])
