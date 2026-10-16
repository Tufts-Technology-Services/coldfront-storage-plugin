"""The share creation state attribute must be owned by one task, end to end.

Each of create_share, create_smb_share, create_project_share and create_course_share is a task
entry point dispatched by storage.tasks.create_share, so each is responsible for the whole
lifecycle of SHARE_CREATION_STATE_ATTRIBUTE_NAME for its own run.
"""

from unittest.mock import patch

import pytest

from storage.constants import (
    SHARE_CREATION_STATE_ATTRIBUTE_NAME,
    ProvisionStatus,
)
from storage.tests.helpers import message_of, state_of
from storage.utils import GroupNotFoundError
from storage.vast import course, project, vast


@pytest.fixture
def fake_ad_search(ad_search):
    """conftest's stand-in ADSearch instance, which lookup_ad_entries gets."""
    return ad_search


@pytest.fixture
def share_state_attribute_type(make_attribute_type):
    """update_allocation_attribute_value looks this type up, so it must already exist."""
    return make_attribute_type(SHARE_CREATION_STATE_ATTRIBUTE_NAME)


@pytest.fixture
def allocation(storage_resource, make_allocation, share_state_attribute_type):
    return make_allocation(storage_resource)


# --- the plain share task ----------------------------------------------------------------


@patch("storage.vast.vast.provision_share")
def test_create_share_records_success(mock_provision, allocation):
    vast.create_share("/projects/p", 10, "owner", "group", "hpc", allocation.pk)

    mock_provision.assert_called_once_with("/projects/p", 10, "hpc")
    assert state_of(allocation) == ProvisionStatus.SUCCESSFUL


@patch("storage.vast.vast.provision_share")
def test_create_share_records_failure_and_reraises(mock_provision, allocation):
    mock_provision.side_effect = RuntimeError("VAST exploded")

    with pytest.raises(RuntimeError):
        vast.create_share("/projects/p", 10, "owner", "group", "hpc", allocation.pk)

    assert state_of(allocation) == ProvisionStatus.FAILED


@patch("storage.vast.vast.provision_smb_share")
def test_create_smb_share_records_success(mock_provision, allocation):
    vast.create_smb_share("/projects/p", 10, "owner", "group", "hpc", allocation.pk)

    mock_provision.assert_called_once_with("/projects/p", 10, "group", "hpc")
    assert state_of(allocation) == ProvisionStatus.SUCCESSFUL


# --- the action itself holds no state ----------------------------------------------------


@patch("storage.vast.vast.get_vast_params")
@patch("storage.vast.vast.get_vast_client")
def test_provision_share_records_no_allocation_state(mock_client, mock_params, allocation):
    mock_params.return_value = {"include_share": False, "protocols": [], "view_policy_id": 1}
    mock_client.return_value.get_views.return_value = ["existing"]
    mock_client.return_value.get_quotas.return_value = ["existing"]
    mock_client.return_value.get_protected_paths.return_value = ["existing"]

    vast.provision_share("/projects/p", 10, "hpc")

    assert state_of(allocation) is None, "provisioning must leave the state to its caller"


@pytest.mark.parametrize("native_path, quota_bytes", [("", 10), ("/projects/p", 0), (None, 10)])
@patch("storage.vast.vast.get_vast_params")
@patch("storage.vast.vast.get_vast_client")
def test_provision_share_rejects_missing_inputs(mock_client, mock_params, native_path, quota_bytes):
    with pytest.raises(ValueError):
        vast.provision_share(native_path, quota_bytes, "hpc")


# --- the project task owns its own state -------------------------------------------------


@patch("storage.vast.project.create_project_share_file_structure")
@patch("storage.vast.project.check_folder")
@patch("storage.vast.project.provision_share")
def test_create_project_share_records_success(mock_provision, mock_check, mock_structure, allocation):
    """A fully successful run must end at success, not at the pending it started from."""
    project.create_project_share("/Projects/P", 10, "owner", "group", "hpc", allocation.pk)

    mock_provision.assert_called_once_with(native_path="/projects/p", quota_bytes=10, client_config_id="hpc")
    # the folder steps keep the un-lowercased path they were always given
    mock_check.assert_called_once_with("/Projects/P", client_config_id="hpc")
    assert state_of(allocation) == ProvisionStatus.SUCCESSFUL


@patch("storage.utils.schedule")
@patch("storage.vast.project.create_project_share_file_structure")
@patch("storage.vast.project.check_folder")
@patch("storage.vast.project.provision_share")
def test_create_project_share_waits_when_retries_remain(
    mock_provision, mock_check, mock_structure, mock_schedule, allocation
):
    mock_structure.side_effect = GroupNotFoundError("no group yet")

    project.create_project_share("/projects/p", 10, "owner", "group", "hpc", allocation.pk, retries=2)

    mock_schedule.assert_called_once()
    assert state_of(allocation) == ProvisionStatus.SCHEDULED, "the retry state must not be overwritten by success"


@patch("storage.vast.project.create_project_share_file_structure")
@patch("storage.vast.project.check_folder")
@patch("storage.vast.project.provision_share")
def test_create_project_share_fails_when_retries_exhausted(mock_provision, mock_check, mock_structure, allocation):
    mock_structure.side_effect = GroupNotFoundError("no group ever")

    with pytest.raises(GroupNotFoundError):
        project.create_project_share("/projects/p", 10, "owner", "group", "hpc", allocation.pk, retries=0)

    assert state_of(allocation) == ProvisionStatus.FAILED


@patch("storage.vast.project.create_project_share_file_structure")
@patch("storage.vast.project.check_folder")
@patch("storage.vast.project.provision_share")
def test_create_project_share_fails_when_provisioning_fails(mock_provision, mock_check, mock_structure, allocation):
    """Provisioning is now inside the task's own try, so its failure is the task's failure."""
    mock_provision.side_effect = RuntimeError("VAST exploded")

    with pytest.raises(RuntimeError):
        project.create_project_share("/projects/p", 10, "owner", "group", "hpc", allocation.pk)

    mock_check.assert_not_called()
    assert state_of(allocation) == ProvisionStatus.FAILED


# --- the course task owns its own state --------------------------------------------------


@patch("storage.vast.course.create_course_folders")
@patch("storage.vast.course.native_path_to_cluster_path")
@patch("storage.vast.course.check_folder")
@patch("storage.vast.course.provision_share")
def test_create_course_share_records_success(
    mock_provision, mock_check, mock_cluster, mock_folders, allocation, fake_ad_search
):
    mock_cluster.return_value = "/cluster/p"
    fake_ad_search.get_ad_user.return_value = {"uidNumber": [1000]}
    fake_ad_search.get_ad_group.return_value = {"gidNumber": [2000]}

    course.create_course_share("/Courses/C", 10, "owner", "group", "hpc", allocation.pk)

    mock_provision.assert_called_once_with(native_path="/courses/c", quota_bytes=10, client_config_id="hpc")
    assert state_of(allocation) == ProvisionStatus.SUCCESSFUL


@patch("storage.utils.schedule")
@patch("storage.vast.course.check_folder")
@patch("storage.vast.course.provision_share")
def test_create_course_share_waits_when_retries_remain(
    mock_provision, mock_check, mock_schedule, allocation, fake_ad_search
):
    fake_ad_search.get_ad_user.return_value = {"uidNumber": [1000]}
    fake_ad_search.get_ad_group.return_value = {"gidNumber": []}

    course.create_course_share("/courses/c", 10, "owner", "group", "hpc", allocation.pk, retries=2)

    mock_schedule.assert_called_once()
    assert state_of(allocation) == ProvisionStatus.SCHEDULED, "the retry state must not be overwritten by success"


# --- the reason lives in its own attribute ------------------------------------------------


@patch("storage.vast.vast.provision_share")
def test_failure_records_the_reason_as_a_message(mock_provision, allocation):
    mock_provision.side_effect = RuntimeError("VAST exploded")

    with pytest.raises(RuntimeError):
        vast.create_share("/projects/p", 10, "owner", "group", "hpc", allocation.pk)

    assert state_of(allocation) == ProvisionStatus.FAILED
    assert message_of(allocation) == "VAST exploded", "the state says what, the message says why"


@patch("storage.vast.vast.provision_share")
def test_success_clears_a_previous_failure_message(mock_provision, allocation):
    """A reason must not outlive the state it explained."""
    mock_provision.side_effect = RuntimeError("VAST exploded")
    with pytest.raises(RuntimeError):
        vast.create_share("/projects/p", 10, "owner", "group", "hpc", allocation.pk)
    assert message_of(allocation) == "VAST exploded"

    mock_provision.side_effect = None
    vast.create_share("/projects/p", 10, "owner", "group", "hpc", allocation.pk)

    assert state_of(allocation) == ProvisionStatus.SUCCESSFUL
    assert message_of(allocation) == "", "the stale reason must be cleared, not left behind"


@patch("storage.utils.schedule")
@patch("storage.vast.project.create_project_share_file_structure")
@patch("storage.vast.project.check_folder")
@patch("storage.vast.project.provision_share")
def test_waiting_records_what_it_is_waiting_for(
    mock_provision, mock_check, mock_structure, mock_schedule, allocation
):
    mock_structure.side_effect = GroupNotFoundError("Could not find group with GID 'g' in AD")

    project.create_project_share("/projects/p", 10, "owner", "group", "hpc", allocation.pk, retries=2)

    assert state_of(allocation) == ProvisionStatus.SCHEDULED
    assert message_of(allocation) == "Could not find group with GID 'g' in AD"


def test_provision_status_stores_its_bare_value(allocation):
    """update_allocation_attribute_value stores str(value), which must not be 'ProvisionStatus.STARTED'."""
    from storage.utils import set_share_creation_state

    set_share_creation_state(allocation, ProvisionStatus.STARTED, sender=None)

    stored = allocation.allocationattribute_set.get(
        allocation_attribute_type__name=SHARE_CREATION_STATE_ATTRIBUTE_NAME
    ).value
    assert stored == "started"
    # every status a task can reach, and nothing else
    assert [str(status) for status in ProvisionStatus] == ["started", "failed", "scheduled", "successful"]
