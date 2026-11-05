"""require_existing_directory: a storage system creates a share's top directory, and pyinfra must not.

On the cluster host the project directories sit on an autofs mount, where mkdir is always refused.
So before any operation is added for such a directory, the deploy checks the host can see it.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from django.utils.module_loading import import_string
from pyinfra.api.exceptions import PyinfraError
from pyinfra.facts.files import Directory

from storage.constants import CLUSTER_PATH_ATTRIBUTE_NAME, ProvisionStatus
from storage.directory_structure import tasks as directory_tasks
from storage.directory_structure.course import deploy_course_directory
from storage.directory_structure.hpc_projects import deploy_project_directory
from storage.directory_structure.utils import ClusterDirectoryNotVisibleError, require_existing_directory
from storage.tests.helpers import message_of, state_of
from storage.vast import course, project

PROJECT_DIR = Path("/cluster/tufts/desjardinslab")


@pytest.fixture
def fake_host():
    """Stand in for pyinfra's current host, so no test needs a connected deploy state."""
    with patch("storage.directory_structure.utils.host") as stand_in:
        stand_in.name = "admin-p01.example.edu"
        yield stand_in


def test_an_existing_directory_passes_on_one_read_of_its_fact(fake_host):
    fake_host.get_fact.return_value = {"user": "root", "group": "root", "mode": 755}

    require_existing_directory(PROJECT_DIR)

    fake_host.get_fact.assert_called_once_with(Directory, path="/cluster/tufts/desjardinslab")


def test_a_directory_not_there_yet_is_not_visible(fake_host):
    """What autofs gives while the view is still being created, or while it caches a failed mount."""
    fake_host.get_fact.return_value = None

    with pytest.raises(ClusterDirectoryNotVisibleError) as raised:
        require_existing_directory(PROJECT_DIR)

    assert "/cluster/tufts/desjardinslab" in str(raised.value)
    assert "admin-p01.example.edu" in str(raised.value)


def test_something_other_than_a_directory_is_not_waited_for(fake_host):
    """Waiting won't turn a file into a directory, so this must not be the retried error."""
    fake_host.get_fact.return_value = False

    with pytest.raises(NotADirectoryError) as raised:
        require_existing_directory(PROJECT_DIR)

    assert not isinstance(raised.value, ClusterDirectoryNotVisibleError)
    assert "/cluster/tufts/desjardinslab" in str(raised.value)


def test_the_not_visible_error_is_not_swallowed_as_a_pyinfra_error():
    """create_subdirectory and the deployment runner both catch PyinfraError; this has to reach the task."""
    assert not issubclass(ClusterDirectoryNotVisibleError, PyinfraError)


# --- the tasks wait for the directory --------------------------------------------------------

NOT_VISIBLE = ClusterDirectoryNotVisibleError("Directory /cluster/tufts/p is not visible on admin-p01 yet.")

# each share task, with the call that reaches the folder deploy and so can raise NOT_VISIBLE
SHARE_TASKS = [
    pytest.param(project.create_project_share, "create_project_share_file_structure", id="project"),
    pytest.param(course.create_course_share, "create_course_folders", id="course"),
]


@pytest.fixture
def share_allocation(storage_resource, make_allocation):
    return make_allocation(storage_resource)


def run_share_task_without_the_directory(task, folder_step, allocation, retries):
    """Run a share task with VAST stubbed out and the cluster directory not visible yet."""
    module = task.__module__
    with (
        patch(f"{module}.provision_share"),
        patch(f"{module}.check_folder"),
        patch(f"{module}.native_path_to_cluster_path", return_value="/cluster/tufts/p", create=True),
        patch(f"{module}.{folder_step}", side_effect=NOT_VISIBLE),
    ):
        return task("/projects/p", 10, "o", "g", "hpc", allocation.pk, retries=retries)


@pytest.mark.parametrize("task, folder_step", SHARE_TASKS)
@patch("storage.utils.schedule")
def test_a_share_task_waits_for_the_directory(mock_schedule, task, folder_step, share_allocation):
    result = run_share_task_without_the_directory(task, folder_step, share_allocation, retries=2)

    assert import_string(mock_schedule.call_args.args[0]) is task
    assert mock_schedule.call_args.kwargs["retries"] == 1
    assert state_of(share_allocation) == ProvisionStatus.SCHEDULED
    assert message_of(share_allocation) == str(NOT_VISIBLE)
    assert result == {"message": f"scheduled:{NOT_VISIBLE}"}


@pytest.mark.parametrize("task, folder_step", SHARE_TASKS)
@patch("storage.utils.schedule")
def test_a_share_task_fails_once_its_retries_run_out(mock_schedule, task, folder_step, share_allocation):
    with pytest.raises(ClusterDirectoryNotVisibleError):
        run_share_task_without_the_directory(task, folder_step, share_allocation, retries=0)

    mock_schedule.assert_not_called()
    assert state_of(share_allocation) == ProvisionStatus.FAILED
    assert message_of(share_allocation) == str(NOT_VISIBLE)


@pytest.mark.parametrize(
    "structure_type, folder_step", [("hpc_project", "create_project_folders"), ("hpc_course", "create_course_folders")]
)
@patch("storage.utils.schedule")
@patch("storage.directory_structure.tasks.get_allocation_group", return_value="g")
def test_folder_creation_waits_for_the_directory(
    mock_group, mock_schedule, structure_type, folder_step, storage_resource, make_allocation
):
    allocation = make_allocation(storage_resource, attributes={CLUSTER_PATH_ATTRIBUTE_NAME: "/cluster/tufts/p"})

    with patch(f"storage.directory_structure.tasks.{folder_step}", side_effect=NOT_VISIBLE):
        result = directory_tasks.create_folders(allocation.pk, structure_type, retries=2, wait=3)

    assert import_string(mock_schedule.call_args.args[0]) is directory_tasks.create_folders
    assert mock_schedule.call_args.kwargs["retries"] == 1
    assert mock_schedule.call_args.kwargs["structure_type"] == structure_type
    assert result == {"message": f"scheduled:{NOT_VISIBLE} (runs again in 3 minutes, 1 retries left)"}


@patch("storage.utils.schedule")
@patch("storage.directory_structure.tasks.create_project_folders", side_effect=NOT_VISIBLE)
@patch("storage.directory_structure.tasks.get_allocation_group", return_value="g")
def test_folder_creation_fails_once_its_retries_run_out(
    mock_group, mock_folders, mock_schedule, storage_resource, make_allocation
):
    allocation = make_allocation(storage_resource, attributes={CLUSTER_PATH_ATTRIBUTE_NAME: "/cluster/tufts/p"})

    with pytest.raises(ClusterDirectoryNotVisibleError):
        directory_tasks.create_folders(allocation.pk, "hpc_project", retries=0)

    mock_schedule.assert_not_called()


# --- the deploys check their arguments before using them -----------------------------------------

DEPLOYS_WITHOUT_A_DIRECTORY_NAME = [
    pytest.param(deploy_project_directory, {"project_directory_name": None}, id="project"),
    pytest.param(deploy_course_directory, {"course_directory_name": None}, id="course"),
]


@pytest.mark.parametrize("deploy_function, missing", DEPLOYS_WITHOUT_A_DIRECTORY_NAME)
def test_a_deploy_without_its_directory_name_says_so(fake_host, deploy_function, missing):
    """Not a TypeError from joining None onto the parent path."""
    # _inner is the undecorated function, so no pyinfra deploy state is needed
    with pytest.raises(PyinfraError, match="subdirectory name must be provided"):
        deploy_function._inner(parent_directory=PROJECT_DIR.parent, owner="o", group="g", **missing)

    fake_host.get_fact.assert_not_called()
