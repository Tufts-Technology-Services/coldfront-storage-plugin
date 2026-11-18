"""StorageUsage: the quota-and-usage fields every storage answer in the REST API shares."""

import pytest

from storage.usage import StorageUsage, limit_of


def test_storage_usage_is_one_limit_and_a_state():
    usage = StorageUsage(limit_bytes=10, used_bytes=4, measured_at="m", limit_reported_at="l")

    assert usage.as_dict() == {
        "limit_bytes": 10,
        "used_bytes": 4,
        "state": "ok",
        "measured_at": "m",
        "limit_reported_at": "l",
    }


@pytest.mark.parametrize(
    "limit, used, state",
    [(10, 4, "ok"), (10, 10, "ok"), (10, 11, "over_limit"), (None, 4, None), (10, None, None)],
)
def test_the_state_compares_usage_with_the_limit(limit, used, state):
    assert StorageUsage(limit_bytes=limit, used_bytes=used).state == state


@pytest.mark.parametrize(
    "soft, hard, limit",
    [(10, 11, 10), (None, 11, 11), (0, 11, 11), (None, None, None), (0, 0, None)],
)
def test_the_limit_is_the_soft_one_else_the_hard_one_and_0_is_none(soft, hard, limit):
    assert limit_of(soft, hard) == limit
