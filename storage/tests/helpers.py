"""Plain helpers shared by the test modules (fixtures live in conftest.py)."""

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
