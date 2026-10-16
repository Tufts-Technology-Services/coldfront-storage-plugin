"""lookup_ad_entries and schedule_retry, and the tasks that wait for AD with them."""

import inspect
from unittest.mock import patch

import pytest
from django.utils.module_loading import import_string

from storage.constants import CLUSTER_PATH_ATTRIBUTE_NAME, ProvisionStatus
from storage.directory_structure import tasks as directory_tasks
from storage.tests.helpers import message_of, state_of
from storage.utils import (
    ActiveDirectoryNotFoundError,
    GIDNotFoundError,
    GroupNotFoundError,
    UIDNotFoundError,
    UserNotFoundError,
    lookup_ad_entries,
    schedule_retry,
)
from storage.vast import project, vast

USER = {"uidNumber": [1000]}
GROUP = {"gidNumber": [2000], "objectSid": ["S-1-5-21-1"]}


@pytest.fixture
def ad():
    """The ADSearch instance lookup_ad_entries gets; set get_ad_user/get_ad_group return values."""
    with patch("storage.utils.ADSearch") as ad_search_class:
        ad_search = ad_search_class.return_value
        ad_search.get_ad_user.return_value = USER
        ad_search.get_ad_group.return_value = GROUP
        yield ad_search


@pytest.fixture
def allocation(storage_resource, make_allocation):
    return make_allocation(storage_resource)


# --- lookup_ad_entries -----------------------------------------------------------------------


def test_returns_the_entries_asked_for(ad):
    assert lookup_ad_entries("jdoe01", "lab01") == (USER, GROUP)
    assert lookup_ad_entries(group="lab01") == (None, GROUP)
    ad.get_ad_user.assert_called_once_with("jdoe01")


@pytest.mark.parametrize(
    ("user", "group", "error", "message"),
    [
        (None, GROUP, UserNotFoundError, "Could not find user jdoe01 in AD"),
        ({"uidNumber": []}, GROUP, UIDNotFoundError, "User jdoe01 is in AD but has no UID yet"),
        (USER, None, GroupNotFoundError, "Could not find group lab01 in AD"),
        (USER, {"gidNumber": []}, GIDNotFoundError, "Group lab01 is in AD but has no GID yet"),
    ],
)
def test_each_missing_piece_has_its_own_error_and_message(ad, user, group, error, message):
    ad.get_ad_user.return_value = user
    ad.get_ad_group.return_value = group

    with pytest.raises(error, match=message) as raised:
        lookup_ad_entries("jdoe01", "lab01")

    assert isinstance(raised.value, ActiveDirectoryNotFoundError), "retry wrappers catch the base class"


# --- schedule_retry --------------------------------------------------------------------------


@patch("storage.utils.schedule")
def test_schedules_the_task_by_its_own_path_with_arguments_it_accepts(mock_schedule):
    error = GroupNotFoundError("not yet")
    task_kwargs = dict(
        native_path="/projects/p", quota_bytes=10, owner="o", group="g", client_config_id="hpc", allocation_pk=1
    )

    schedule_retry(project.create_project_share, error, retries=3, wait=7, **task_kwargs)

    path = mock_schedule.call_args.args[0]
    scheduled = {k: v for k, v in mock_schedule.call_args.kwargs.items() if k not in ("schedule_type", "next_run")}
    assert import_string(path) is project.create_project_share
    assert scheduled == {**task_kwargs, "retries": 2, "wait": 7}
    # the scheduled run must be callable with what was scheduled: passing them as kwargs={...}
    # handed the task one argument named "kwargs", which is how TrueNAS's retries used to break
    inspect.signature(project.create_project_share).bind(**scheduled)


@patch("storage.utils.schedule")
def test_with_no_retries_left_the_error_is_raised(mock_schedule):
    error = GIDNotFoundError("never")

    with pytest.raises(GIDNotFoundError) as raised:
        schedule_retry(project.create_project_share, error, retries=0, wait=5)

    assert raised.value is error
    mock_schedule.assert_not_called()


# --- the tasks -------------------------------------------------------------------------------


@patch("storage.utils.schedule")
@patch("storage.vast.vast.get_vast_params")
@patch("storage.vast.vast.get_vast_client")
def test_an_smb_share_waits_for_its_group(mock_client, mock_params, mock_schedule, ad, allocation):
    ad.get_ad_group.return_value = None

    vast.create_smb_share("/rstore/p", 10, "o", "g", "rstore", allocation.pk)

    assert state_of(allocation) == ProvisionStatus.SCHEDULED
    assert import_string(mock_schedule.call_args.args[0]) is vast.create_smb_share
    mock_client.return_value.add_view.assert_not_called()


@patch("storage.utils.schedule")
@patch("storage.vast.vast.get_vast_params")
@patch("storage.vast.vast.get_vast_client")
def test_an_smb_share_fails_once_its_retries_run_out(mock_client, mock_params, mock_schedule, ad, allocation):
    ad.get_ad_group.return_value = {"gidNumber": []}

    with pytest.raises(GIDNotFoundError):
        vast.create_smb_share("/rstore/p", 10, "o", "g", "rstore", allocation.pk, retries=0)

    assert state_of(allocation) == ProvisionStatus.FAILED
    assert message_of(allocation) == "Group g is in AD but has no GID yet"
    mock_schedule.assert_not_called()


@patch("storage.utils.schedule")
@patch("storage.vast.project.check_folder")
@patch("storage.vast.project.provision_share")
def test_a_missing_user_is_waited_for_too(mock_provision, mock_check, mock_schedule, ad, allocation):
    ad.get_ad_user.return_value = None

    project.create_project_share("/projects/p", 10, "newuser01", "g", "hpc", allocation.pk)

    assert state_of(allocation) == ProvisionStatus.SCHEDULED
    assert message_of(allocation) == "Could not find user newuser01 in AD"
    mock_schedule.assert_called_once()


@patch("storage.utils.schedule")
@patch("storage.directory_structure.tasks.create_project_folders")
@patch("storage.directory_structure.tasks.get_allocation_group", return_value="g")
def test_folder_creation_waits_for_the_group(
    mock_group, mock_folders, mock_schedule, ad, storage_resource, make_allocation
):
    allocation = make_allocation(storage_resource, attributes={CLUSTER_PATH_ATTRIBUTE_NAME: "/cluster/tufts/p"})
    ad.get_ad_group.return_value = None

    assert directory_tasks.create_folders(allocation.pk, "hpc_project") is None

    mock_folders.assert_not_called()
    assert import_string(mock_schedule.call_args.args[0]) is directory_tasks.create_folders
    assert mock_schedule.call_args.kwargs["allocation_pk"] == allocation.pk
    assert mock_schedule.call_args.kwargs["structure_type"] == "hpc_project"
    assert mock_schedule.call_args.kwargs["retries"] == 4
