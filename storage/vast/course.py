import logging
import shutil
from pathlib import Path
from coldfront.core.allocation.models import Allocation
from coldfront_utils import update_allocation_attribute_value
from .vast import create_share, native_path_to_cluster_path
from storage.constants import SHARE_CREATION_STATE_ATTRIBUTE_NAME
from storage.shell_utils import (check_group_exists, check_user_exists, 
                                 validate_dirname, create_subfolder)

logger = logging.getLogger(__name__)

def create_course_share(native_path: str, quota_bytes: int, owner: str, group: str, client_config_id: str, allocation_pk: int):
    create_share(native_path=native_path.lower(), quota_bytes=quota_bytes, 
                 owner=owner, group=group, client_config_id=client_config_id, 
                 allocation_pk=allocation_pk)
    try:
        # reset state to pending
        allocation = Allocation.objects.get(id=allocation_pk)
        update_allocation_attribute_value(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'pending')
        owner = owner.strip().lower()
        group = group.strip()
        logger.info("checking user and group...")
        check_user_exists(owner)
        check_group_exists(group)
        admin_group = f'{group}admin'
        check_group_exists(admin_group)

        cluster_path = native_path_to_cluster_path(native_path, client_config_id=client_config_id)
        create_course_folder(cluster_path=Path(cluster_path), owner=owner, group=group, admin_group=admin_group)
    except Exception as e:
        logger.error(f"Error creating course share for path {native_path} in VAST: {e}")
        update_allocation_attribute_value(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'failed')
        raise e 


def create_course_folder(cluster_path: Path, owner: str, group: str, admin_group: str):
    validate_dirname(cluster_path.name)
    # owner is the username

    if cluster_path.exists():
        if cluster_path.owner() != owner or cluster_path.group() != group:
            shutil.chown(cluster_path, owner, group)
        if oct(cluster_path.stat().st_mode & 0o7777) != oct(0o2750):
            cluster_path.chmod(0o2750)
        create_subfolder(cluster_path, 'shared', owner, admin_group, mode=0o2775)
        shared_path = cluster_path / 'shared'
        if shared_path.exists():
            if shared_path.owner() != owner or shared_path.group() != admin_group:
                shutil.chown(shared_path, owner, admin_group)
            if oct(shared_path.stat().st_mode & 0o7777) != oct(0o2775):
                shared_path.chmod(0o2775)
        # course owner folder
        create_subfolder(cluster_path, owner, owner, admin_group)
        return True
    return False
