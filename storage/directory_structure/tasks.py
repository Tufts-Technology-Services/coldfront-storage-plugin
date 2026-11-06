import logging
from pathlib import Path

from coldfront_utils.tasks import reschedulable
from django_q.models import Task

from coldfront.core.allocation.models import Allocation, AllocationAttribute

from storage.constants import CLUSTER_PATH_ATTRIBUTE_NAME, ProvisionStatus
from storage.directory_structure import PosixDeploymentRunner
from storage.directory_structure.course import deploy_course_directory, deploy_course_member_directory
from storage.directory_structure.hpc_projects import deploy_project_directory, deploy_project_member_directory
from storage.directory_structure.scratch import deploy_personal_scratch_directory
from storage.directory_structure.utils import ClusterDirectoryNotVisibleError
from storage.utils import (
    ActiveDirectoryNotReadyError,
    build_task_result,
    get_allocation_group,
    lookup_ad_entries,
    validate_dirname,
    validate_username,
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


def member_folder_task_name(allocation_pk: int, structure_type: str, username: str) -> str:
    """The name every run of create_member_folder gets: create_folders_<type>_<directory>_<username>.

    It starts like create_folders' name, so filtering the task report on an allocation's directory
    finds both.
    """
    name = f"{folder_task_name(allocation_pk, structure_type)}_{username}"
    return name[: Task._meta.get_field("name").max_length]


def member_folder_task_options(allocation_pk: int, structure_type: str, username: str, **_) -> dict:
    """django-q options for a run of create_member_folder, built for each call like folder_task_options."""
    return {"task_name": member_folder_task_name(allocation_pk, structure_type, username)}


def _folder_target(allocation_pk: int, structure_type: str):
    """The allocation, group and cluster path a folder task deploys to, or ValueError for what's missing.

    The structure type is checked first, so a bad value fails at once rather than after waiting
    out AD's retries.
    """
    if structure_type not in ("hpc_project", "hpc_course"):
        logger.error(f"Unknown structure type {structure_type}. Cannot create folders.")
        raise ValueError(f"Unknown structure type {structure_type}. Cannot create folders.")
    allocation = Allocation.objects.get(id=allocation_pk)
    group = get_allocation_group(allocation)
    if group is None:
        logger.error(f"No group found for allocation {allocation_pk}")
        raise ValueError(f"No group found for allocation {allocation_pk}")
    aa = allocation.allocationattribute_set.filter(allocation_attribute_type__name=CLUSTER_PATH_ATTRIBUTE_NAME)
    if not aa.exists():
        logger.error(f"No cluster path found for allocation {allocation_pk}")
        raise ValueError(f"No cluster path found for allocation {allocation_pk}")
    return allocation, group, Path(aa.first().value)


@reschedulable(
    retry_on=(ActiveDirectoryNotReadyError, ClusterDirectoryNotVisibleError),
    retries=5,
    wait=3,
    q_options=folder_task_options,
)
def create_folders(allocation_pk: int, structure_type: str):
    """Create all of an allocation's folders on the cluster, waiting for AD and the cluster directory.

    The full tree: `shared`, and a folder for the PI and for every active member. A member being
    activated runs create_member_folder instead; this is for putting a whole tree right, such as
    after an outage.

    Not a state-recording task, so a run that has to wait returns @reschedulable's scheduled
    result, which the task report shows: it runs again every 3 minutes, up to 5 times.
    """
    allocation, group, cluster_path = _folder_target(allocation_pk, structure_type)
    owner = allocation.project.pi.username
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


@reschedulable(
    retry_on=(ActiveDirectoryNotReadyError, ClusterDirectoryNotVisibleError),
    retries=5,
    wait=3,
    q_options=member_folder_task_options,
)
def create_member_folder(allocation_pk: int, structure_type: str, username: str):
    """Create one member's folder in an allocation's directory, when they're activated on it.

    Waits like create_folders, every 3 minutes up to 5 times, for the member and the group to reach
    AD and for the allocation's directory to be visible on the cluster: so a new member whose UID
    isn't ready yet waits on their own, rather than failing inside a deploy for everyone. A member
    no longer active on the allocation by the time it runs is skipped.
    """
    allocation, group, cluster_path = _folder_target(allocation_pk, structure_type)
    username = username.strip().lower()
    # it becomes a directory name, so check it first: a bad one fails at once, without waiting
    validate_username(username)
    if not allocation.allocationuser_set.filter(user__username=username, status__name="Active").exists():
        return build_task_result(
            ProvisionStatus.SUCCESSFUL, f"skipped: {username} is no longer active on allocation {allocation_pk}"
        )
    # the folder is owned by the member and the group, so both must be in AD first
    lookup_ad_entries(username, group)
    if structure_type == "hpc_project":
        create_project_member_folder(cluster_path, username, group)
    else:
        create_course_member_folder(cluster_path, username, group)
    return build_task_result(ProvisionStatus.SUCCESSFUL, f"created {username}'s folder in {cluster_path}")


def create_project_member_folder(cluster_path: Path, username: str, group: str):
    validate_dirname(cluster_path.name)
    runner = PosixDeploymentRunner()
    runner.add_deployment(
        deploy_project_member_directory,
        parent_directory=cluster_path.parent,
        project_directory_name=cluster_path.name,
        username=username,
        group=group.strip(),
        require_existing=True,
    )
    runner.run()


def create_course_member_folder(cluster_path: Path, username: str, group: str):
    validate_dirname(cluster_path.name)
    runner = PosixDeploymentRunner()
    runner.add_deployment(
        deploy_course_member_directory,
        parent_directory=cluster_path.parent,
        course_directory_name=cluster_path.name,
        username=username,
        admin_group=f"{group.strip()}admin",
        require_existing=True,
    )
    runner.run()


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
