"""Plain helpers shared by the test modules (fixtures live in conftest.py)."""

from coldfront_utils.tasks import SCHEDULED, Rescheduled, build_task_result

from storage.constants import (
    QUOTA_UPDATE_MESSAGE_ATTRIBUTE_NAME,
    QUOTA_UPDATE_STATE_ATTRIBUTE_NAME,
    SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME,
    SHARE_CREATION_STATE_ATTRIBUTE_NAME,
)


def state_of(allocation):
    return allocation.get_attribute(SHARE_CREATION_STATE_ATTRIBUTE_NAME)


def message_of(allocation):
    return allocation.get_attribute(SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME)


def quota_state_of(allocation):
    return allocation.get_attribute(QUOTA_UPDATE_STATE_ATTRIBUTE_NAME)


def quota_message_of(allocation):
    return allocation.get_attribute(QUOTA_UPDATE_MESSAGE_ATTRIBUTE_NAME)


#: where @reschedulable schedules a task's retry, for tests to patch
RESCHEDULE = "coldfront_utils.tasks.reschedulable.schedule"


def rescheduled_result(reason):
    """What a @reschedulable task body returns once it has scheduled its retry in 5 minutes."""
    message = f"{reason} (runs again in 5 minutes, 4 retries left)"
    return Rescheduled(
        build_task_result(SCHEDULED, message), reason=reason, retries_left=4, next_run="2026-11-05T12:05:00+00:00"
    )
