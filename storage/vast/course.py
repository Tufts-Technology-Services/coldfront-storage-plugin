import logging
from pathlib import Path

from storage.directory_structure.tasks import create_course_folders
from storage.utils import (
    ActiveDirectoryNotFoundError,
    TaskRetryScheduled,
    lookup_ad_entries,
    records_share_creation_state,
    schedule_retry,
)
from storage.vast.errors import VastViewNotFoundError

from .vast import check_folder, native_path_to_cluster_path, provision_share

logger = logging.getLogger(__name__)


@records_share_creation_state
def create_course_share(
    native_path: str,
    quota_bytes: int,
    owner: str,
    group: str,
    client_config_id: str,
    allocation_pk: int,
    retries=5,
    wait=5,
):
    """
    Create a course share in the VAST storage system.

    Args:
        native_path (str): The native path of the course share.
        quota_bytes (int): The quota for the course share in bytes.
        owner (str): The owner of the course share.
        group (str): The group of the course share.
        client_config_id (str): The client configuration ID.
        allocation_pk (int): The primary key of the allocation.
        retries (int, optional): The number of retries while AD or the VAST view isn't ready. Defaults to 5.
        wait (int, optional): The wait time between retries in minutes. Defaults to 5.
    """

    try:
        provision_share(
            native_path=native_path.lower(),
            quota_bytes=quota_bytes,
            client_config_id=client_config_id,
        )

        lookup_ad_entries(owner, group)
        check_folder(native_path, client_config_id=client_config_id)
        cluster_path = Path(native_path_to_cluster_path(native_path, client_config_id=client_config_id))
        create_course_folders(cluster_path=cluster_path, owner=owner, group=group, members=[])

    except (ActiveDirectoryNotFoundError, VastViewNotFoundError) as e:
        schedule_retry(
            create_course_share,
            e,
            retries=retries,
            wait=wait,
            native_path=native_path,
            quota_bytes=quota_bytes,
            owner=owner,
            group=group,
            client_config_id=client_config_id,
            allocation_pk=allocation_pk,
        )
        raise TaskRetryScheduled(str(e)) from e

