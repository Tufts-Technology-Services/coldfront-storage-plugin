"""A member activated on an allocation gets their own folder, and nothing else is redeployed.

ColdFront signals allocation_activate_user once per member. Each signal used to redeploy the whole
tree for every member, so adding several members at once ran that many overlapping full deploys,
and a member whose UID wasn't ready yet failed inside a deploy meant for everyone. Now each member's
task makes their one folder, and waits for their own account.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.utils.module_loading import import_string

from coldfront.core.allocation.models import AllocationUserStatusChoice
from coldfront.core.allocation.signals import allocation_activate_user

from storage.constants import CLUSTER_PATH_ATTRIBUTE_NAME, GROUP_ATTRIBUTE_NAME
from storage.directory_structure import tasks as directory_tasks
from storage.directory_structure.course import deploy_course_member_directory
from storage.directory_structure.hpc_projects import deploy_project_member_directory
from storage.directory_structure.tasks import member_folder_task_name
from storage.directory_structure.utils import ClusterDirectoryNotVisibleError
from storage.tests.helpers import RESCHEDULE

LAB_DIR = "/cluster/tufts/desjardinslab"


def activate(allocation, username, status="Active"):
    """Put a user on the allocation with the given status, as ColdFront's add_user() does."""
    user = User.objects.get_or_create(username=username)[0]
    return allocation.allocationuser_set.create(
        user=user, status=AllocationUserStatusChoice.objects.get_or_create(name=status)[0]
    )


@pytest.fixture
def lab_allocation(storage_resource, make_allocation):
    return make_allocation(
        storage_resource,
        attributes={
            CLUSTER_PATH_ATTRIBUTE_NAME: LAB_DIR,
            GROUP_ATTRIBUTE_NAME: "desjardinslab",
            "directory_creation": "hpc_project",
        },
    )


# --- the signal ---------------------------------------------------------------------------------


@patch("storage.tasks.async_task")
def test_activating_a_member_queues_just_their_folder(mock_async, lab_allocation):
    member = activate(lab_allocation, "student01")

    allocation_activate_user.send(sender=None, allocation_user_pk=member.pk)

    mock_async.assert_called_once()
    assert mock_async.call_args.args == (
        directory_tasks.create_member_folder,
        lab_allocation.pk,
        "hpc_project",
        "student01",
    )
    assert mock_async.call_args.kwargs["q_options"] == {
        "task_name": "create_folders_hpc_project_desjardinslab_student01"
    }


@patch("storage.tasks.async_task")
def test_an_allocation_without_directory_creation_queues_nothing(mock_async, storage_resource, make_allocation):
    allocation = make_allocation(storage_resource, attributes={CLUSTER_PATH_ATTRIBUTE_NAME: LAB_DIR})
    member = activate(allocation, "student01")

    allocation_activate_user.send(sender=None, allocation_user_pk=member.pk)

    mock_async.assert_not_called()


def test_the_name_starts_like_the_full_trees(lab_allocation):
    """So filtering the task report on the directory finds both."""
    assert member_folder_task_name(lab_allocation.pk, "hpc_course", "student01") == (
        "create_folders_hpc_course_desjardinslab_student01"
    )


# --- the task -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "structure_type, folder_step",
    [("hpc_project", "create_project_member_folder"), ("hpc_course", "create_course_member_folder")],
)
def test_the_member_gets_their_folder(structure_type, folder_step, lab_allocation):
    activate(lab_allocation, "student01")

    with patch(f"storage.directory_structure.tasks.{folder_step}") as mock_folder:
        result = directory_tasks.create_member_folder(lab_allocation.pk, structure_type, "student01")

    mock_folder.assert_called_once_with(Path(LAB_DIR), "student01", "desjardinslab")
    assert result == {"message": f"successful:created student01's folder in {LAB_DIR}"}


@patch(RESCHEDULE)
@patch("storage.directory_structure.tasks.create_project_member_folder")
def test_a_member_without_a_uid_yet_waits_on_their_own(mock_folder, mock_schedule, ad_search, lab_allocation):
    activate(lab_allocation, "student01")
    ad_search.get_ad_user.return_value = {"uidNumber": []}

    result = directory_tasks.create_member_folder(lab_allocation.pk, "hpc_project", "student01")

    mock_folder.assert_not_called()
    assert result["message"] == (
        "scheduled:User student01 is in AD but has no UID yet (runs again in 3 minutes, 4 retries left)"
    )
    assert import_string(mock_schedule.call_args.args[0]) is directory_tasks.create_member_folder
    assert mock_schedule.call_args.kwargs["username"] == "student01"
    assert mock_schedule.call_args.kwargs["q_options"] == {
        "task_name": "create_folders_hpc_project_desjardinslab_student01"
    }


@patch(RESCHEDULE)
@patch(
    "storage.directory_structure.tasks.create_project_member_folder",
    side_effect=ClusterDirectoryNotVisibleError(f"Directory {LAB_DIR} is not visible on admin-p01 yet."),
)
def test_a_member_waits_for_the_allocations_directory(mock_folder, mock_schedule, lab_allocation):
    """At approval the members' tasks can run before the share task has made the directory."""
    activate(lab_allocation, "student01")

    result = directory_tasks.create_member_folder(lab_allocation.pk, "hpc_project", "student01")

    assert result["message"].startswith(f"scheduled:Directory {LAB_DIR} is not visible")
    mock_schedule.assert_called_once()


@patch("storage.directory_structure.tasks.create_project_member_folder")
def test_a_member_no_longer_active_is_skipped(mock_folder, ad_search, lab_allocation):
    activate(lab_allocation, "student01", status="Removed")

    result = directory_tasks.create_member_folder(lab_allocation.pk, "hpc_project", "student01")

    mock_folder.assert_not_called()
    ad_search.get_ad_user.assert_not_called()
    assert result == {"message": f"successful:skipped: student01 is no longer active on allocation {lab_allocation.pk}"}


@patch("storage.directory_structure.tasks.create_project_member_folder")
def test_a_username_that_cant_be_a_directory_fails_at_once(mock_folder, ad_search, lab_allocation):
    with pytest.raises(ValueError, match="Invalid name for user"):
        directory_tasks.create_member_folder(lab_allocation.pk, "hpc_project", "../etc")

    mock_folder.assert_not_called()
    ad_search.get_ad_user.assert_not_called()


# --- the deploys ----------------------------------------------------------------------------------


@pytest.fixture
def directory_visible():
    """The allocation's directory is there on the cluster host."""
    with patch("storage.directory_structure.utils.host") as stand_in:
        stand_in.name = "admin-p01.example.edu"
        stand_in.get_fact.return_value = {"user": "root", "group": "root", "mode": 755}
        yield stand_in


def test_a_project_member_gets_the_folder_the_full_deploy_makes(directory_visible):
    with patch("storage.directory_structure.hpc_projects.create_subdirectory") as mock_subdirectory:
        # _inner is the undecorated function, so no pyinfra deploy state is needed
        deploy_project_member_directory._inner(
            parent_directory=Path("/cluster/tufts"),
            project_directory_name="desjardinslab",
            username="student01",
            group="desjardinslab",
        )

    mock_subdirectory.assert_called_once_with(
        parent_directory=Path(LAB_DIR), subdirectory_name="student01", owner="student01", group="desjardinslab"
    )


def test_a_course_member_gets_the_folder_the_full_deploy_makes(directory_visible):
    with patch("storage.directory_structure.course.create_subdirectory") as mock_subdirectory:
        deploy_course_member_directory._inner(
            parent_directory=Path("/cluster/tufts"),
            course_directory_name="capstone26",
            username="student01",
            admin_group="capstone26admin",
        )

    mock_subdirectory.assert_called_once_with(
        parent_directory=Path("/cluster/tufts/capstone26"),
        subdirectory_name="student01",
        owner="student01",
        group="capstone26admin",
        mode="2770",
    )


def test_a_member_folder_isnt_made_before_its_directory_is_visible(directory_visible):
    directory_visible.get_fact.return_value = None

    with patch("storage.directory_structure.hpc_projects.create_subdirectory") as mock_subdirectory:
        with pytest.raises(ClusterDirectoryNotVisibleError):
            deploy_project_member_directory._inner(
                parent_directory=Path("/cluster/tufts"),
                project_directory_name="desjardinslab",
                username="student01",
                group="desjardinslab",
            )

    mock_subdirectory.assert_not_called()
