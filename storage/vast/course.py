import logging
from pathlib import Path

from coldfront_utils.tasks import RetryLater, reschedulable

from storage.directory_structure.tasks import create_course_folders
from storage.directory_structure.utils import ClusterDirectoryNotVisibleError
from storage.utils import ActiveDirectoryNotReadyError, lookup_ad_entries, records_share_creation_state
from storage.vast.errors import VastViewNotFoundError

from .vast import check_folder, native_path_to_cluster_path, provision_share

logger = logging.getLogger(__name__)


@records_share_creation_state
@reschedulable(retry_on=(ActiveDirectoryNotReadyError, VastViewNotFoundError), retries=5, wait=5)
def create_course_share(
    native_path: str,
    quota_bytes: int,
    owner: str,
    group: str,
    client_config_id: str,
    allocation_pk: int,
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
        share_provisioned (bool, optional): Whether an earlier run already finished on VAST, so this one
            only creates the folders. Set by the retry that waits for the cluster directory. Defaults to False.

    Runs again every 5 minutes, up to 5 times, while AD, the VAST view or the cluster directory isn't
    ready (@reschedulable); a call can pass retries= and wait= to change that.
    """

    # before any VAST calls, so waiting for AD doesn't repeat them on every retry. raises ActiveDirectoryNotReadyError if AD isn't ready
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
    try:
        create_course_folders(cluster_path=cluster_path, owner=owner, group=group, members=[])
    except ClusterDirectoryNotVisibleError as e:
        # the last step: VAST is done by now, so the retry only has to create the folders
        raise RetryLater(str(e), share_provisioned=True) from e
