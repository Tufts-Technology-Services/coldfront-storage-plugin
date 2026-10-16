"""Recording a final state sends the matching outcome signal from coldfront_utils.signals, whichever
path recorded it: a decorated task, or the dispatch tasks in storage.tasks."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from coldfront.core.allocation.models import AllocationAttributeType, AttributeType
from coldfront_utils.signals import (
    quota_update_failed,
    quota_update_successful,
    share_creation_failed,
    share_creation_successful,
)

from storage import tasks, truenas
from storage.constants import (
    QUOTA_ATTRIBUTE_NAME,
    QUOTA_UPDATE_STATE_ATTRIBUTE_NAME,
    SHARE_CREATION_STATE_ATTRIBUTE_NAME,
    SHARE_CREATION_TASK_ID_ATTRIBUTE_NAME,
    ProvisionStatus,
)
from storage.utils import (
    TaskRetryScheduled,
    records_quota_update_state,
    records_share_creation_state,
)
from storage.vast import project, vast

ALL_SIGNALS = (share_creation_successful, share_creation_failed, quota_update_successful, quota_update_failed)


@pytest.fixture
def state_attribute_types(db):
    for name in (SHARE_CREATION_STATE_ATTRIBUTE_NAME, QUOTA_UPDATE_STATE_ATTRIBUTE_NAME):
        AllocationAttributeType.objects.get_or_create(
            name=name,
            defaults={"attribute_type": AttributeType.objects.get_or_create(name="Text")[0]},
        )


@pytest.fixture
def allocation(storage_resource, make_allocation, state_attribute_types):
    return make_allocation(storage_resource)


@contextmanager
def listening(signal, sender=None):
    """Collect everything a signal sends while the block runs."""
    received = []

    def listener(sender, **kwargs):
        received.append({"sender": sender, **kwargs})

    signal.connect(listener, sender=sender, weak=False)
    try:
        yield received
    finally:
        signal.disconnect(listener, sender=sender)


@contextmanager
def listening_to_all():
    """Collect what each of the four outcome signals sends, keyed by signal."""
    received = {signal: [] for signal in ALL_SIGNALS}
    listeners = {}
    for signal in ALL_SIGNALS:

        def listener(sender, _signal=signal, **kwargs):
            received[_signal].append({"sender": sender, **kwargs})

        listeners[signal] = listener
        signal.connect(listener, weak=False)
    try:
        yield received
    finally:
        for signal, listener in listeners.items():
            signal.disconnect(listener)


@contextmanager
def connected(signal, receiver):
    signal.connect(receiver, weak=False)
    try:
        yield
    finally:
        signal.disconnect(receiver)


def exploding(sender, **kwargs):
    raise RuntimeError("receiver exploded")


# --- success ---------------------------------------------------------------------------------


def test_success_sends_the_allocation_and_result(allocation):
    @records_share_creation_state
    def task(allocation_pk):
        return {"path": "/projects/p"}

    with listening(share_creation_successful) as received:
        result = task(allocation.pk)

    assert len(received) == 1
    assert received[0]["allocation"] == allocation
    assert received[0]["result"] == result == {"path": "/projects/p", "message": "successful:"}


def test_the_task_function_is_the_sender(allocation):
    """So a receiver can subscribe to one task rather than all of them."""

    @records_share_creation_state
    def create_something(allocation_pk):
        pass

    with listening(share_creation_successful) as received:
        create_something(allocation.pk)

    assert received[0]["sender"] is create_something, (
        "the sender must be the decorated name a receiver imports, not the function inside"
    )


def test_a_receiver_can_filter_by_sender(allocation):
    @records_share_creation_state
    def wanted(allocation_pk):
        pass

    @records_share_creation_state
    def unwanted(allocation_pk):
        pass

    with listening(share_creation_successful, sender=wanted) as received:
        unwanted(allocation.pk)
        wanted(allocation.pk)

    assert len(received) == 1, "only the subscribed task should reach this receiver"


def test_the_state_is_already_recorded_when_receivers_run(allocation):
    seen = {}

    @records_share_creation_state
    def task(allocation_pk):
        pass

    def listener(sender, allocation, **kwargs):
        seen["state"] = allocation.get_attribute(SHARE_CREATION_STATE_ATTRIBUTE_NAME)

    with connected(share_creation_successful, listener):
        task(allocation.pk)

    assert seen["state"] == ProvisionStatus.SUCCESSFUL, "a receiver must see the allocation as the task left it"


def test_a_broken_receiver_does_not_fail_the_task(allocation):
    """The work is done and recorded by then, so a receiver must not be able to undo that."""

    @records_share_creation_state
    def task(allocation_pk):
        return {"path": "/projects/p"}

    with connected(share_creation_successful, exploding):
        result = task(allocation.pk)

    assert result == {"path": "/projects/p", "message": "successful:"}
    assert allocation.get_attribute(SHARE_CREATION_STATE_ATTRIBUTE_NAME) == ProvisionStatus.SUCCESSFUL


def test_a_broken_receiver_does_not_stop_the_others(allocation):
    @records_share_creation_state
    def task(allocation_pk):
        pass

    reached = []

    def good(sender, **kwargs):
        reached.append(True)

    with connected(share_creation_successful, exploding), connected(share_creation_successful, good):
        task(allocation.pk)

    assert reached == [True]


# --- failure ---------------------------------------------------------------------------------


def test_failure_sends_the_reason_and_the_exception(allocation):
    error = RuntimeError("it broke")

    @records_share_creation_state
    def task(allocation_pk):
        raise error

    with listening_to_all() as received:
        with pytest.raises(RuntimeError):
            task(allocation.pk)

    assert received[share_creation_successful] == []
    [sent] = received[share_creation_failed]
    assert sent["sender"] is task
    assert sent["allocation"] == allocation
    assert sent["message"] == "it broke"
    assert sent["exception"] is error


def test_failure_is_recorded_when_receivers_run(allocation):
    seen = {}

    @records_share_creation_state
    def task(allocation_pk):
        raise RuntimeError("it broke")

    def listener(sender, allocation, **kwargs):
        seen["state"] = allocation.get_attribute(SHARE_CREATION_STATE_ATTRIBUTE_NAME)

    with connected(share_creation_failed, listener):
        with pytest.raises(RuntimeError):
            task(allocation.pk)

    assert seen["state"] == ProvisionStatus.FAILED


def test_a_broken_receiver_does_not_replace_the_tasks_exception(allocation):
    """django-q has to record the task's own failure, not the receiver's."""

    @records_share_creation_state
    def task(allocation_pk):
        raise ValueError("the real problem")

    with connected(share_creation_failed, exploding):
        with pytest.raises(ValueError, match="the real problem"):
            task(allocation.pk)


# --- not an outcome --------------------------------------------------------------------------


def test_a_scheduled_retry_sends_nothing(allocation):
    """A retry is not a completion, so there is no outcome to send yet."""

    @records_share_creation_state
    def task(allocation_pk):
        raise TaskRetryScheduled("group not in AD yet")

    with listening_to_all() as received:
        task(allocation.pk)

    assert all(sent == [] for sent in received.values())


# --- the two channels stay separate ----------------------------------------------------------


def test_the_channels_do_not_cross(allocation):
    @records_quota_update_state
    def quota_task(allocation_pk):
        pass

    @records_quota_update_state
    def failing_quota_task(allocation_pk):
        raise RuntimeError("it broke")

    with listening_to_all() as received:
        quota_task(allocation.pk)
        with pytest.raises(RuntimeError):
            failing_quota_task(allocation.pk)

    assert len(received[quota_update_successful]) == 1
    assert len(received[quota_update_failed]) == 1
    assert received[share_creation_successful] == received[share_creation_failed] == [], (
        "a quota update must not send a share creation outcome"
    )


# --- the dispatch tasks in storage.tasks record states themselves -----------------------------


@pytest.fixture
def dispatchable(storage_resource, make_allocation, state_attribute_types):
    """An allocation with what the dispatch tasks read, and a handler with both tasks configured."""
    allocation = make_allocation(storage_resource, attributes={QUOTA_ATTRIBUTE_NAME: "10", "vast_path": "/projects/p"})
    # different clients for the two jobs, so a test can tell which one a task was handed
    handler = SimpleNamespace(
        create_share_task="storage.vast.create_share",
        create_client_id="share-client",
        set_quota_task="storage.vast.set_quota",
        quota_client_id="quota-client",
    )
    with (
        patch("storage.tasks.get_storage_handler", return_value=handler),
        patch("storage.tasks.get_client_config", return_value={"native_path_attribute_name": "vast_path"}),
        patch("storage.tasks.get_allocation_group", return_value="g"),
    ):
        yield allocation


@patch("storage.tasks.STORAGE_LOG_ONLY", False)  # it defaults to on, which returns before queueing
def test_create_share_hands_the_share_task_the_share_client(dispatchable):
    """Not the quota client: the share is created through the client its handler names for creation."""
    AllocationAttributeType.objects.get_or_create(
        name=SHARE_CREATION_TASK_ID_ATTRIBUTE_NAME,
        defaults={"attribute_type": AttributeType.objects.get_or_create(name="Text")[0]},
    )

    with patch("storage.tasks.async_task", return_value="task-1") as queued:
        tasks.create_share(dispatchable.pk)

    task, native_path, quota_bytes, owner, group, client_id, allocation_pk = queued.call_args.args
    assert task == "storage.vast.create_share"
    assert client_id == "share-client"
    assert allocation_pk == dispatchable.pk


@patch("storage.tasks.STORAGE_LOG_ONLY", True)
def test_log_only_share_creation_sends_successful(dispatchable):
    with listening_to_all() as received:
        tasks.create_share(dispatchable.pk)

    [sent] = received[share_creation_successful]
    assert sent["sender"] is tasks.create_share
    assert sent["result"] == {"message": "successful:"}
    assert received[share_creation_failed] == []


@patch("storage.tasks.STORAGE_LOG_ONLY", True)
def test_log_only_quota_update_sends_successful(dispatchable):
    with listening_to_all() as received:
        tasks.set_storage_quota(dispatchable.pk)

    [sent] = received[quota_update_successful]
    assert sent["sender"] is tasks.set_storage_quota
    assert received[share_creation_successful] == []


def test_missing_information_sends_failed_with_the_raised_exception(dispatchable):
    dispatchable.allocationattribute_set.filter(allocation_attribute_type__name="vast_path").update(value="")

    with listening(share_creation_failed) as received:
        with pytest.raises(ValueError) as raised:
            tasks.create_share(dispatchable.pk)

    [sent] = received
    assert sent["sender"] is tasks.create_share
    assert sent["message"] == "missing information"
    assert sent["exception"] is raised.value


@patch("storage.tasks.get_storage_handler", return_value=None)
def test_no_storage_handler_sends_failed_without_an_exception(mock_handler, allocation):
    with listening_to_all() as received:
        tasks.create_share(allocation.pk)
        tasks.set_storage_quota(allocation.pk)

    [share] = received[share_creation_failed]
    [quota] = received[quota_update_failed]
    assert share["message"] == "no share creation task or client configured"
    assert quota["message"] == "no quota update task or client configured"
    assert share["exception"] is None and quota["exception"] is None


# --- the real tasks --------------------------------------------------------------------------


@patch("storage.vast.vast.provision_share")
def test_vast_create_share_sends_successful(mock_provision, allocation):
    with listening(share_creation_successful) as received:
        vast.create_share("/projects/p", 10, "o", "g", "hpc", allocation.pk)

    assert len(received) == 1
    assert received[0]["result"]["message"] == "successful:"


@patch("storage.vast.project.create_project_share_file_structure")
@patch("storage.vast.project.check_folder")
@patch("storage.vast.project.provision_share")
def test_vast_create_project_share_sends_successful(mock_provision, mock_check, mock_structure, allocation):
    with listening(share_creation_successful) as received:
        project.create_project_share("/projects/p", 10, "o", "g", "hpc", allocation.pk)

    assert len(received) == 1
    assert received[0]["allocation"] == allocation


@patch("storage.truenas.provision_share")
def test_truenas_create_share_sends_successful(mock_provision, allocation):
    with listening(share_creation_successful) as received:
        truenas.create_share("/mnt/p", 10, "o", "g", "tier2", allocation.pk)

    assert len(received) == 1


@patch("storage.vast.vast.get_vast_client")
def test_vast_set_quota_sends_on_the_quota_channel(mock_client, allocation):
    mock_client.return_value.get_quotas.return_value = [{"id": 7}]

    with listening(quota_update_successful) as received:
        vast.set_quota("/projects/p", 10, "hpc", allocation.pk)

    assert len(received) == 1
    assert received[0]["result"]["message"] == "successful:"


@patch("storage.vast.vast.get_vast_client")
def test_vast_set_quota_failure_sends_on_the_quota_channel(mock_client, allocation):
    mock_client.return_value.get_quotas.return_value = []

    with listening(quota_update_failed) as received:
        with pytest.raises(ValueError):
            vast.set_quota("/projects/p", 10, "hpc", allocation.pk)

    assert len(received) == 1
    assert isinstance(received[0]["exception"], ValueError)
