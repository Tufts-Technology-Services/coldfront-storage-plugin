"""
The usage extension of the REST API, at /api/v2/allocations/<id>/usage/: one storage allocation's
quota and usage. Live from VAST for an allocation on a VAST client, ColdFront's stored values for
one on TrueNAS. The API itself is tested in coldfront-rest-api-plugin; these call the extension
directly. See "GET /api/v2/allocations/<id>/usage/" in that plugin's docs/rest-api-plan.md.
"""

import logging
from datetime import datetime, timedelta

import pytest
from coldfront_utils.api_extensions import ExtensionNotFound, ExtensionUnavailable, find_extension
from django.contrib.auth.models import User
from django.core.cache import cache
from django.utils import timezone
from vast_api_client.exceptions import VastConnectionError

from coldfront.core.allocation.models import AllocationAttributeType, AllocationUserStatusChoice
from coldfront.core.project.models import ProjectUserRoleChoice, ProjectUserStatusChoice
from coldfront.core.resource.models import Resource, ResourceType

from storage import allocation_usage
from storage.api_extension import LOOKUPS_OFF, UNAVAILABLE, AllocationUsage
from storage.models import StorageHandler

TB = 1_000_000_000_000
VAST_PATH = "/projects/ocean"
STORAGE_PATH = "/cluster/tufts/ocean"

CLIENTS = [
    {"client_key": "hpc", "type": "vast", "host": "vms.example.edu", "native_path_attribute_name": "vast_path"},
    {"client_key": "truenas", "type": "truenas", "native_path_attribute_name": "truenas_path"},
    {"client_key": "untyped", "native_path_attribute_name": "vast_path"},
]


def directory_quota(path=VAST_PATH, *, soft=10 * TB, hard=11 * TB, used=6 * TB, effective=5 * TB, state="OK"):
    """A directory quota as VAST lists it."""
    return {
        "id": 77,
        "path": path,
        "soft_limit": soft,
        "hard_limit": hard,
        "used_capacity": used,
        "used_effective_capacity": effective,
        "state": state,
        "pretty_state": state,
    }


class FakeVast:
    """Stands in for VASTClient's get_quotas: every quota whose path starts with the one asked for."""

    def __init__(self, quotas=()):
        self.quotas = list(quotas)
        self.calls = []
        self.failure = None

    def get_quotas(self, path=None):
        self.calls.append(("get_quotas", str(path)))
        if self.failure:
            raise self.failure
        return [quota for quota in self.quotas if quota["path"].startswith(str(path))]


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def clients(settings, monkeypatch):
    settings.STORAGE_PLUGIN_CLIENTS = CLIENTS
    settings.TIME_ZONE = "America/New_York"
    monkeypatch.setattr(allocation_usage, "STORAGE_LOG_ONLY", False)
    return settings


@pytest.fixture
def vast(clients, monkeypatch):
    """The fake VAST every VAST client is, with a quota at VAST_PATH."""
    fake = FakeVast([directory_quota()])
    monkeypatch.setattr(allocation_usage, "vast_client_for", lambda client_key: fake)
    return fake


@pytest.fixture
def make_storage_resource(db):
    """A Storage resource by name, with a StorageHandler whose quota client is client_key (none if None)."""

    def _make(name, client_key):
        resource = Resource.objects.create(
            resource_type=ResourceType.objects.get_or_create(name="Storage", description="storage")[0],
            name=name,
            description=name,
        )
        if client_key is not None:
            StorageHandler.objects.create(resource=resource, quota_client_id=client_key)
        return resource

    return _make


def set_storage_path_private(is_private):
    """The fixtures' Storage Path is public; ColdFront's default for a new attribute type is private."""
    AllocationAttributeType.objects.filter(name="Storage Path").update(is_private=is_private)


@pytest.fixture
def vast_allocation(make_allocation, make_storage_resource):
    allocation = make_allocation(
        make_storage_resource("Tier 3 Storage", "hpc"),
        attributes={
            "vast_path": VAST_PATH,
            "Storage Path": STORAGE_PATH,
            "reported_usage_bytes": str(6150 * 10**9),
            "usage_report_date": "2026-11-03T03:00:00",
        },
    )
    set_storage_path_private(False)
    return allocation


@pytest.fixture
def truenas_allocation(make_allocation, make_storage_resource):
    allocation = make_allocation(
        make_storage_resource("Tier 2 Storage", "truenas"),
        attributes={
            "truenas_path": "tank/projects/ocean",
            "Storage Path": "/cluster/tufts/ocean-t2",
            "reported_quota_bytes": str(2 * TB),
            "quota_report_date": "2026-11-03T02:00:00",
            "reported_usage_bytes": str(1100 * 10**9),
            "usage_report_date": "2026-11-03T03:00:00",
        },
    )
    set_storage_path_private(False)
    return allocation


def join_project(project, user, role):
    """An Active ProjectUser row, which ColdFront's rules want even for the PI (ColdFront makes the PI's)."""
    return project.projectuser_set.create(
        user=user,
        role=ProjectUserRoleChoice.objects.get_or_create(name=role)[0],
        status=ProjectUserStatusChoice.objects.get_or_create(name="Active")[0],
    )


def ask(allocation, caller=None):
    return AllocationUsage().summary(allocation, caller or allocation.project.pi, params={})


# registration and who may see it


def test_the_plugin_registers_the_extension():
    assert find_extension("allocation", "usage") is AllocationUsage


def test_the_projects_pi_may_see_it(vast_allocation):
    join_project(vast_allocation.project, vast_allocation.project.pi, "Manager")

    assert AllocationUsage().allows(vast_allocation, vast_allocation.project.pi)


def test_the_allocations_users_may_see_it(vast_allocation):
    member = User.objects.create_user("member01")
    join_project(vast_allocation.project, member, "User")
    vast_allocation.allocationuser_set.create(
        user=member, status=AllocationUserStatusChoice.objects.get_or_create(name="Active")[0]
    )

    assert AllocationUsage().allows(vast_allocation, member)


def test_superusers_may_see_it(vast_allocation, superuser):
    assert AllocationUsage().allows(vast_allocation, superuser)


def test_nobody_else_may(vast_allocation, regular_user):
    assert not AllocationUsage().allows(vast_allocation, regular_user)


def test_a_project_member_not_on_the_allocation_may_not(vast_allocation, regular_user):
    join_project(vast_allocation.project, regular_user, "User")

    assert not AllocationUsage().allows(vast_allocation, regular_user)


def test_it_has_no_items(vast_allocation):
    with pytest.raises(ExtensionNotFound):
        AllocationUsage().item(vast_allocation, "anything", vast_allocation.project.pi, params={})


# which allocations it serves


def test_an_allocation_with_no_storage_handler_is_not_found(vast, make_allocation, make_storage_resource):
    allocation = make_allocation(make_storage_resource("Cluster", None))

    with pytest.raises(ExtensionNotFound):
        ask(allocation)
    assert vast.calls == []


def test_the_resource_with_a_handler_is_the_one_used(vast, make_allocation, make_storage_resource):
    allocation = make_allocation(
        make_storage_resource("Cluster", None),
        make_storage_resource("Tier 3 Storage", "hpc"),
        attributes={"vast_path": VAST_PATH},
    )

    assert ask(allocation)["resource"] == "Tier 3 Storage"


@pytest.mark.parametrize("client_key", ["untyped", "nowhere", ""])
def test_a_client_without_a_known_type_is_unavailable(vast, make_allocation, make_storage_resource, client_key):
    allocation = make_allocation(make_storage_resource("Odd Storage", client_key), attributes={"vast_path": VAST_PATH})

    with pytest.raises(ExtensionUnavailable) as raised:
        ask(allocation)
    assert str(raised.value) == UNAVAILABLE
    assert vast.calls == []


# the unified shape: the same keys from every source


def test_every_source_answers_with_the_same_keys(vast, vast_allocation, truenas_allocation):
    assert ask(vast_allocation).keys() == ask(truenas_allocation).keys()


def test_the_answer_doesnt_say_where_it_came_from(vast, vast_allocation, truenas_allocation):
    assert "source" not in ask(vast_allocation)
    assert "source" not in ask(truenas_allocation)


# the Storage Path: the path people use, an attribute, with ColdFront's rule for private ones


def make_storage_path_private():
    set_storage_path_private(True)


def test_no_storage_path_is_null(vast, make_allocation, make_storage_resource):
    allocation = make_allocation(make_storage_resource("Tier 3 Storage", "hpc"), attributes={"vast_path": VAST_PATH})

    assert ask(allocation)["path"] is None


def test_a_private_storage_path_is_null_for_anyone_but_superusers(vast, vast_allocation):
    make_storage_path_private()

    assert ask(vast_allocation)["path"] is None


def test_superusers_see_a_private_storage_path(vast, vast_allocation, superuser):
    make_storage_path_private()

    assert ask(vast_allocation, caller=superuser)["path"] == STORAGE_PATH


def test_the_cache_doesnt_pass_a_private_path_to_the_next_caller(vast, vast_allocation, superuser):
    make_storage_path_private()

    assert ask(vast_allocation, caller=superuser)["path"] == STORAGE_PATH
    assert ask(vast_allocation)["path"] is None


# VAST: live


def test_a_vast_allocation_is_asked_of_vast(vast, vast_allocation):
    before = timezone.now()

    answer = ask(vast_allocation)

    measured_at = answer.pop("measured_at")
    assert answer.pop("limit_reported_at") == measured_at
    assert answer == {
        "extension": "usage",
        "version": 1,
        "allocation": vast_allocation.pk,
        "project": {"id": vast_allocation.project.pk, "title": vast_allocation.project.title},
        "resource": "Tier 3 Storage",
        "path": STORAGE_PATH,
        "limit_bytes": 10 * TB,
        "used_bytes": 6 * TB,
        "state": "ok",
        "stored_used_bytes": 6150 * 10**9,
        "stored_usage_reported_at": "2026-11-03T03:00:00-05:00",
    }
    assert before - timedelta(seconds=1) <= datetime.fromisoformat(measured_at) <= timezone.now()
    assert vast.calls == [("get_quotas", VAST_PATH)]


def test_the_path_on_vast_is_never_in_the_answer(vast, vast_allocation):
    assert VAST_PATH not in str(ask(vast_allocation))


def test_only_the_quota_at_exactly_the_path_counts(vast, vast_allocation):
    vast.quotas = [directory_quota(VAST_PATH + "/sub", used=1), directory_quota(VAST_PATH)]

    assert ask(vast_allocation)["used_bytes"] == 6 * TB


@pytest.mark.parametrize(
    "soft, hard, limit",
    [(10 * TB, 11 * TB, 10 * TB), (None, 11 * TB, 11 * TB), (0, 11 * TB, 11 * TB), (None, None, None), (0, 0, None)],
    ids=["soft", "hard-without-soft", "hard-when-soft-is-0", "none", "zeros"],
)
def test_the_limit_is_the_soft_one_else_the_hard_one(vast, vast_allocation, soft, hard, limit):
    vast.quotas = [directory_quota(soft=soft, hard=hard)]

    assert ask(vast_allocation)["limit_bytes"] == limit


def test_over_the_soft_limit_is_over_limit(vast, vast_allocation):
    vast.quotas = [directory_quota(soft=10 * TB, hard=11 * TB, used=10 * TB + 1)]

    assert ask(vast_allocation)["state"] == "over_limit"


def test_answers_are_cached_for_a_while(vast, vast_allocation):
    first = ask(vast_allocation)
    second = ask(vast_allocation)

    assert len(vast.calls) == 1
    assert second["measured_at"] == first["measured_at"]


def test_no_stored_usage_is_null(vast, make_allocation, make_storage_resource):
    allocation = make_allocation(make_storage_resource("Tier 3 Storage", "hpc"), attributes={"vast_path": VAST_PATH})

    answer = ask(allocation)

    assert (answer["stored_used_bytes"], answer["stored_usage_reported_at"]) == (None, None)


def test_no_quota_at_the_path_is_not_found(vast, vast_allocation):
    vast.quotas = []

    with pytest.raises(ExtensionNotFound):
        ask(vast_allocation)


def test_no_path_attribute_is_not_found_and_asks_nothing(vast, make_allocation, make_storage_resource):
    allocation = make_allocation(make_storage_resource("Tier 3 Storage", "hpc"))

    with pytest.raises(ExtensionNotFound):
        ask(allocation)
    assert vast.calls == []


def test_two_quotas_at_the_path_is_unavailable(vast, vast_allocation):
    vast.quotas = [directory_quota(), directory_quota()]

    with pytest.raises(ExtensionUnavailable):
        ask(vast_allocation)


def test_vast_failing_is_unavailable_without_vasts_words_and_logged(vast, vast_allocation, caplog):
    vast.failure = VastConnectionError("vms-secret-name.example.edu timed out")

    with caplog.at_level(logging.ERROR), pytest.raises(ExtensionUnavailable) as raised:
        ask(vast_allocation)

    assert str(raised.value) == UNAVAILABLE
    assert "vms-secret-name" in caplog.text
    assert f"allocation {vast_allocation.pk}" in caplog.text


def test_with_storage_lookups_off_vast_is_not_asked(vast, vast_allocation, monkeypatch):
    monkeypatch.setattr(allocation_usage, "STORAGE_LOG_ONLY", True)

    with pytest.raises(ExtensionUnavailable) as raised:
        ask(vast_allocation)

    assert str(raised.value) == LOOKUPS_OFF
    assert vast.calls == []


# TrueNAS: stored


def test_a_truenas_allocation_answers_with_the_stored_values(vast, truenas_allocation):
    assert ask(truenas_allocation) == {
        "extension": "usage",
        "version": 1,
        "allocation": truenas_allocation.pk,
        "project": {"id": truenas_allocation.project.pk, "title": truenas_allocation.project.title},
        "resource": "Tier 2 Storage",
        "path": "/cluster/tufts/ocean-t2",
        "limit_bytes": 2 * TB,
        "used_bytes": 1100 * 10**9,
        "state": "ok",
        "measured_at": "2026-11-03T03:00:00-05:00",
        "limit_reported_at": "2026-11-03T02:00:00-05:00",
        "stored_used_bytes": 1100 * 10**9,
        "stored_usage_reported_at": "2026-11-03T03:00:00-05:00",
    }
    assert vast.calls == []


def test_a_stored_limit_of_0_is_no_limit(clients, make_allocation, make_storage_resource):
    """TrueNAS (ZFS) records no quota as 0."""
    allocation = make_allocation(
        make_storage_resource("Tier 2 Storage", "truenas"),
        attributes={"reported_quota_bytes": "0", "reported_usage_bytes": "5"},
    )

    answer = ask(allocation)

    assert (answer["limit_bytes"], answer["state"]) == (None, None)


def test_truenas_answers_even_with_storage_lookups_off(vast, truenas_allocation, monkeypatch):
    monkeypatch.setattr(allocation_usage, "STORAGE_LOG_ONLY", True)

    assert ask(truenas_allocation)["used_bytes"] == 1100 * 10**9


def test_missing_or_unreadable_stored_values_are_null(clients, make_allocation, make_storage_resource):
    allocation = make_allocation(
        make_storage_resource("Tier 2 Storage", "truenas"),
        attributes={"reported_quota_bytes": "lots", "quota_report_date": "yesterday"},
    )

    answer = ask(allocation)

    fields = ["limit_bytes", "used_bytes", "state", "measured_at", "limit_reported_at"]
    assert [answer[field] for field in fields] == [None] * len(fields)


def test_a_stored_date_with_a_zone_keeps_it(clients, make_allocation, make_storage_resource):
    allocation = make_allocation(
        make_storage_resource("Tier 2 Storage", "truenas"),
        attributes={"usage_report_date": "2026-11-03T08:00:00+00:00", "reported_usage_bytes": "5.0"},
    )

    answer = ask(allocation)

    assert (answer["measured_at"], answer["used_bytes"]) == ("2026-11-03T08:00:00+00:00", 5)
