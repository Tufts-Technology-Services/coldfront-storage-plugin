import logging
from pathlib import Path
from coldfront.core.allocation.models import Allocation
from coldfront_utils import update_allocation_attribute_value
from .vast import create_share, native_path_to_cluster_path
from storage.constants import SHARE_CREATION_STATE_ATTRIBUTE_NAME
from storage.directory_structure.tasks import create_course_folders

logger = logging.getLogger(__name__)

def create_course_share(native_path: str, quota_bytes: int, owner: str, group: str, client_config_id: str, allocation_pk: int):
    create_share(native_path=native_path.lower(), quota_bytes=quota_bytes, 
                 owner=owner, group=group, client_config_id=client_config_id, 
                 allocation_pk=allocation_pk)


    allocation = Allocation.objects.get(id=allocation_pk)
    update_allocation_attribute_value(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'pending')

    try:
        cluster_path = Path(native_path_to_cluster_path(native_path, client_config_id=client_config_id))
        create_course_folders(cluster_path=cluster_path,
                              owner=owner,
                              group=group,
                              members=[])
    except Exception as e:
        logger.error(f"Error creating course share for path {cluster_path}: {e}")
        update_allocation_attribute_value(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'failed')
        raise e 
