"""New shares must have lowercase names, unless STORAGE_REQUIRE_LOWERCASE_SHARE_NAMES is False.

Only creating a share is checked. Shares that already exist keep whatever case they have (some
older VAST shares are mixed case), so quota changes use the allocation's path exactly as stored,
and nothing on the way lowercases a path.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from coldfront_utils.signals import share_creation_failed

from storage import tasks
from storage.constants import (
    QUOTA_ATTRIBUTE_NAME,
    QUOTA_UPDATE_STATE_ATTRIBUTE_NAME,
    QUOTA_UPDATE_TASK_ID_ATTRIBUTE_NAME,
    SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME,
    SHARE_CREATION_STATE_ATTRIBUTE_NAME,
    SHARE_CREATION_TASK_ID_ATTRIBUTE_NAME,
    ProvisionStatus,
)
from storage.tests.helpers import message_of, state_of
from storage.vast import vast


@pytest.fixture(autouse=True)
def attribute_types(make_attribute_type):
    for name in (
        SHARE_CREATION_STATE_ATTRIBUTE_NAME,
        SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME,
        SHARE_CREATION_TASK_ID_ATTRIBUTE_NAME,
        QUOTA_UPDATE_STATE_ATTRIBUTE_NAME,
        QUOTA_UPDATE_TASK_ID_ATTRIBUTE_NAME,
    ):
        make_attribute_type(name)


@pytest.fixture
def group():
    """The allocation's group, as get_allocation_group finds it."""
    return SimpleNamespace(name="smithlab")


@pytest.fixture
def allocation_at(storage_resource, make_allocation, group):
    """An allocation with a quota and the given VAST path, whose resource has share and quota tasks."""
    handler = SimpleNamespace(
        create_share_task="storage.vast.create_share",
        create_client_id="hpc",
        set_quota_task="storage.vast.set_quota",
        quota_client_id="hpc",
    )
    with (
        patch("storage.tasks.get_storage_handler", return_value=handler),
        patch("storage.tasks.get_client_config", return_value={"native_path_attribute_name": "vast_path"}),
        patch("storage.tasks.get_allocation_group", side_effect=lambda allocation: group.name),
        patch("storage.tasks.STORAGE_LOG_ONLY", False),
    ):
        yield lambda path: make_allocation(storage_resource, attributes={QUOTA_ATTRIBUTE_NAME: "10", "vast_path": path})


@pytest.fixture
def queued():
    with patch("storage.tasks.async_task", return_value="task-1") as async_task:
        yield async_task


def queued_path(queued):
    task, native_path, *_ = queued.call_args.args
    return native_path


# --- creating a share ----------------------------------------------------------------------------


def test_a_mixed_case_share_name_is_refused_by_default(allocation_at, queued):
    allocation = allocation_at("/projects/SmithLab")
    failures = []

    def record_failure(sender, **kwargs):
        failures.append(kwargs)

    share_creation_failed.connect(record_failure)
    try:
        tasks.create_share(allocation.pk)
    finally:
        share_creation_failed.disconnect(record_failure)

    queued.assert_not_called()
    assert state_of(allocation) == ProvisionStatus.FAILED
    assert message_of(allocation) == "share name isn't lowercase"
    [failure] = failures
    assert isinstance(failure["exception"], ValueError)
    assert "SmithLab" in str(failure["exception"])


def test_a_mixed_case_share_name_is_refused_when_only_logging(allocation_at, queued):
    """So a test deployment shows the refusal production would make."""
    allocation = allocation_at("/projects/SmithLab")

    with patch("storage.tasks.STORAGE_LOG_ONLY", True):
        tasks.create_share(allocation.pk)

    assert state_of(allocation) == ProvisionStatus.FAILED
    assert message_of(allocation) == "share name isn't lowercase"


def test_a_lowercase_share_name_is_created_as_it_is(allocation_at, queued):
    tasks.create_share(allocation_at("/projects/smithlab").pk)

    assert queued_path(queued) == "/projects/smithlab"


def test_only_the_share_name_is_checked_not_the_directories_above_it(allocation_at, queued):
    """The directories above come from the resource's blueprint, not the project; they exist already."""
    tasks.create_share(allocation_at("/Projects/smithlab").pk)

    assert queued_path(queued) == "/Projects/smithlab"


def test_a_mixed_case_group_does_not_stop_a_share(allocation_at, queued, group):
    """Some older projects have mixed-case groups. AD ignores case, and the share doesn't create the group."""
    group.name = "VET_RSCH_TZIPORILAB"

    tasks.create_share(allocation_at("/projects/tziporilab").pk)

    queued.assert_called_once()


def test_a_mixed_case_share_name_is_created_as_it_is_when_not_required(allocation_at, queued, settings):
    settings.STORAGE_REQUIRE_LOWERCASE_SHARE_NAMES = False

    tasks.create_share(allocation_at("/projects/SmithLab").pk)

    assert queued_path(queued) == "/projects/SmithLab"


# --- changing a quota ----------------------------------------------------------------------------


def test_a_quota_change_on_a_mixed_case_share_uses_its_path_as_it_is(allocation_at, queued):
    """The check is for new shares only: existing ones keep their case."""
    allocation = allocation_at("/smb/WhittierLab")

    tasks.set_storage_quota(allocation.pk)

    assert queued_path(queued) == "/smb/WhittierLab"


# --- the VAST tasks use the path they're given ---------------------------------------------------


@patch("storage.vast.vast.get_vast_params", return_value={"cluster_path_template": "/cluster/tufts/{directory_name}"})
def test_the_cluster_path_keeps_the_share_names_case(mock_params):
    assert vast.native_path_to_cluster_path("/projects/SmithLab", "hpc") == "/cluster/tufts/SmithLab"
