import logging
from pathlib import Path

from storage.directory_structure.tasks import create_project_folders
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
def create_project_share(
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
    try:
        provision_share(
            native_path=native_path.lower(),
            quota_bytes=quota_bytes,
            client_config_id=client_config_id,
        )
        check_folder(native_path, client_config_id=client_config_id)
        create_project_share_file_structure(
            native_path=native_path, owner=owner, group=group, client_config_id=client_config_id
        )
    except (ActiveDirectoryNotFoundError, VastViewNotFoundError) as e:
        schedule_retry(
            create_project_share,
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


def create_project_share_file_structure(native_path: str, owner: str, group: str, client_config_id: str):
    lookup_ad_entries(owner, group)

    cluster_path = Path(native_path_to_cluster_path(native_path, client_config_id=client_config_id))

    create_project_folders(cluster_path, owner=owner, group=group, members=[])
