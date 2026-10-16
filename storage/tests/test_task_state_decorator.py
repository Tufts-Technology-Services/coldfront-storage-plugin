"""The state decorators own every transition, so no task can forget one.

records_share_creation_state and records_quota_update_state come from the same factory and
behave identically; they differ only in which pair of attributes they record into.
"""

from unittest.mock import patch

import pytest
from coldfront.core.allocation.models import AllocationAttributeType, AttributeType

from storage import truenas
from storage.constants import (
    QUOTA_UPDATE_MESSAGE_ATTRIBUTE_NAME,
    QUOTA_UPDATE_STATE_ATTRIBUTE_NAME,
    SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME,
    SHARE_CREATION_STATE_ATTRIBUTE_NAME,
    ProvisionStatus,
)
from storage.utils import GroupNotFoundError, TaskRetryScheduled, records_share_creation_state
from storage.vast import vast


@pytest.fixture
def share_state_attribute_type(db):
    return AllocationAttributeType.objects.get_or_create(
        name=SHARE_CREATION_STATE_ATTRIBUTE_NAME,
        defaults={"attribute_type": AttributeType.objects.get_or_create(name="Text")[0]},
    )[0]


@pytest.fixture
def allocation(storage_resource, make_allocation, share_state_attribute_type):
    return make_allocation(storage_resource)


def state_of(allocation):
    return allocation.get_attribute(SHARE_CREATION_STATE_ATTRIBUTE_NAME)


def message_of(allocation):
    return allocation.get_attribute(SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME)


# --- what the decorator guarantees -------------------------------------------------------


def test_success_is_recorded(allocation):
    @records_share_creation_state
    def task(allocation_pk):
        return "done"

    assert task(allocation.pk) == {"message": "successful:", "result": "done"}
    assert state_of(allocation) == ProvisionStatus.SUCCESSFUL
    assert message_of(allocation) == ""


def test_failure_is_recorded_with_the_reason_and_reraised(allocation):
    @records_share_creation_state
    def task(allocation_pk):
        raise RuntimeError("it broke")

    with pytest.raises(RuntimeError):
        task(allocation.pk)

    assert state_of(allocation) == ProvisionStatus.FAILED
    assert message_of(allocation) == "it broke"


def test_retry_is_recorded_as_waiting_and_not_reraised(allocation):
    """A scheduled retry is not a failure, so it is swallowed and recorded as waiting."""

    @records_share_creation_state
    def task(allocation_pk):
        raise TaskRetryScheduled("group not in AD yet")

    assert task(allocation.pk) == {"message": "scheduled:group not in AD yet"}
    assert state_of(allocation) == ProvisionStatus.SCHEDULED
    assert message_of(allocation) == "group not in AD yet"


def test_body_runs_with_the_state_already_pending(allocation):
    seen = {}

    @records_share_creation_state
    def task(allocation_pk):
        seen["state"] = state_of(allocation)

    task(allocation.pk)

    assert seen["state"] == ProvisionStatus.STARTED, "the body must run with pending already recorded"
    assert state_of(allocation) == ProvisionStatus.SUCCESSFUL


def test_allocation_pk_is_found_however_it_is_passed(allocation):
    """django_q dispatches these tasks positionally in some places and by keyword in others."""

    @records_share_creation_state
    def task(native_path, allocation_pk, retries=5):
        return native_path

    task("/projects/p", allocation.pk)
    assert state_of(allocation) == ProvisionStatus.SUCCESSFUL

    task(native_path="/projects/p", allocation_pk=allocation.pk)
    assert state_of(allocation) == ProvisionStatus.SUCCESSFUL

    task("/projects/p", allocation_pk=allocation.pk, retries=1)
    assert state_of(allocation) == ProvisionStatus.SUCCESSFUL


def test_decorated_task_keeps_its_identity(allocation):
    """django_q resolves tasks by dotted path, so the wrapper must not rename the function."""

    @records_share_creation_state
    def create_something(allocation_pk):
        pass

    assert create_something.__name__ == "create_something"


# --- the truenas tasks, which had no coverage before --------------------------------------


@patch("storage.truenas.provision_share")
def test_truenas_create_share_records_success(mock_provision, allocation):
    truenas.create_share("/mnt/p", 10, "owner", "group", "tier2", allocation.pk)

    mock_provision.assert_called_once_with("/mnt/p", 10, "owner", "group", "tier2")
    assert state_of(allocation) == ProvisionStatus.SUCCESSFUL


@patch("storage.truenas.provision_share")
def test_truenas_create_share_records_failure(mock_provision, allocation):
    mock_provision.side_effect = RuntimeError("TrueNAS exploded")

    with pytest.raises(RuntimeError):
        truenas.create_share("/mnt/p", 10, "owner", "group", "tier2", allocation.pk)

    assert state_of(allocation) == ProvisionStatus.FAILED
    assert message_of(allocation) == "TrueNAS exploded"


@patch("storage.truenas.schedule")
@patch("storage.truenas.provision_share")
def test_truenas_wait_for_group_waits_when_retries_remain(mock_provision, mock_schedule, allocation):
    mock_provision.side_effect = GroupNotFoundError("Could not find group g in AD")

    truenas.create_share_wait_for_group("/mnt/p", 10, "owner", "group", "tier2", allocation.pk, retries=2)

    mock_schedule.assert_called_once()
    assert state_of(allocation) == ProvisionStatus.SCHEDULED
    assert message_of(allocation) == "Could not find group g in AD"


@patch("storage.truenas.schedule")
@patch("storage.truenas.provision_share")
def test_truenas_wait_for_group_fails_when_retries_exhausted(mock_provision, mock_schedule, allocation):
    mock_provision.side_effect = GroupNotFoundError("Could not find group g in AD")

    with pytest.raises(GroupNotFoundError):
        truenas.create_share_wait_for_group("/mnt/p", 10, "owner", "group", "tier2", allocation.pk, retries=0)

    mock_schedule.assert_not_called()
    assert state_of(allocation) == ProvisionStatus.FAILED


@patch("storage.truenas.provision_share")
def test_truenas_wait_for_group_does_not_nest_state_handling(mock_provision, allocation):
    """It must call the action, not the create_share task, or both would record state."""
    truenas.create_share_wait_for_group("/mnt/p", 10, "owner", "group", "tier2", allocation.pk)

    mock_provision.assert_called_once_with("/mnt/p", 10, "owner", "group", "tier2")
    assert state_of(allocation) == ProvisionStatus.SUCCESSFUL


# --- the quota decorator is a separate channel --------------------------------------------


@pytest.fixture
def quota_state_attribute_type(db):
    return AllocationAttributeType.objects.get_or_create(
        name=QUOTA_UPDATE_STATE_ATTRIBUTE_NAME,
        defaults={"attribute_type": AttributeType.objects.get_or_create(name="Text")[0]},
    )[0]


@pytest.fixture
def quota_allocation(allocation, quota_state_attribute_type):
    return allocation


def quota_state_of(allocation):
    return allocation.get_attribute(QUOTA_UPDATE_STATE_ATTRIBUTE_NAME)


def quota_message_of(allocation):
    return allocation.get_attribute(QUOTA_UPDATE_MESSAGE_ATTRIBUTE_NAME)


@patch("storage.vast.vast.get_vast_client")
def test_vast_set_quota_records_success(mock_client, quota_allocation):
    mock_client.return_value.get_quotas.return_value = [{"id": 7}]

    vast.set_quota("/projects/p", 10, "hpc", quota_allocation.pk)

    mock_client.return_value.update_quota_size.assert_called_once_with(7, 10)
    assert quota_state_of(quota_allocation) == ProvisionStatus.SUCCESSFUL


@patch("storage.vast.vast.get_vast_client")
def test_vast_set_quota_records_failure(mock_client, quota_allocation):
    mock_client.return_value.get_quotas.return_value = []  # no quota to resize

    with pytest.raises(ValueError):
        vast.set_quota("/projects/p", 10, "hpc", quota_allocation.pk)

    assert quota_state_of(quota_allocation) == ProvisionStatus.FAILED
    assert "No existing quota found" in quota_message_of(quota_allocation)


@patch("storage.truenas.get_truenas_client")
def test_truenas_set_quota_records_success(mock_client, quota_allocation):
    mock_client.return_value.get_dataset_info.return_value = {"id": "tank/p"}

    truenas.set_quota("/mnt/p", 10, "tier2", quota_allocation.pk)

    mock_client.return_value.update_quota.assert_called_once_with("/mnt/p", 10)
    assert quota_state_of(quota_allocation) == ProvisionStatus.SUCCESSFUL


@patch("storage.truenas.get_truenas_client")
def test_truenas_set_quota_records_failure(mock_client, quota_allocation):
    mock_client.return_value.get_dataset_info.return_value = None  # dataset missing

    with pytest.raises(ValueError):
        truenas.set_quota("/mnt/p", 10, "tier2", quota_allocation.pk)

    assert quota_state_of(quota_allocation) == ProvisionStatus.FAILED
    assert "does not exist" in quota_message_of(quota_allocation)


@patch("storage.vast.vast.get_vast_client")
def test_quota_decorator_does_not_touch_share_creation_state(mock_client, quota_allocation):
    """The two channels are separate: updating a quota must not disturb the share creation state."""
    from storage.utils import set_share_creation_state

    set_share_creation_state(quota_allocation, ProvisionStatus.SUCCESSFUL, sender=None)
    mock_client.return_value.get_quotas.return_value = []

    with pytest.raises(ValueError):
        vast.set_quota("/projects/p", 10, "hpc", quota_allocation.pk)

    assert quota_state_of(quota_allocation) == ProvisionStatus.FAILED
    assert state_of(quota_allocation) == ProvisionStatus.SUCCESSFUL, "the share state must be left alone"


# --- how django-q2 classifies each outcome ------------------------------------------------
#
# django_q.worker sets task["success"] solely on whether an exception escapes the task callable.
# These run through django_q's own sync path (worker + monitor, writing a real Task row) rather
# than asserting that from reading its source.


@records_share_creation_state
def _succeeding_task(allocation_pk):
    return "done"


@records_share_creation_state
def _retrying_task(allocation_pk):
    raise TaskRetryScheduled("group not in AD yet")


@records_share_creation_state
def _failing_task(allocation_pk):
    raise RuntimeError("it broke")


def run_through_django_q(func, allocation):
    """Run a task through django_q's real worker and monitor, and return the Task row.

    This builds the signed package the way async_task does but leaves the "sync" flag off. With
    it on, the worker re-raises a failing task to the caller and the monitor never records it,
    which is a convenience for synchronous callers rather than how the cluster behaves.
    """
    from django.utils import timezone
    from django_q.humanhash import uuid
    from django_q.models import Task
    from django_q.signing import SignedPackage
    from django_q.tasks import _sync

    tag = uuid()
    pack = SignedPackage.dumps({
        "id": tag[1],
        "name": tag[0],
        "func": func,
        "args": (),
        "kwargs": {"allocation_pk": allocation.pk},
        "started": timezone.now(),
    })
    return Task.objects.get(id=_sync(pack))


def test_django_q_marks_a_successful_task_successful(allocation):
    task = run_through_django_q(_succeeding_task, allocation)

    assert task.success is True
    assert task.result == {"message": "successful:", "result": "done"}
    assert state_of(allocation) == ProvisionStatus.SUCCESSFUL


def test_django_q_marks_a_retrying_task_successful(allocation):
    """A scheduled retry is a success to django-q2: the decorator swallows TaskRetryScheduled,
    so nothing escapes the callable. The returned message is what distinguishes it from a completed
    task in the Task list."""
    task = run_through_django_q(_retrying_task, allocation)

    assert task.success is True
    assert task.result == {"message": "scheduled:group not in AD yet"}
    assert state_of(allocation) == ProvisionStatus.SCHEDULED


def test_django_q_marks_a_failing_task_failed(allocation):
    task = run_through_django_q(_failing_task, allocation)

    assert task.success is False
    assert "it broke" in task.result
    assert state_of(allocation) == ProvisionStatus.FAILED


# --- the returned dict ---------------------------------------------------------------------


def test_a_task_returning_nothing_still_returns_a_message(allocation):
    @records_share_creation_state
    def task(allocation_pk):
        pass

    assert task(allocation.pk) == {"message": "successful:"}


def test_a_dict_from_the_body_is_carried_through(allocation):
    @records_share_creation_state
    def task(allocation_pk):
        return {"path": "/projects/p", "bytes": 10}

    assert task(allocation.pk) == {"path": "/projects/p", "bytes": 10, "message": "successful:"}


def test_a_non_dict_from_the_body_is_kept_under_result(allocation):
    @records_share_creation_state
    def task(allocation_pk):
        return ["a", "b"]

    assert task(allocation.pk) == {"result": ["a", "b"], "message": "successful:"}


def test_the_status_message_wins_over_one_from_the_body(allocation):
    """message is reserved, so a caller can always rely on it being the task's status."""

    @records_share_creation_state
    def task(allocation_pk):
        return {"message": "something else", "path": "/projects/p"}

    assert task(allocation.pk) == {"message": "successful:", "path": "/projects/p"}


def test_the_message_carries_the_reason_for_waiting(allocation):
    @records_share_creation_state
    def task(allocation_pk):
        raise TaskRetryScheduled("Could not find group with GID 'g' in AD")

    result = task(allocation.pk)

    assert result["message"] == "scheduled:Could not find group with GID 'g' in AD"
    state, _, reason = result["message"].partition(":")
    assert state == ProvisionStatus.SCHEDULED
    assert reason == message_of(allocation), "the message mirrors what was recorded on the allocation"


def test_a_failing_task_raises_rather_than_returning(allocation):
    """Failure keeps propagating, so django-q2 still records the task as failed."""

    @records_share_creation_state
    def task(allocation_pk):
        raise RuntimeError("it broke")

    with pytest.raises(RuntimeError):
        task(allocation.pk)
