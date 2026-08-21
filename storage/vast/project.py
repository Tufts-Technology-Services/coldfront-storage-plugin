import logging
import datetime
from pathlib import Path

from django_q.tasks import Schedule, schedule

from coldfront.core.allocation.models import Allocation
from coldfront_utils import update_allocation_attribute_value
from coldfront_utils.util.ad_search import ADSearch
from storage.constants import SHARE_CREATION_STATE_ATTRIBUTE_NAME
from storage.utils import GroupNotFoundError, UIDNotFoundError, UserNotFoundError
from .vast import create_share, native_path_to_cluster_path
from storage.directory_structure.tasks import create_project_folders

logger = logging.getLogger(__name__)

def create_project_share(native_path: str, quota_bytes: int, owner: str, group: str, 
                         client_config_id: str, allocation_pk: int, retries=5, wait=5):
    """
    Create a share for the project in VAST and set the appropriate quota. Also creates 
    the project owner folder and shared folder within the project directory on the cluster 
    with appropriate permissions.
    Args:
        native_path (str): The native path for the share on VAST, e.g. /projects/<project_name>
        quota_bytes (int): The quota size in bytes to set for the share
        owner (str): The owner of the project, used for cluster folder ownership
        group (str): The group of the project, used for cluster folder group
        client_config_id (str): The client config id to use for connecting to VAST
        allocation_pk (int): The allocation primary key, used for logging and tracking purposes
    """
    create_share(native_path=native_path.lower(), quota_bytes=quota_bytes, 
                 owner=owner, group=group, client_config_id=client_config_id, 
                 allocation_pk=allocation_pk)

    allocation = Allocation.objects.get(id=allocation_pk)
    # reset state to pending
    update_allocation_attribute_value(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'pending')
    try:
        create_project_share_file_structure(native_path=native_path, 
                                            owner=owner, group=group, 
                                            client_config_id=client_config_id)
    except GroupNotFoundError as e:
        if retries <= 0:
            logger.error(f"Group {group} not found in AD and no retries left")
            update_allocation_attribute_value(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'failed')
            raise e
        else:
            update_allocation_attribute_value(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'waiting...')
            schedule('storage.vast.create_project_share', kwargs={
                    'native_path': native_path, 'quota_bytes': quota_bytes, 'owner': owner, 'group': group, 
                    'client_config_id': client_config_id, 'allocation_pk': allocation_pk, 'retries': retries-1, 'wait': wait },
                    schedule_type=Schedule.ONCE,
                    next_run=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=wait)
            )
            return

    except Exception as e:
        logger.error(f"Error creating project share for path {native_path} in VAST: {e}")
        update_allocation_attribute_value(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'failed')
        raise e 


def create_project_share_file_structure(native_path: str, owner: str, group: str, client_config_id: str):
    ad_search = ADSearch('', '')

    owner_results = ad_search.get_ad_user(owner)
    if not owner_results:
        logger.error(f"Could not find owner {owner} in AD")
        raise UserNotFoundError(f"Could not find owner {owner} in AD")
    uid = owner_results.get('uidNumber', None)
    if uid is None:
        logger.error(f"Could not find UID for owner {owner} in AD")
        raise UIDNotFoundError(f"Could not find UID for owner {owner} in AD")
    
    group_results = ad_search.get_ad_group(group)
    if not group_results or group_results.get('gidNumber', []) == []:
        raise GroupNotFoundError(f"Could not find group with GID '{group}' in AD")

    cluster_path = Path(native_path_to_cluster_path(native_path, client_config_id=client_config_id))

    create_project_folders(cluster_path, owner=owner, 
                            group=group, members=None)
