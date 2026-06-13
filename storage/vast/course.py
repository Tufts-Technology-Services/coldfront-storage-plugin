import logging
import shutil
from pathlib import Path
from .vast import create_share
from storage.shell_utils import (check_group_exists, check_user_exists, 
                                 validate_dirname, validate_groupname, 
                                 validate_username, create_subfolder)

logger = logging.getLogger(__name__)

def create_course_share(native_path: str, quota_bytes: int, owner: str, group: str, client_config_id: str, allocation_pk: int):
    create_share(native_path=native_path.lower(), quota_bytes=quota_bytes, 
                 owner=owner, group=group, client_config_id=client_config_id, 
                 allocation_pk=allocation_pk)

    owner = owner.strip().lower()
    group = group.strip()
    logger.info("checking user and group...")
    
    try:
        check_user_exists(owner)
    except ValueError as e:
        logger.error(f"User {owner} is not on the system. Please fix account issues and try again: {e}")
        raise e
    admin_group = f'{group}admin'
    try:
        check_group_exists(group)
        check_group_exists(admin_group)
    except ValueError as e:
        logger.error(f"Group {group} or {admin_group} is not on the system. Please fix group issues and try again: {e}")
        raise e

    return create_course_folder(course_name=native_path, owner=owner, group=group, admin_group=admin_group)


def create_course_folder(course_name, owner, group, admin_group):
    validate_dirname(course_name)
    # owner is the username
    project_path = Path(f'/cluster/tufts/{course_name}')
    if project_path.exists():
        if project_path.owner() != owner or project_path.group() != group:
            shutil.chown(project_path, owner, group)
        if oct(project_path.stat().st_mode & 0o7777) != oct(0o2750):
            project_path.chmod(0o2750)
        create_subfolder(project_path, 'shared', owner, admin_group, mode=0o2775)
        shared_path = project_path / 'shared'
        if shared_path.exists():
            if shared_path.owner() != owner or shared_path.group() != admin_group:
                shutil.chown(shared_path, owner, admin_group)
            if oct(shared_path.stat().st_mode & 0o7777) != oct(0o2775):
                shared_path.chmod(0o2775)
        # course owner folder
        create_subfolder(project_path, owner, owner, admin_group)
        return True
    return False