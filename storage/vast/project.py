import logging
import shutil
from pathlib import Path

from coldfront.core.allocation.models import Allocation
from coldfront_utils import update_allocation_attribute_value

from storage.constants import SHARE_CREATION_STATE_ATTRIBUTE_NAME
from .vast import create_share, native_path_to_cluster_path
from storage.shell_utils import (check_group_exists, check_user_exists, 
                                 validate_dirname, validate_groupname, 
                                 validate_username, create_subfolder)

logger = logging.getLogger(__name__)

def create_project_share(native_path: str, quota_bytes: int, owner: str, group: str, 
                         client_config_id: str, allocation_pk: int):
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
    try:
        # reset state to pending
        allocation = Allocation.objects.get(id=allocation_pk)
        update_allocation_attribute_value(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'pending')
        owner = owner.strip().lower()
        group = group.strip()
        logger.info("checking user and group...")
        check_user_exists(owner)
        check_group_exists(group)

        cluster_path = native_path_to_cluster_path(native_path, client_config_id=client_config_id)
        create_project_owner_folder(cluster_path=Path(cluster_path), owner=owner, group=group)
        update_allocation_attribute_value(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'success')
    except Exception as e:
        logger.error(f"Error creating project share for path {native_path} in VAST: {e}")
        update_allocation_attribute_value(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'failed')
        raise e 


def create_project_owner_folder(cluster_path: Path, owner: str, group: str):
    validate_dirname(cluster_path.name)
    validate_dirname(owner)
    validate_username(owner)
    validate_groupname(group)
    # owner is the username

    if cluster_path.exists():
        if cluster_path.owner() != owner or cluster_path.group() != group:
            shutil.chown(cluster_path, owner, group)
        if oct(cluster_path.stat().st_mode & 0o7777) != oct(0o2770):
            cluster_path.chmod(0o2770)
        create_projects_subfolder(cluster_path, owner)
        create_subfolder(cluster_path, 'shared', owner, group)
    else:
        raise ValueError(f"Cluster path {cluster_path} does not exist. Cannot create project owner folder.")


def create_projects_subfolder(cluster_path: Path, labuser: str):
    validate_dirname(cluster_path.name)
    validate_dirname(labuser)
    validate_username(labuser)

    if not check_user_exists(labuser):
        raise ValueError(f'User {labuser} is not on the system')
    # path is <pathbase>/<project>/<labuser>
    # labuser is the username
    group = cluster_path.group()
    create_subfolder(cluster_path, labuser, labuser, group)
