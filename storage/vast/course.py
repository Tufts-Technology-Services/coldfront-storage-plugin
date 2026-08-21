import logging
import datetime
from pathlib import Path

from django_q.tasks import Schedule, schedule

from coldfront.core.allocation.models import Allocation
from coldfront_utils import update_allocation_attribute_value

from storage.utils import GroupNotFoundError, UIDNotFoundError, UserNotFoundError
from coldfront_utils.util.ad_search import ADSearch
from .vast import create_share, native_path_to_cluster_path
from storage.constants import SHARE_CREATION_STATE_ATTRIBUTE_NAME
from storage.directory_structure.tasks import create_course_folders

logger = logging.getLogger(__name__)

def create_course_share(native_path: str, quota_bytes: int, owner: str, group: str, 
                        client_config_id: str, allocation_pk: int, retries=5, wait=5):
    """
    Create a course share in the VAST storage system.

    Args:
        native_path (str): The native path of the course share.
        quota_bytes (int): The quota for the course share in bytes.
        owner (str): The owner of the course share.
        group (str): The group of the course share.
        client_config_id (str): The client configuration ID.
        allocation_pk (int): The primary key of the allocation.
        retries (int, optional): The number of retries if the group is not found in AD. Defaults to 5.
        wait (int, optional): The wait time between retries in minutes. Defaults to 5.
    """
    create_share(native_path=native_path.lower(), quota_bytes=quota_bytes, 
                 owner=owner, group=group, client_config_id=client_config_id, 
                 allocation_pk=allocation_pk)


    allocation = Allocation.objects.get(id=allocation_pk)
    update_allocation_attribute_value(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'pending')

    try:
        ad_search = ADSearch('', '')
        owner_results = ad_search.get_ad_user(owner)
        if not owner_results:
            raise UserNotFoundError(f"Could not find owner {owner} in AD")
        uid = owner_results.get('uidNumber', None)
        if not uid:
            raise UIDNotFoundError(f"Could not find UID for owner {owner} in AD")
        group_results = ad_search.get_ad_group(group)
        if not group_results:
            raise GroupNotFoundError(f"Could not find group {group} in AD")
        cluster_path = Path(native_path_to_cluster_path(native_path, client_config_id=client_config_id))
        create_course_folders(cluster_path=cluster_path,
                              owner=owner,
                              group=group,
                              members=[])

    except GroupNotFoundError as e:
        if retries <= 0:
            logger.error(f"Group {group} not found in AD and no retries left")
            update_allocation_attribute_value(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'failed')
            raise e
        else:
            update_allocation_attribute_value(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'waiting...')
            schedule(create_course_share, kwargs={
                    'native_path': native_path, 'quota_bytes': quota_bytes, 'owner': owner, 'group': group, 
                    'client_config_id': client_config_id, 'allocation_pk': allocation_pk, 'retries': retries-1, 'wait': wait 
                    },
                    schedule_type=Schedule.ONCE,
                    next_run=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=wait)
            )
    except Exception as e:
        logger.error(f"Error creating course share for path {cluster_path}: {e}")
        update_allocation_attribute_value(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'failed')
        raise e 
