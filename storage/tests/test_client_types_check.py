"""storage.W003: each STORAGE_PLUGIN_CLIENTS entry says which kind of system it is, for the usage extension."""

import pytest

from storage.checks import check_client_types


def warnings_for(settings, clients):
    settings.STORAGE_PLUGIN_CLIENTS = clients
    return check_client_types(None)


def test_typed_clients_pass(settings):
    clients = [
        {"client_key": "hpc", "type": "vast"},
        {"client_key": "truenas", "type": "truenas"},
        {"client_key": "starfish", "type": "starfish"},
    ]

    assert warnings_for(settings, clients) == []


def test_no_clients_is_fine(settings):
    assert warnings_for(settings, []) == []


@pytest.mark.parametrize(
    "entry", [{"client_key": "hpc"}, {"client_key": "hpc", "type": "VAST"}, {"client_key": "hpc", "type": None}]
)
def test_a_missing_or_unknown_type_is_a_warning_naming_the_client(settings, entry):
    warnings = warnings_for(settings, [entry, {"client_key": "truenas", "type": "truenas"}])

    assert [warning.id for warning in warnings] == ["storage.W003"]
    assert "'hpc'" in warnings[0].msg
