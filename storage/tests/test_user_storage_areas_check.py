"""storage.E002: USER_STORAGE_AREAS, if set, names areas the storage extension can look up."""

import pytest

from storage.checks import check_user_storage_areas

CLIENTS = [{"client_key": "hpc"}, {"client_key": "rstore"}]


@pytest.fixture
def clients(settings):
    settings.STORAGE_PLUGIN_CLIENTS = CLIENTS
    return settings


def errors_for(settings, areas):
    settings.USER_STORAGE_AREAS = areas
    return check_user_storage_areas(None)


def test_unset_is_fine(settings):
    if hasattr(settings, "USER_STORAGE_AREAS"):
        del settings.USER_STORAGE_AREAS

    assert check_user_storage_areas(None) == []


def test_well_formed_areas_pass(clients):
    areas = {"home": {"client": "hpc", "path": "/homedir"}, "lab": {"client": "rstore", "path": "/lab"}}

    assert errors_for(clients, areas) == []


def test_not_a_dict_is_an_error(clients):
    assert [error.id for error in errors_for(clients, [("home", "/homedir")])] == ["storage.E002"]


@pytest.mark.parametrize(
    "areas, words",
    [
        ({"home": "/homedir"}, "dict"),
        ({"home": {"path": "/homedir"}}, "client"),
        ({"home": {"client": "hpc"}}, "absolute path"),
        ({"home": {"client": "hpc", "path": "homedir"}}, "absolute path"),
        ({"home": {"client": "vast9", "path": "/homedir"}}, "STORAGE_PLUGIN_CLIENTS"),
        ({"home/x": {"client": "hpc", "path": "/homedir"}}, "slash"),
        ({"": {"client": "hpc", "path": "/homedir"}}, "slash"),
    ],
    ids=["not a dict", "no client", "no path", "relative path", "unknown client", "slash in name", "empty name"],
)
def test_each_malformed_area_is_an_error(clients, areas, words):
    errors = errors_for(clients, areas)

    assert [error.id for error in errors] == ["storage.E002"]
    assert words in errors[0].msg


def test_each_bad_area_is_its_own_error(clients):
    areas = {"home": {"client": "nope", "path": "/homedir"}, "scratch": {"client": "hpc"}}

    assert [error.id for error in errors_for(clients, areas)] == ["storage.E002", "storage.E002"]
