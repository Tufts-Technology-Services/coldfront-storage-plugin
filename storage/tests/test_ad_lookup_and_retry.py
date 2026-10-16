"""lookup_ad_entries and schedule_retry, and the tasks that wait for AD with them."""

import inspect
from unittest.mock import patch

import pytest
from coldfront.core.allocation.models import AllocationAttributeType
from django.utils.module_loading import import_string
from ldap3.core.exceptions import LDAPInvalidFilterError, LDAPSocketOpenError

from storage.constants import CLUSTER_PATH_ATTRIBUTE_NAME, SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME, ProvisionStatus
from storage.directory_structure import tasks as directory_tasks
from storage.tests.helpers import message_of, state_of
from storage.utils import (
    ActiveDirectoryNotFoundError,
    ActiveDirectoryNotReadyError,
    ActiveDirectoryUnavailableError,
    GIDNotFoundError,
    GroupNotFoundError,
    UIDNotFoundError,
    UserNotFoundError,
    lookup_ad_entries,
    records_share_creation_state,
    schedule_retry,
)
from storage.vast import course, project, vast

USER = {"uidNumber": [1000]}
GROUP = {"gidNumber": [2000], "objectSid": ["S-1-5-21-1"]}


@pytest.fixture
def ad(ad_search):
    """conftest's stand-in ADSearch instance, with this module's user and group."""
    ad_search.get_ad_user.return_value = USER
    ad_search.get_ad_group.return_value = GROUP
    return ad_search


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
    ad.get_ad_group.return_value = None

    with pytest.raises(GroupNotFoundError):
        vast.create_smb_share("/rstore/p", 10, "o", "g", "rstore", allocation.pk, retries=0)

    assert state_of(allocation) == ProvisionStatus.FAILED
    assert message_of(allocation) == "Could not find group g in AD"
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


# --- AD that can't be reached, and its connections --------------------------------------------


def test_an_unreachable_ad_is_retryable():
    with patch("storage.utils.ADSearch", side_effect=LDAPSocketOpenError("no route to host")):
        with pytest.raises(ActiveDirectoryUnavailableError) as raised:
            lookup_ad_entries("jdoe01", "lab01")

    assert isinstance(raised.value, ActiveDirectoryNotReadyError), "the retry wrappers catch the base class"


def test_ad_dropping_the_connection_mid_lookup_is_retryable_too(ad):
    ad.get_ad_group.side_effect = LDAPSocketOpenError("connection reset")

    with pytest.raises(ActiveDirectoryUnavailableError):
        lookup_ad_entries(group="lab01")


def test_a_bad_query_is_not_mistaken_for_an_outage(ad):
    """Only connection problems are retried; anything else is a bug to surface at once."""
    ad.get_ad_user.side_effect = LDAPInvalidFilterError("bad filter")

    with pytest.raises(LDAPInvalidFilterError):
        lookup_ad_entries("jdoe01")


@pytest.mark.parametrize("found", [True, False])
def test_every_lookup_unbinds_its_connection(ad, found):
    if not found:
        ad.get_ad_group.return_value = None
    try:
        lookup_ad_entries("jdoe01", "lab01")
    except GroupNotFoundError:
        pass

    ad.conn.unbind.assert_called_once()


# --- SMB-only groups -------------------------------------------------------------------------


def test_an_smb_only_group_needs_no_gid(ad):
    ad.get_ad_group.return_value = {"objectSid": ["S-1-5-21-1"], "gidNumber": []}

    _, group = lookup_ad_entries(group="rstore-lab", require_gid=False)

    assert group["objectSid"] == ["S-1-5-21-1"]


@patch("storage.vast.vast.add_quota_and_protected_path")
@patch("storage.vast.vast.get_vast_params")
@patch("storage.vast.vast.get_vast_client")
def test_an_smb_share_for_a_group_without_a_gid_is_created(mock_client, mock_params, mock_quota, ad, allocation):
    ad.get_ad_group.return_value = {"objectSid": ["S-1-5-21-1"], "gidNumber": []}
    client = mock_client.return_value
    client.get_views.return_value = []
    mock_params.return_value = {"smb_admin_acls": {}, "protocols": ["SMB"], "view_policy_id": 5}

    vast.create_smb_share("/rstore/lab", 10, "o", "rstore-lab", "rstore", allocation.pk)

    assert state_of(allocation) == ProvisionStatus.SUCCESSFUL
    group_acl = client.create_acl_from_str.call_args_list[-1].kwargs
    assert group_acl["sid_str"] == "S-1-5-21-1"
    assert group_acl["uid_or_gid"] is None
    client.add_view.assert_called_once()


# --- the order of the work -------------------------------------------------------------------


@pytest.mark.parametrize("task", [project.create_project_share, course.create_course_share])
def test_waiting_for_ad_makes_no_vast_calls(ad, allocation, task):
    ad.get_ad_group.return_value = None
    module = task.__module__
    with patch(f"{module}.provision_share") as mock_provision, patch(f"{module}.check_folder") as mock_check:
        with patch("storage.utils.schedule"):
            task("/projects/p", 10, "o", "g", "hpc", allocation.pk)

    mock_provision.assert_not_called()
    mock_check.assert_not_called()
    assert state_of(allocation) == ProvisionStatus.SCHEDULED


@patch("storage.utils.schedule")
@patch("storage.directory_structure.tasks.create_project_folders")
@patch("storage.directory_structure.tasks.get_allocation_group", return_value="g")
def test_folder_creation_waits_for_the_owner_too(
    mock_group, mock_folders, mock_schedule, ad, storage_resource, make_allocation
):
    allocation = make_allocation(storage_resource, attributes={CLUSTER_PATH_ATTRIBUTE_NAME: "/cluster/tufts/p"})
    ad.get_ad_user.return_value = None

    directory_tasks.create_folders(allocation.pk, "hpc_project")

    mock_folders.assert_not_called()
    mock_schedule.assert_called_once()


@patch("storage.utils.schedule")
def test_an_unknown_structure_type_fails_before_waiting_for_ad(mock_schedule, ad):
    ad.get_ad_group.return_value = None

    with pytest.raises(ValueError, match="Unknown structure type bogus"):
        directory_tasks.create_folders(1, "bogus")

    ad.get_ad_group.assert_not_called()
    mock_schedule.assert_not_called()


# --- a missing state or message type ----------------------------------------------------------


def test_a_deleted_message_type_is_recreated_and_the_tasks_own_error_kept(allocation):
    AllocationAttributeType.objects.filter(name=SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME).delete()

    @records_share_creation_state
    def task(allocation_pk):
        raise RuntimeError("the real problem")

    with pytest.raises(RuntimeError, match="the real problem"):
        task(allocation.pk)

    assert state_of(allocation) == ProvisionStatus.FAILED
    assert message_of(allocation) == "the real problem"
    assert AllocationAttributeType.objects.get(name=SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME).is_private
