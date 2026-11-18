"""
The storage extension of the REST API: who may see a person's home and scratch, the area list, and
their quota and usage in one area, looked up on VAST. The API itself, which serves the extension,
is tested in coldfront-rest-api-plugin; these call the extension directly.
"""

import logging
from datetime import datetime, timedelta

import pytest
from coldfront_utils.api_extensions import ExtensionNotFound, ExtensionUnavailable, find_extension
from django.contrib.auth.models import Permission, User
from django.core.cache import cache
from django.utils import timezone
from vast_api_client.exceptions import VastConnectionError

from storage import user_storage
from storage.api_extension import StorageAreas

GB = 1_000_000_000

AREAS = {
    "home": {"client": "hpc", "path": "/homedir"},
    "scratch": {"client": "hpc", "path": "/scratch"},
}


def user_entry(identifier, *, path="/homedir", system_id=466, soft=30 * GB, hard=30 * GB, used=GB, kind="username"):
    """A user quota entry as VAST lists it."""
    return {
        "id": hash((identifier, path)) % 100000,
        "path": path,
        "quota_system_id": system_id,
        "soft_limit": soft,
        "hard_limit": hard,
        "used_capacity": used,
        "used_inodes": 1234,
        "state": "OK",
        "entity": {"identifier": identifier, "identifier_type": kind, "is_group": False},
    }


def group_entry(identifier, *, path="/homedir", system_id=466):
    entry = user_entry(identifier, path=path, system_id=system_id, soft=None, hard=None, used=5)
    entry["entity"] = {"identifier": identifier, "identifier_type": "groupname", "is_group": True}
    return entry


class FakeVast:
    """Stands in for VASTClient: the quota and user quota calls, filtered as VAST filters them."""

    def __init__(self, entries=()):
        self.quotas = {
            "/homedir": {
                "id": 484,
                "path": "/homedir",
                "system_id": 466,
                "default_user_quota": {"soft_limit": 30 * GB, "hard_limit": 30 * GB},
                "last_user_quotas_update": "2026-11-16T21:49:07.652033Z",
            },
            "/scratch": {
                "id": 680,
                "path": "/scratch",
                "system_id": 657,
                "default_user_quota": {"soft_limit": 100 * GB, "hard_limit": 100 * GB},
                "last_user_quotas_update": "2026-11-16T21:49:07.652033Z",
            },
        }
        self.entries = list(entries)
        self.calls = []
        self.failure = None

    def _record(self, *call):
        self.calls.append(call)
        if self.failure:
            raise self.failure

    def get_quotas(self, path=None):
        self._record("get_quotas", str(path))
        return [quota for quota in self.quotas.values() if quota["path"].startswith(str(path))]

    def get_quota(self, quota_id):
        self._record("get_quota", quota_id)
        return next(quota for quota in self.quotas.values() if quota["id"] == quota_id)

    def get_user_quotas(self, path=None, identifier=None, **kwargs):
        self._record("get_user_quotas", str(path), str(identifier))
        return [
            entry
            for entry in self.entries
            if entry["path"] == str(path) and entry["entity"]["identifier"] == str(identifier)
        ]


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def storage_settings(settings, monkeypatch):
    settings.USER_STORAGE_AREAS = AREAS
    settings.STORAGE_PLUGIN_CLIENTS = [{"client_key": "hpc", "host": "vms.example.edu", "user": "u", "password": "p"}]
    monkeypatch.setattr(user_storage, "STORAGE_LOG_ONLY", False)
    return settings


@pytest.fixture
def vast(storage_settings, monkeypatch):
    """The fake VAST every area's client is, and no UIDs unless a test gives them."""
    fake = FakeVast()
    monkeypatch.setattr(user_storage, "vast_client_for", lambda client_key: fake)
    monkeypatch.setattr(user_storage, "uid_of", lambda username: None)
    return fake


@pytest.fixture
def make_person(db):
    """A user by username, superuser if asked, holding the permissions named "app_label.codename"."""

    def _make(username, *, superuser=False, staff=False, perms=()):
        person = User.objects.create_user(username, f"{username}@example.com", is_superuser=superuser, is_staff=staff)
        for name in perms:
            app_label, codename = name.split(".")
            person.user_permissions.add(Permission.objects.get(content_type__app_label=app_label, codename=codename))
        # has_perm caches a user's permissions on first use, so hand back a fresh copy
        return User.objects.get(pk=person.pk)

    return _make


@pytest.fixture
def smith(make_person):
    return make_person("smith01")


def ask_area(person, area="home", caller=None):
    return StorageAreas().item(person, area, caller or person, params={})


# registration and permission


def test_the_plugin_registers_the_extension():
    assert find_extension("person", "storage") is StorageAreas


def test_the_permission_for_service_accounts_exists(db):
    permission = Permission.objects.get(content_type__app_label="storage", codename="view_any_storage")

    assert "REST API" in permission.name


# who may see whose


def test_a_person_may_see_their_own(smith):
    assert StorageAreas().allows(smith, smith)


@pytest.mark.parametrize("flags", [{"superuser": True}, {"perms": ["storage.view_any_storage"]}])
def test_superusers_and_holders_of_view_any_storage_may_see_anyones(make_person, smith, flags):
    assert StorageAreas().allows(smith, make_person("caller", **flags))


@pytest.mark.parametrize(
    "flags",
    [
        {},
        {"staff": True},
        {"perms": ["project.can_view_all_projects"]},
        {"perms": ["allocation.can_view_all_allocations"]},
    ],
    ids=["anyone", "staff", "can_view_all_projects", "can_view_all_allocations"],
)
def test_nobody_else_may_see_someone_elses(make_person, smith, flags):
    assert not StorageAreas().allows(smith, make_person("caller", **flags))


# the area list


def test_the_summary_lists_the_areas_by_key_in_settings_order_and_asks_nothing(vast, smith):
    assert StorageAreas().summary(smith, smith, params={}) == {
        "extension": "storage",
        "version": 1,
        "username": "smith01",
        "areas": [{"key": "home"}, {"key": "scratch"}],
    }
    assert vast.calls == []


def test_no_areas_when_none_are_configured(settings, smith):
    settings.USER_STORAGE_AREAS = None

    assert StorageAreas().summary(smith, smith, params={})["areas"] == []


# one area


def test_the_area_shape(vast, smith):
    """The quota and usage fields every storage answer shares (storage.usage.StorageUsage), and
    nothing of VAST's own: no path, limits by kind, inodes or VAST state."""
    vast.entries = [user_entry("smith01", used=12 * GB)]
    before = timezone.now()

    body = ask_area(smith)

    limit_reported_at = body.pop("limit_reported_at")
    assert body == {
        "extension": "storage",
        "version": 1,
        "username": "smith01",
        "area": "home",
        "limit_bytes": 30 * GB,
        "used_bytes": 12 * GB,
        "state": "ok",
        # when VAST last recalculated the area's user usage
        "measured_at": "2026-11-16T21:49:07.652033+00:00",
    }
    # when VAST was asked
    assert before - timedelta(seconds=1) <= datetime.fromisoformat(limit_reported_at) <= timezone.now()


def test_the_areas_path_is_never_in_the_answer(vast, smith):
    vast.entries = [user_entry("smith01")]

    assert "/homedir" not in str(ask_area(smith))


@pytest.mark.parametrize(
    "soft, hard, limit",
    [(200 * GB, 300 * GB, 200 * GB), (200 * GB, None, 200 * GB), (None, 300 * GB, 300 * GB), (0, 0, None)],
    ids=["soft", "soft-alone", "hard-without-soft", "zeros"],
)
def test_a_persons_own_limit_is_the_soft_one_else_the_hard_one(vast, smith, soft, hard, limit):
    vast.entries = [user_entry("smith01", soft=soft, hard=hard)]

    assert ask_area(smith)["limit_bytes"] == limit


def test_over_the_limit_is_over_limit(vast, smith):
    vast.entries = [user_entry("smith01", soft=30 * GB, used=30 * GB + 1)]

    assert ask_area(smith)["state"] == "over_limit"


def test_someone_with_no_entry_gets_the_default_and_nothing_used(vast, smith):
    body = ask_area(smith)

    assert (body["limit_bytes"], body["used_bytes"], body["state"]) == (30 * GB, 0, "ok")


def test_an_unreadable_recalculation_time_is_null(vast, smith):
    vast.quotas["/homedir"]["last_user_quotas_update"] = None

    assert ask_area(smith)["measured_at"] is None


def test_each_area_is_its_own_quota(vast, smith):
    vast.entries = [user_entry("smith01", path="/scratch", system_id=657, soft=100 * GB, hard=100 * GB, used=7 * GB)]

    body = ask_area(smith, "scratch")

    assert (body["area"], body["used_bytes"], body["limit_bytes"]) == ("scratch", 7 * GB, 100 * GB)


def test_someone_elses_area_is_looked_up_for_them_not_the_caller(vast, make_person, smith):
    vast.entries = [user_entry("smith01", used=9 * GB)]

    body = ask_area(smith, caller=make_person("root01", superuser=True))

    assert (body["username"], body["used_bytes"]) == ("smith01", 9 * GB)


@pytest.mark.parametrize(
    "entry",
    [group_entry("smith01"), user_entry("smith01", system_id=999), user_entry("smith01", kind="uid")],
    ids=["a group of the same name", "another quota", "recorded as a uid"],
)
def test_entries_that_arent_the_persons_own_are_ignored(vast, smith, entry):
    vast.entries = [entry]

    assert ask_area(smith)["used_bytes"] == 0


def test_an_entry_marked_as_a_group_is_ignored_whatever_its_identifier_type(vast, smith):
    # VAST gives groups a groupname or gid identifier type too, but is_group is what says it's a group
    entry = user_entry("smith01")
    entry["entity"]["is_group"] = True
    vast.entries = [entry]

    assert ask_area(smith)["used_bytes"] == 0


def test_the_user_entry_is_found_beside_a_group_of_the_same_name(vast, smith):
    vast.entries = [group_entry("smith01"), user_entry("smith01", used=3 * GB)]

    assert ask_area(smith)["used_bytes"] == 3 * GB


def test_someone_vast_records_by_uid_is_found_by_their_uid(vast, smith, monkeypatch):
    monkeypatch.setattr(user_storage, "uid_of", lambda username: 31415)
    vast.entries = [user_entry("31415", kind="uid", used=4 * GB)]

    assert ask_area(smith)["used_bytes"] == 4 * GB
    assert ("get_user_quotas", "/homedir", "31415") in vast.calls


def test_a_uid_isnt_asked_for_when_the_username_is_found(vast, smith, monkeypatch):
    monkeypatch.setattr(user_storage, "uid_of", lambda username: pytest.fail("uid looked up"))
    vast.entries = [user_entry("smith01")]

    assert ask_area(smith)["used_bytes"] == GB


def test_a_uid_matches_only_an_entry_recorded_by_uid(vast, smith, monkeypatch):
    monkeypatch.setattr(user_storage, "uid_of", lambda username: 31415)
    vast.entries = [user_entry("31415", kind="username", used=4 * GB)]

    assert ask_area(smith)["used_bytes"] == 0


def test_uid_of_is_none_for_someone_getent_doesnt_know(monkeypatch):
    def unknown(username):
        raise ValueError(f"User {username} does not exist")

    monkeypatch.setattr(user_storage, "get_uid", unknown)

    assert user_storage.uid_of("ghost01") is None


def test_query_parameters_are_ignored(vast, smith):
    vast.entries = [user_entry("smith01", used=12 * GB)]

    assert StorageAreas().item(smith, "home", smith, params={"page": ["2"]}) == ask_area(smith)


# errors


def test_an_area_that_isnt_configured_is_not_found(vast, smith):
    with pytest.raises(ExtensionNotFound):
        ask_area(smith, "archive")
    assert vast.calls == []


def test_storage_lookups_off_is_unavailable_and_asks_nothing(vast, smith, monkeypatch):
    monkeypatch.setattr(user_storage, "STORAGE_LOG_ONLY", True)

    with pytest.raises(ExtensionUnavailable, match="off"):
        ask_area(smith)
    assert vast.calls == []


def test_vast_failing_is_unavailable_without_vasts_own_text_and_logged(vast, smith, caplog):
    caplog.set_level(logging.ERROR, logger="storage.api_extension")
    vast.failure = VastConnectionError("GET userquotas on vms.example.edu failed: secret detail")

    with pytest.raises(ExtensionUnavailable) as unavailable:
        ask_area(smith)

    assert "vms.example.edu" not in str(unavailable.value)
    assert "secret" not in str(unavailable.value)
    assert ["vms.example.edu" in record.getMessage() for record in caplog.records] == [True]


def test_an_area_path_with_no_quota_is_unavailable(vast, smith, settings):
    settings.USER_STORAGE_AREAS = {"home": {"client": "hpc", "path": "/nowhere"}}

    with pytest.raises(ExtensionUnavailable):
        ask_area(smith)


# caching and clients


def test_an_answer_is_reused_for_a_while(vast, smith):
    vast.entries = [user_entry("smith01")]
    ask_area(smith)
    asked = len(vast.calls)

    ask_area(smith)

    assert len(vast.calls) == asked


def test_an_areas_quota_is_found_once_for_everyone(vast, make_person, smith):
    ask_area(smith)
    ask_area(make_person("jones01"))

    assert [call for call in vast.calls if call[0] == "get_quotas"] == [("get_quotas", "/homedir")]


def test_each_area_asks_its_own_client(storage_settings, smith, monkeypatch):
    storage_settings.USER_STORAGE_AREAS = {
        "home": {"client": "hpc", "path": "/homedir"},
        "lab": {"client": "rstore", "path": "/homedir"},
    }
    asked = []

    def client_for(client_key):
        asked.append(client_key)
        return FakeVast()

    monkeypatch.setattr(user_storage, "vast_client_for", client_for)
    monkeypatch.setattr(user_storage, "uid_of", lambda username: None)

    ask_area(smith, "home")
    ask_area(smith, "lab")

    assert set(asked) == {"hpc", "rstore"}


def test_a_client_is_built_from_its_storage_plugin_entry(storage_settings):
    assert user_storage.vast_client_for("hpc").host == "vms.example.edu"


def test_an_unknown_client_key_is_unavailable(storage_settings):
    with pytest.raises(user_storage.StorageUnavailable):
        user_storage.vast_client_for("nope")
