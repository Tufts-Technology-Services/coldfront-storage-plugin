"""Every run of create_folders for an allocation has the same name, so its retries can be found together.

django-q names a task at random unless it's given one, and a retry is a new task. So without a name
an allocation's retries can't be told apart from any other task on the report, and only a failure
stands out.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from django_q.conf import Conf
from django_q.models import Schedule, Task
from django_q.scheduler import scheduler

from storage import tasks
from storage.constants import CLUSTER_PATH_ATTRIBUTE_NAME
from storage.directory_structure import tasks as directory_tasks
from storage.directory_structure.tasks import folder_task_name
from storage.directory_structure.utils import ClusterDirectoryNotVisibleError

NOT_VISIBLE = ClusterDirectoryNotVisibleError("Directory /cluster/tufts/desjardinslab is not visible on admin-p01 yet.")


@pytest.fixture
def lab_allocation(storage_resource, make_allocation):
    return make_allocation(storage_resource, attributes={CLUSTER_PATH_ATTRIBUTE_NAME: "/cluster/tufts/desjardinslab"})


def test_the_name_is_the_structure_type_and_the_directory(lab_allocation):
    assert folder_task_name(lab_allocation.pk, "hpc_project") == "create_folders_hpc_project_desjardinslab"
    assert folder_task_name(lab_allocation.pk, "hpc_course") == "create_folders_hpc_course_desjardinslab"


def test_stray_spaces_and_a_trailing_slash_dont_change_the_name(storage_resource, make_allocation):
    allocation = make_allocation(
        storage_resource, attributes={CLUSTER_PATH_ATTRIBUTE_NAME: " /cluster/tufts/lab01/ \n"}
    )

    assert folder_task_name(allocation.pk, "hpc_project") == "create_folders_hpc_project_lab01"


def test_an_allocation_without_a_cluster_path_is_named_by_its_pk(storage_resource, make_allocation):
    """create_folders fails on such an allocation, but the run that fails still needs a name."""
    allocation = make_allocation(storage_resource)

    assert folder_task_name(allocation.pk, "hpc_project") == f"create_folders_hpc_project_allocation_{allocation.pk}"


def test_the_name_fits_in_what_django_q_stores(storage_resource, make_allocation):
    allocation = make_allocation(
        storage_resource, attributes={CLUSTER_PATH_ATTRIBUTE_NAME: "/cluster/tufts/" + "x" * 200}
    )

    assert len(folder_task_name(allocation.pk, "hpc_course")) == Task._meta.get_field("name").max_length


@patch("storage.tasks.async_task")
def test_the_first_run_is_named(mock_async, lab_allocation):
    tasks.create_directory(lab_allocation.pk, "hpc_project")

    assert mock_async.call_args.args == (directory_tasks.create_folders, lab_allocation.pk, "hpc_project")
    assert mock_async.call_args.kwargs["q_options"] == {"task_name": "create_folders_hpc_project_desjardinslab"}


@patch("coldfront_utils.tasks.reschedulable.schedule")
@patch("storage.directory_structure.tasks.create_project_folders", side_effect=NOT_VISIBLE)
@patch("storage.directory_structure.tasks.get_allocation_group", return_value="g")
def test_each_retry_is_named_the_same(mock_group, mock_folders, mock_schedule, lab_allocation):
    directory_tasks.create_folders(lab_allocation.pk, "hpc_project")

    assert mock_schedule.call_args.kwargs["q_options"] == {"task_name": "create_folders_hpc_project_desjardinslab"}


@patch("storage.directory_structure.tasks.create_project_folders", side_effect=[NOT_VISIBLE, None])
@patch("storage.directory_structure.tasks.get_allocation_group", return_value="g")
def test_the_retry_django_q_runs_carries_the_name(mock_group, mock_folders, lab_allocation):
    """Through django-q's own scheduler and worker, which sync mode runs in this process."""
    directory_tasks.create_folders(lab_allocation.pk, "hpc_project")
    Schedule.objects.update(next_run=timezone.now() - timedelta(minutes=1))

    with patch.object(Conf, "SYNC", True):
        scheduler()

    retry = Task.objects.get(func="storage.directory_structure.tasks.create_folders")
    assert retry.name == "create_folders_hpc_project_desjardinslab"
    assert retry.success is True
    assert mock_folders.call_count == 2
