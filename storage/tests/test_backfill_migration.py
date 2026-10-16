"""The backfill must convert every legacy value, and touch nothing it does not recognise.

The values exercised here are the ones actually found in the production copy: 'success',
'pending', 'failed', 'failed: missing information', 'failed: no share creation task or client
configured' and 'already created'.
"""

import importlib

import pytest
from coldfront.core.allocation.models import (
    AllocationAttribute,
    AllocationAttributeType,
    AttributeType,
)
from django.apps import apps as global_apps

from storage.constants import (
    QUOTA_UPDATE_MESSAGE_ATTRIBUTE_NAME,
    QUOTA_UPDATE_STATE_ATTRIBUTE_NAME,
    SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME,
    SHARE_CREATION_STATE_ATTRIBUTE_NAME,
    ProvisionStatus,
)

migration = importlib.import_module("storage.migrations.0002_backfill_provision_status")


# --- the mapping rules, without a database ------------------------------------------------


@pytest.mark.parametrize(
    "legacy, expected",
    [
        ("success", ("successful", "")),
        ("pending", ("started", "")),
        ("already created", ("successful", "already created")),
        ("failed: missing information", ("failed", "missing information")),
        (
            "failed: no share creation task or client configured",
            ("failed", "no share creation task or client configured"),
        ),
        ("failed: no quota update task or client configured", ("failed", "no quota update task or client configured")),
        # tolerate the whitespace and casing a hand-edited value might have
        ("  Success  ", ("successful", "")),
        ("failed:already gone", ("failed", "already gone")),
    ],
)
def test_legacy_values_convert(legacy, expected):
    assert migration.convert_legacy_status(legacy) == expected


@pytest.mark.parametrize("value", ["started", "failed", "scheduled", "successful"])
def test_current_values_are_left_alone(value):
    """A row the new code wrote must not be touched, or its message would be cleared."""
    assert migration.convert_legacy_status(value) is None


@pytest.mark.parametrize("value", ["", None, "   ", "in progress", "weird: thing", "OK"])
def test_unrecognised_values_are_left_alone(value):
    assert migration.convert_legacy_status(value) is None


def test_frozen_statuses_still_match_the_enum():
    """The migration freezes its own copy; a drift here means the backfill no longer lines up."""
    assert migration.STATUSES == {str(status) for status in ProvisionStatus}


# --- the backfill against real rows -------------------------------------------------------


@pytest.fixture
def state_type(db):
    return AllocationAttributeType.objects.get_or_create(
        name=SHARE_CREATION_STATE_ATTRIBUTE_NAME,
        defaults={"attribute_type": AttributeType.objects.get_or_create(name="Text")[0]},
    )[0]


@pytest.fixture
def run_backfill():
    """Run the migration's own function against the live app registry."""

    def run():
        migration.backfill(global_apps, None)

    return run


def state_of(allocation):
    return allocation.get_attribute(SHARE_CREATION_STATE_ATTRIBUTE_NAME)


def message_of(allocation):
    return allocation.get_attribute(SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME)


@pytest.fixture
def allocation_with_state(storage_resource, make_allocation, state_type):
    def make(value):
        allocation = make_allocation(storage_resource)
        AllocationAttribute.objects.create(
            allocation=allocation, allocation_attribute_type=state_type, value=value
        )
        return allocation

    return make


def test_backfill_splits_status_from_reason(allocation_with_state, run_backfill):
    allocation = allocation_with_state("failed: missing information")

    run_backfill()

    assert state_of(allocation) == ProvisionStatus.FAILED
    assert message_of(allocation) == "missing information"


def test_backfill_converts_success(allocation_with_state, run_backfill):
    allocation = allocation_with_state("success")

    run_backfill()

    assert state_of(allocation) == ProvisionStatus.SUCCESSFUL
    assert message_of(allocation) is None, "a status with no reason must not gain an empty message"


def test_backfill_converts_pending_to_started(allocation_with_state, run_backfill):
    allocation = allocation_with_state("pending")

    run_backfill()

    assert state_of(allocation) == ProvisionStatus.STARTED


def test_backfill_keeps_the_already_created_wording(allocation_with_state, run_backfill):
    allocation = allocation_with_state("already created")

    run_backfill()

    assert state_of(allocation) == ProvisionStatus.SUCCESSFUL
    assert message_of(allocation) == "already created"


def test_backfill_leaves_unrecognised_values_untouched(allocation_with_state, run_backfill):
    allocation = allocation_with_state("something nobody expected")

    run_backfill()

    assert state_of(allocation) == "something nobody expected"
    assert message_of(allocation) is None


def test_backfill_does_not_clear_a_message_the_tasks_wrote(
    allocation_with_state, run_backfill, db
):
    """A row already in the new format keeps its reason."""
    allocation = allocation_with_state(str(ProvisionStatus.FAILED))
    message_type = AllocationAttributeType.objects.get_or_create(
        name=SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME,
        defaults={"attribute_type": AttributeType.objects.get_or_create(name="Text")[0]},
    )[0]
    AllocationAttribute.objects.create(
        allocation=allocation, allocation_attribute_type=message_type, value="VAST exploded"
    )

    run_backfill()

    assert state_of(allocation) == ProvisionStatus.FAILED
    assert message_of(allocation) == "VAST exploded"


def test_backfill_is_idempotent(allocation_with_state, run_backfill):
    allocation = allocation_with_state("failed: missing information")

    run_backfill()
    run_backfill()

    assert state_of(allocation) == ProvisionStatus.FAILED
    assert message_of(allocation) == "missing information"


def test_backfill_creates_the_message_attribute_type(allocation_with_state, run_backfill):
    allocation_with_state("failed: missing information")
    assert not AllocationAttributeType.objects.filter(name=SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME).exists()

    run_backfill()

    message_type = AllocationAttributeType.objects.get(name=SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME)
    assert message_type.is_private, "a reason can name a user or a path, so it stays private"


def test_backfill_truncates_a_reason_that_would_not_fit(allocation_with_state, run_backfill):
    """AllocationAttribute.value is a CharField(max_length=128)."""
    allocation = allocation_with_state(f"failed: {'x' * 200}")

    run_backfill()

    assert state_of(allocation) == ProvisionStatus.FAILED
    assert len(message_of(allocation)) == 128


def test_backfill_covers_quota_update_as_well(storage_resource, make_allocation, run_backfill, db):
    quota_state_type = AllocationAttributeType.objects.get_or_create(
        name=QUOTA_UPDATE_STATE_ATTRIBUTE_NAME,
        defaults={"attribute_type": AttributeType.objects.get_or_create(name="Text")[0]},
    )[0]
    allocation = make_allocation(storage_resource)
    AllocationAttribute.objects.create(
        allocation=allocation,
        allocation_attribute_type=quota_state_type,
        value="failed: no quota update task or client configured",
    )

    run_backfill()

    assert allocation.get_attribute(QUOTA_UPDATE_STATE_ATTRIBUTE_NAME) == ProvisionStatus.FAILED
    assert (
        allocation.get_attribute(QUOTA_UPDATE_MESSAGE_ATTRIBUTE_NAME)
        == "no quota update task or client configured"
    )
