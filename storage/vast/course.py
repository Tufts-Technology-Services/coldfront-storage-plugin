import logging
from pathlib import Path
from coldfront.core.allocation.models import Allocation
from coldfront_utils import update_allocation_attribute_value
from .vast import create_share, native_path_to_cluster_path
from storage.constants import SHARE_CREATION_STATE_ATTRIBUTE_NAME
from storage.directory_structure import PosixDeploymentRunner, deploy_course_directory
from storage.shell_utils import (check_group_exists, check_user_exists, 
                                 validate_dirname)

logger = logging.getLogger(__name__)

def create_course_share(native_path: str, quota_bytes: int, owner: str, group: str, client_config_id: str, allocation_pk: int):
    create_share(native_path=native_path.lower(), quota_bytes=quota_bytes, 
                 owner=owner, group=group, client_config_id=client_config_id, 
                 allocation_pk=allocation_pk)

    create_course_folders(native_path=native_path.lower(),
                          owner=owner,
                          group=group,
                          client_config_id=client_config_id,
                          allocation_pk=allocation_pk)


def create_course_folders(native_path: str, owner: str, 
                          group: str, client_config_id: str, allocation_pk: int):
    allocation = Allocation.objects.get(id=allocation_pk)
    try:
        # reset state to pending
        update_allocation_attribute_value(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'pending')

        members = allocation.allocationuser_set.values_list('user__username', flat=True)
        valid_members = []
        for member in members:
            if check_user_exists(member):
                valid_members.append(member)
            else:
                logger.error(f"User {member} does not exist on the system. Skipping folder creation for this user.")

        cluster_path = Path(native_path_to_cluster_path(native_path, client_config_id=client_config_id))
        owner = owner.strip().lower()
        group = group.strip()
        logger.info("checking user and group...")
        check_user_exists(owner)
        check_group_exists(group)
        admin_group = f'{group}admin'
        check_group_exists(admin_group)
        validate_dirname(cluster_path.name)
        runner = PosixDeploymentRunner()
        runner.add_deployment(deploy_course_directory, 
                            parent_directory=cluster_path.parent, 
                            course_directory=cluster_path.name, 
                            course_members=valid_members,
                            owner=owner, 
                            group=group, 
                            admin_group=admin_group)
        runner.run()
    except Exception as e:
        logger.error(f"Error creating course share for path {native_path} in VAST: {e}")
        update_allocation_attribute_value(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, 'failed')
        raise e 
