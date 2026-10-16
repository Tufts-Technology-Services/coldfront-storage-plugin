from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache

from storage import starfish
from storage.constants import USAGE_MATCH_ATTRIBUTE_NAME

PATH_ATTRIBUTE_NAME = "sf_vol_path"


@pytest.fixture(autouse=True)
def clear_ttl_cache():
    """get_starfish_usage_data_by_volume is ttl_cached, so results must not leak between tests."""
    cache.clear()
    yield
    cache.clear()


@pytest.mark.parametrize(
    "vol_path",
    ["", None, "no-colon", ":missing-volume", "missing-subfolder:"],
)
def test_validate_starfish_path_rejects_malformed_paths(vol_path):
    with pytest.raises(ValueError):
        starfish.validate_starfish_path(vol_path)


def test_validate_starfish_path_accepts_a_well_formed_path():
    assert starfish.validate_starfish_path("vol1:/some/path") is None


def test_get_path_usage_data_matches_case_insensitively():
    volume_data = [{"vol_path": "vol1:/Some/Path", "logical_size": 123, "sync": 1700000000}]

    usage, last_synced = starfish.get_path_usage_data(volume_data, "vol1:/some/path")

    assert usage == 123
    assert last_synced.year == 2023


def test_get_path_usage_data_without_a_match():
    assert starfish.get_path_usage_data([], "vol1:/some/path") == (None, None)


@patch("storage.starfish.get_client_config")
def test_get_starfish_usage_data_by_volume_keeps_only_the_needed_fields(mock_get_client_config):
    mock_get_client_config.return_value = {"host": "starfish.example", "api_key": "token"}
    client = MagicMock()
    client.request_subfolder_query.return_value = [
        {"vol_path": "vol1:/a", "logical_size": 1, "sync": 1700000000, "unwanted": "drop me"}
    ]

    with patch("starfish_api_client.StarfishAPIClient", return_value=client) as mock_client_class:
        result = starfish.get_starfish_usage_data_by_volume("vol1", "hpc")

    assert result == [{"vol_path": "vol1:/a", "logical_size": 1, "sync": 1700000000}]
    mock_client_class.assert_called_once_with(host="starfish.example", token="token")
    client.request_subfolder_query.assert_called_once_with("vol1")


@patch("storage.starfish.get_client_config")
def test_get_starfish_usage_data_by_volume_is_cached(mock_get_client_config):
    mock_get_client_config.return_value = {"host": "starfish.example", "api_key": "token"}
    client = MagicMock()
    client.request_subfolder_query.return_value = []

    with patch("starfish_api_client.StarfishAPIClient", return_value=client):
        starfish.get_starfish_usage_data_by_volume("vol1", "hpc")
        starfish.get_starfish_usage_data_by_volume("vol1", "hpc")

    client.request_subfolder_query.assert_called_once()


@pytest.fixture
def usage_match_attribute_type(make_attribute_type):
    """get_storage_usage_batch stamps this attribute, which must already exist."""
    return make_attribute_type(USAGE_MATCH_ATTRIBUTE_NAME)


@patch("storage.starfish.update_allocation_usage")
@patch("storage.starfish.get_starfish_usage_data_by_volume")
@patch("storage.starfish.get_client_config")
def test_get_storage_usage_batch_updates_matching_allocations(
    mock_get_client_config,
    mock_get_volume_data,
    mock_update_usage,
    storage_resource,
    make_allocation,
    usage_match_attribute_type,
):
    mock_get_client_config.return_value = {"native_path_attribute_name": PATH_ATTRIBUTE_NAME}
    mock_get_volume_data.return_value = [{"vol_path": "vol1:/good", "logical_size": 123, "sync": 1700000000}]

    matched = make_allocation(storage_resource, attributes={PATH_ATTRIBUTE_NAME: "vol1:/good"})
    unmatched = make_allocation(storage_resource, attributes={PATH_ATTRIBUTE_NAME: "vol1:/absent"})

    assert starfish.get_storage_usage_batch(storage_resource.pk, "hpc") is True

    mock_get_volume_data.assert_called_once_with("vol1", "hpc")
    assert mock_update_usage.call_count == 1
    allocation, usage, _ = mock_update_usage.call_args[0]
    assert allocation.pk == matched.pk
    assert usage == 123

    assert matched.get_attribute(USAGE_MATCH_ATTRIBUTE_NAME) == "Yes"
    assert unmatched.get_attribute(USAGE_MATCH_ATTRIBUTE_NAME) == "No"


@patch("storage.starfish.update_allocation_usage")
@patch("storage.starfish.get_starfish_usage_data_by_volume")
@patch("storage.starfish.get_client_config")
def test_get_storage_usage_batch_stamps_no_for_an_empty_volume_name(
    mock_get_client_config,
    mock_get_volume_data,
    mock_update_usage,
    storage_resource,
    make_allocation,
    usage_match_attribute_type,
):
    mock_get_client_config.return_value = {"native_path_attribute_name": PATH_ATTRIBUTE_NAME}
    mock_get_volume_data.return_value = []

    invalid = make_allocation(storage_resource, attributes={PATH_ATTRIBUTE_NAME: ":/no/volume"})

    assert starfish.get_storage_usage_batch(storage_resource.pk, "hpc") is True

    mock_update_usage.assert_not_called()
    assert invalid.get_attribute(USAGE_MATCH_ATTRIBUTE_NAME) == "No"


@patch("storage.starfish.update_allocation_usage")
@patch("storage.starfish.get_starfish_usage_data_by_volume")
@patch("storage.starfish.get_client_config")
def test_get_storage_usage_batch_stamps_every_malformed_path(
    mock_get_client_config,
    mock_get_volume_data,
    mock_update_usage,
    storage_resource,
    make_allocation,
    usage_match_attribute_type,
):
    """Every way a path can be malformed must produce a verdict, including a path with no ':'."""
    mock_get_client_config.return_value = {"native_path_attribute_name": PATH_ATTRIBUTE_NAME}
    mock_get_volume_data.return_value = []

    colonless = make_allocation(storage_resource, attributes={PATH_ATTRIBUTE_NAME: "no-colon"})
    empty_volume = make_allocation(storage_resource, attributes={PATH_ATTRIBUTE_NAME: ":/no/volume"})
    empty_subfolder = make_allocation(storage_resource, attributes={PATH_ATTRIBUTE_NAME: "vol1:"})
    missing_attribute = make_allocation(storage_resource)

    assert starfish.get_storage_usage_batch(storage_resource.pk, "hpc") is True

    mock_update_usage.assert_not_called()
    for allocation in (colonless, empty_volume, empty_subfolder):
        assert allocation.get_attribute(USAGE_MATCH_ATTRIBUTE_NAME) == "No"
    # an allocation with no path attribute at all has nothing to match, so it stays unstamped
    assert missing_attribute.get_attribute(USAGE_MATCH_ATTRIBUTE_NAME) is None


@patch("storage.starfish.update_allocation_usage")
@patch("storage.starfish.get_starfish_usage_data_by_volume")
@patch("storage.starfish.get_client_config")
def test_get_storage_usage_batch_does_not_query_bogus_volumes(
    mock_get_client_config,
    mock_get_volume_data,
    mock_update_usage,
    storage_resource,
    make_allocation,
    usage_match_attribute_type,
):
    """Volume names are harvested only from paths that validated, so Starfish is never asked
    about a volume invented from a malformed value."""
    mock_get_client_config.return_value = {"native_path_attribute_name": PATH_ATTRIBUTE_NAME}
    mock_get_volume_data.return_value = []

    make_allocation(storage_resource, attributes={PATH_ATTRIBUTE_NAME: "vol1:/good"})
    make_allocation(storage_resource, attributes={PATH_ATTRIBUTE_NAME: "no-colon"})
    make_allocation(storage_resource, attributes={PATH_ATTRIBUTE_NAME: ":/no/volume"})

    starfish.get_storage_usage_batch(storage_resource.pk, "hpc")

    queried = sorted(call[0][0] for call in mock_get_volume_data.call_args_list)
    assert queried == ["vol1"]
