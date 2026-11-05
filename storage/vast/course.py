import logging
from pathlib import Path

from storage.directory_structure.tasks import create_course_folders
from storage.directory_structure.utils import ClusterDirectoryNotVisibleError
from storage.utils import (
    ActiveDirectoryNotReadyError,
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
    share_provisioned=False,
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
        retries (int, optional): The number of retries while AD, the VAST view or the cluster directory
            isn't ready. Defaults to 5.
        wait (int, optional): The wait time between retries in minutes. Defaults to 5.
        share_provisioned (bool, optional): Whether an earlier run already finished on VAST, so this one
            only creates the folders. Set by the retry that waits for the cluster directory. Defaults to False.
    """

    try:
        # before any VAST calls, so waiting for AD doesn't repeat them on every retry
        lookup_ad_entries(owner, group)
        # skipped once VAST is done, so waiting for the cluster directory doesn't repeat them either
        if not share_provisioned:
            provision_share(
                native_path=native_path.lower(),
                quota_bytes=quota_bytes,
                client_config_id=client_config_id,
            )
            check_folder(native_path, client_config_id=client_config_id)
        cluster_path = Path(native_path_to_cluster_path(native_path, client_config_id=client_config_id))
        create_course_folders(cluster_path=cluster_path, owner=owner, group=group, members=[])

    except (ActiveDirectoryNotReadyError, VastViewNotFoundError, ClusterDirectoryNotVisibleError) as e:
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
            # the cluster directory is the last step: VAST is done by the time it's waited for
            share_provisioned=share_provisioned or isinstance(e, ClusterDirectoryNotVisibleError),
        )
        raise TaskRetryScheduled(str(e)) from e
