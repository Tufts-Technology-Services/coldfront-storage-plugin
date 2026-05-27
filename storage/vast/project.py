import logging
from .vast import create_share

logger = logging.getLogger(__name__)

def create_project_share(native_path: str, quota_bytes: int, owner: str, group: str, client_config: dict):
    create_share(native_path=native_path, quota_bytes=quota_bytes, owner=owner, group=group, client_config=client_config)

    owner = owner.strip().lower()
    group = group.strip()
    logger.info("checking user and group...")
    
    try:
        check_user(owner)
    except ValueError as e:
        logger.error(f"User {owner} is not valid. Please fix account issues and try again: {e}")
        raise e

    try:
        check_group(group)
    except ValueError as e:
        logger.error(f"Group {group} is not valid. Please fix group issues and try again: {e}")
        raise e
    
    