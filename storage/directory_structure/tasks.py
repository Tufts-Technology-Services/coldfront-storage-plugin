import logging
from pathlib import Path

from coldfront.core.allocation.models import Allocation, AllocationAttribute
from coldfront_utils.tasks import reschedulable
from django_q.models import Task

from storage.constants import CLUSTER_PATH_ATTRIBUTE_NAME, ProvisionStatus
from storage.directory_structure import PosixDeploymentRunner
from storage.directory_structure.course import deploy_course_directory
from storage.directory_structure.hpc_projects import deploy_project_directory
from storage.directory_structure.scratch import deploy_personal_scratch_directory
from storage.directory_structure.utils import ClusterDirectoryNotVisibleError
from storage.utils import (
    ActiveDirectoryNotReadyError,
    build_task_result,
    get_allocation_group,
    lookup_ad_entries,
    validate_dirname,
)

logger = logging.getLogger(__name__)


def folder_task_name(allocation_pk: int, structure_type: str) -> str:
    """The name every run of create_folders gets on the task report: create_folders_<type>_<directory>.

    <directory> is the last part of the allocation's cluster path, or allocation_<pk> if it has none.
    All the runs for one allocation share the name, so its retries can be found together; django-q
    doesn't need task names to be unique. Cut to the length django-q stores.
    """
    cluster_path = (
        AllocationAttribute.objects.filter(
            allocation_id=allocation_pk, allocation_attribute_type__name=CLUSTER_PATH_ATTRIBUTE_NAME
        )
        .values_list("value", flat=True)
        .first()
    )
    directory = Path((cluster_path or "").strip()).name or f"allocation_{allocation_pk}"
    return f"create_folders_{structure_type}_{directory}"[: Task._meta.get_field("name").max_length]


def folder_task_options(allocation_pk: int, structure_type: str, **_) -> dict:
    """django-q options for a run of create_folders. Build them for each call: async_task pops
    task_name out of the dict it's given."""
    return {"task_name": folder_task_name(allocation_pk, structure_type)}


@reschedulable(
    retry_on=(ActiveDirectoryNotReadyError, ClusterDirectoryNotVisibleError),
    retries=5,
    wait=3,
    q_options=folder_task_options,
)
def create_folders(allocation_pk: int, structure_type: str):
    """Create an allocation's folders on the cluster, waiting for AD and the cluster directory.

    Not a state-recording task, so a run that has to wait returns @reschedulable's scheduled
    result, which the task report shows: it runs again every 3 minutes, up to 5 times.
    """
    # checked first, so a bad value fails at once rather than after waiting out AD's retries
    if structure_type not in ("hpc_project", "hpc_course"):
        logger.error(f"Unknown structure type {structure_type}. Cannot create folders.")
        raise ValueError(f"Unknown structure type {structure_type}. Cannot create folders.")
    allocation = Allocation.objects.get(id=allocation_pk)
    owner = allocation.project.pi.username
    group = get_allocation_group(allocation)
    if group is None:
        logger.error(f"No group found for allocation {allocation_pk}")
        raise ValueError(f"No group found for allocation {allocation_pk}")
    aa = allocation.allocationattribute_set.filter(allocation_attribute_type__name=CLUSTER_PATH_ATTRIBUTE_NAME)
    if aa.exists():
        cluster_path = Path(aa.first().value)
    else:
        logger.error(f"No cluster path found for allocation {allocation_pk}")
        raise ValueError(f"No cluster path found for allocation {allocation_pk}")
    members = allocation.allocationuser_set.filter(status__name="Active").values_list("user__username", flat=True)
    # the folders are owned by the PI and the group, so both must be in AD first
    lookup_ad_entries(owner, group)
    if structure_type == "hpc_project":
        create_project_folders(cluster_path, owner, group, list(members))
    else:
        create_course_folders(cluster_path, owner, group, list(members))
    return build_task_result(
        ProvisionStatus.SUCCESSFUL, f"created {structure_type} folders for allocation {allocation_pk}"
    )


def create_project_folders(cluster_path: Path, owner: str, group: str, members: list):
    owner = owner.strip().lower()
    group = group.strip()
    validate_dirname(cluster_path.name)
    runner = PosixDeploymentRunner()
    runner.add_deployment(
        deploy_project_directory,
        parent_directory=cluster_path.parent,
        project_directory_name=cluster_path.name,
        project_members=members,
        owner=owner,
        group=group,
        require_existing=True,
    )
    runner.run()


def create_scratch_folders(parent_path: Path, owner: str):
    owner = owner.strip().lower()
    runner = PosixDeploymentRunner()
    runner.add_deployment(deploy_personal_scratch_directory, parent_directory=parent_path, username=owner)
    runner.run()


def create_course_folders(cluster_path: Path, owner: str, group: str, members: list):
    owner = owner.strip().lower()
    group = group.strip()
    admin_group = f"{group}admin"
    validate_dirname(cluster_path.name)
    runner = PosixDeploymentRunner()
    runner.add_deployment(
        deploy_course_directory,
        parent_directory=cluster_path.parent,
        course_directory_name=cluster_path.name,
        course_members=members,
        owner=owner,
        group=group,
        admin_group=admin_group,
        require_existing=True,
    )
    runner.run()
