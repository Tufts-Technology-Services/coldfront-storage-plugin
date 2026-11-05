"""Batch tasks report counts on the task report, and fail only when most of a run went wrong.

One allocation failing is in the counts, not a failed task: a batch covers many allocations, and a
run that refreshed all but one of them worked.
"""

from unittest.mock import patch

import pytest

from storage import starfish, tasks, truenas
from storage.models import StorageHandler
from storage.utils import BatchMostlyFailedError, BatchTally, PathAttributeTypeMissingError
from storage.vast import vast

VAST_PATH = "vast_path"
SF_PATH = "sf_vol_path"


# --- the tally ---------------------------------------------------------------------------------


def test_the_summary_leaves_out_outcomes_that_did_not_happen():
    assert BatchTally(updated=412, skipped=9, failed=3).summary() == "updated 412, skipped 9, failed 3"


def test_a_resource_with_no_allocations_says_so():
    assert BatchTally().task_result() == {"message": "no allocations"}


def test_a_run_with_a_few_failures_still_succeeds():
    assert BatchTally(updated=9, failed=1).task_result() == {"message": "updated 9, failed 1"}


def test_a_run_that_went_wrong_for_exactly_half_still_succeeds():
    assert BatchTally(updated=2, unmatched=1, failed=1).task_result() == {"message": "updated 2, unmatched 1, failed 1"}


def test_a_run_that_went_wrong_for_most_allocations_fails():
    with pytest.raises(BatchMostlyFailedError, match="3 of 4 allocations went wrong: updated 1, failed 3"):
        BatchTally(updated=1, failed=3).task_result()


def test_unmatched_allocations_count_against_the_run():
    """A storage system with nothing for most paths is broken even though no call raised."""
    with pytest.raises(BatchMostlyFailedError):
        BatchTally(updated=1, unmatched=2).task_result()


def test_skipped_allocations_count_neither_way():
    assert BatchTally(updated=1, skipped=50).task_result() == {"message": "updated 1, skipped 50"}
    with pytest.raises(BatchMostlyFailedError):
        BatchTally(failed=1, skipped=50).task_result()


def test_the_threshold_is_a_setting():
    with patch("storage.utils.STORAGE_BATCH_FAILURE_THRESHOLD", 0.1):
        with pytest.raises(BatchMostlyFailedError):
            BatchTally(updated=8, failed=2).task_result()


# --- VAST quotas -------------------------------------------------------------------------------


def vast_quota(path, soft_limit=10**12):
    return {"path": path, "soft_limit": soft_limit, "hard_limit": None, "pretty_state": "OK"}


@pytest.fixture
def vast_quota_mocks():
    """Stub VAST's quota list, so the batch looks each allocation up in it."""
    with (
        patch("storage.vast.vast.get_client_config", return_value={"native_path_attribute_name": VAST_PATH}),
        patch("storage.vast.vast.update_allocation_attribute_value"),
        patch("storage.vast.vast.get_all_quotas", return_value=[vast_quota("/projects/good")]) as get_all_quotas,
    ):
        yield get_all_quotas


def test_vast_quotas_count_each_allocation(vast_quota_mocks, storage_resource, make_allocation):
    for path in ("/projects/good", "/projects/good", "/projects/good", "/projects/gone"):
        make_allocation(storage_resource, attributes={VAST_PATH: path})
    make_allocation(storage_resource)

    assert vast.get_quotas_batch(storage_resource.pk, "hpc") == {"message": "updated 3, unmatched 1, skipped 1"}


def test_one_bad_vast_path_does_not_stop_the_rest(vast_quota_mocks, storage_resource, make_allocation):
    """The lookup validates the path; a bad one used to raise out of the loop and end the run."""
    for path in ("/projects//good", "/projects/good", "/projects/good"):
        make_allocation(storage_resource, attributes={VAST_PATH: path})

    assert vast.get_quotas_batch(storage_resource.pk, "hpc") == {"message": "updated 2, failed 1"}


def test_vast_is_asked_for_its_quotas_once_per_run(vast_quota_mocks, storage_resource, make_allocation):
    for path in ("/projects/good", "/projects/gone", "/projects/good"):
        make_allocation(storage_resource, attributes={VAST_PATH: path})

    vast.get_quotas_batch(storage_resource.pk, "hpc")

    vast_quota_mocks.assert_called_once_with("hpc")


def test_vast_being_down_fails_the_run_once(vast_quota_mocks, storage_resource, make_allocation):
    """Not once per allocation, each waiting out the client's timeout before the threshold is reached."""
    vast_quota_mocks.side_effect = ConnectionError("VAST is down")
    for path in ("/projects/a", "/projects/b", "/projects/c"):
        make_allocation(storage_resource, attributes={VAST_PATH: path})

    with pytest.raises(ConnectionError, match="VAST is down"):
        vast.get_quotas_batch(storage_resource.pk, "hpc")

    vast_quota_mocks.assert_called_once()


def test_a_path_vast_has_no_quota_for_is_unmatched_not_failed(vast_quota_mocks, storage_resource, make_allocation):
    """VAST answered; telling a wrong path apart from a VAST fault is what the counts are for."""
    for path in ("/projects/good", "/projects/good", "/projects/good", "/projects/gone", "/projects//bad"):
        make_allocation(storage_resource, attributes={VAST_PATH: path})

    assert vast.get_quotas_batch(storage_resource.pk, "hpc") == {"message": "updated 3, unmatched 1, failed 1"}


def test_a_missing_quota_is_still_a_value_error():
    """get_quota raised ValueError for this before, so anything catching that still works."""
    with pytest.raises(ValueError, match="/projects/gone"):
        vast.find_quota([vast_quota("/projects/good")], "/projects/gone")


def test_vast_quotas_fail_when_most_paths_have_no_quota(vast_quota_mocks, storage_resource, make_allocation):
    make_allocation(storage_resource, attributes={VAST_PATH: "/projects/gone"})

    with pytest.raises(BatchMostlyFailedError, match="1 of 1"):
        vast.get_quotas_batch(storage_resource.pk, "hpc")


# --- TrueNAS quotas ----------------------------------------------------------------------------


@patch("storage.truenas.update_allocation_attribute_value")
@patch("storage.truenas.get_truenas_client")
def test_truenas_quotas_count_each_allocation(mock_client, mock_update, storage_resource, make_allocation):
    mock_client.return_value.get_all_datasets.return_value = [{"mountpoint": "/mnt/t2/a", "quota": 10**12}]
    make_allocation(storage_resource, attributes={"truenas_path": "/mnt/t2/a"})
    make_allocation(storage_resource, attributes={"truenas_path": "/mnt/t2/a"})
    make_allocation(storage_resource, attributes={"truenas_path": "/mnt/t2/missing"})
    make_allocation(storage_resource)

    assert truenas.get_quotas_batch(storage_resource.pk, "truenas") == {"message": "updated 2, unmatched 1, skipped 1"}


@patch("storage.truenas.update_allocation_attribute_value")
@patch("storage.truenas.get_truenas_client")
def test_a_path_truenas_has_no_dataset_for_is_unmatched_not_failed(
    mock_client, mock_update, storage_resource, make_allocation
):
    """TrueNAS answered; a dataset it returned without a quota is the fault, a missing one isn't."""
    mock_client.return_value.get_all_datasets.return_value = [
        {"mountpoint": "/mnt/t2/a", "quota": 10**12},
        {"mountpoint": "/mnt/t2/no_quota"},
    ]
    for path in ("/mnt/t2/a", "/mnt/t2/a", "/mnt/t2/a", "/mnt/t2/missing", "/mnt/t2/no_quota"):
        make_allocation(storage_resource, attributes={"truenas_path": path})

    assert truenas.get_quotas_batch(storage_resource.pk, "truenas") == {"message": "updated 3, unmatched 1, failed 1"}


@patch("storage.truenas.update_allocation_attribute_value")
@patch("storage.truenas.get_truenas_client")
def test_a_truenas_path_with_stray_spaces_still_matches(mock_client, mock_update, storage_resource, make_allocation):
    mock_client.return_value.get_all_datasets.return_value = [{"mountpoint": "/mnt/t2/a", "quota": 10**12}]
    make_allocation(storage_resource, attributes={"truenas_path": " /mnt/t2/a \n"})

    assert truenas.get_quotas_batch(storage_resource.pk, "truenas") == {"message": "updated 1"}


# --- Starfish usage ----------------------------------------------------------------------------


@patch("storage.starfish.update_allocation_attribute_value")
@patch("storage.starfish.update_allocation_usage")
@patch("storage.starfish.get_starfish_usage_data_by_volume")
@patch("storage.starfish.get_client_config", return_value={"native_path_attribute_name": SF_PATH})
def test_a_starfish_volume_that_fails_fails_only_its_own_allocations(
    mock_config, mock_volume_data, mock_usage, mock_stamp, storage_resource, make_allocation
):
    def volume_data(volume, _):
        if volume == "down":
            raise ConnectionError("Starfish timed out")
        return [{"vol_path": f"{volume}:/lab", "logical_size": 1, "sync": 1700000000}]

    mock_volume_data.side_effect = volume_data
    make_allocation(storage_resource, attributes={SF_PATH: "up:/lab"})
    make_allocation(storage_resource, attributes={SF_PATH: "also_up:/lab"})
    make_allocation(storage_resource, attributes={SF_PATH: "down:/lab"})

    assert starfish.get_storage_usage_batch(storage_resource.pk, "sf") == {"message": "updated 2, failed 1"}


@patch("storage.starfish.update_allocation_attribute_value")
@patch("storage.starfish.update_allocation_usage")
@patch("storage.starfish.get_starfish_usage_data_by_volume", side_effect=ConnectionError("Starfish is down"))
@patch("storage.starfish.get_client_config", return_value={"native_path_attribute_name": SF_PATH})
def test_starfish_usage_fails_when_starfish_is_down(
    mock_config, mock_volume_data, mock_usage, mock_stamp, storage_resource, make_allocation
):
    make_allocation(storage_resource, attributes={SF_PATH: "vol1:/a"})
    make_allocation(storage_resource, attributes={SF_PATH: "vol2:/b"})

    with pytest.raises(BatchMostlyFailedError, match="2 of 2"):
        starfish.get_storage_usage_batch(storage_resource.pk, "sf")


# --- a misconfigured path attribute ------------------------------------------------------------


def test_vast_quotas_fail_when_the_path_attribute_type_is_missing(vast_quota_mocks, storage_resource, make_allocation):
    """Every allocation would be skipped, and a run that refreshed nothing would report success."""
    make_allocation(storage_resource)

    with pytest.raises(PathAttributeTypeMissingError, match=f"'{VAST_PATH}' does not exist"):
        vast.get_quotas_batch(storage_resource.pk, "hpc")

    vast_quota_mocks.assert_not_called()


@patch("storage.truenas.get_truenas_client")
def test_truenas_quotas_fail_when_the_path_attribute_type_is_missing(mock_client, storage_resource, make_allocation):
    make_allocation(storage_resource)

    with pytest.raises(PathAttributeTypeMissingError, match="'truenas_path' does not exist"):
        truenas.get_quotas_batch(storage_resource.pk, "truenas")

    mock_client.assert_not_called()


@patch("storage.starfish.get_starfish_usage_data_by_volume")
@patch("storage.starfish.get_client_config", return_value={"native_path_attribute_name": SF_PATH})
def test_starfish_usage_fails_when_the_path_attribute_type_is_missing(
    mock_config, mock_volume_data, storage_resource, make_allocation
):
    make_allocation(storage_resource)

    with pytest.raises(PathAttributeTypeMissingError, match=f"'{SF_PATH}' does not exist"):
        starfish.get_storage_usage_batch(storage_resource.pk, "sf")

    mock_volume_data.assert_not_called()


def test_allocations_without_a_path_are_still_only_skipped(vast_quota_mocks, storage_resource, make_allocation):
    """Pending, denied and expired allocations often have no path; that isn't a misconfiguration."""
    make_allocation(storage_resource, attributes={VAST_PATH: "/projects/good"})
    make_allocation(storage_resource)
    make_allocation(storage_resource)

    assert vast.get_quotas_batch(storage_resource.pk, "hpc") == {"message": "updated 1, skipped 2"}


# --- the batches that queue one task per resource ----------------------------------------------


@pytest.fixture
def handlers(storage_resource):
    """One resource with both batch tasks configured, and one with neither."""
    unconfigured = type(storage_resource).objects.create(
        resource_type=storage_resource.resource_type, name="tier2", description="no tasks"
    )
    StorageHandler.objects.create(
        resource=storage_resource,
        get_usage_batch_task="storage.starfish.get_storage_usage_batch",
        usage_client_id="sf",
        get_quotas_batch_task="storage.vast.get_quotas_batch",
        quota_client_id="hpc",
    )
    StorageHandler.objects.create(resource=unconfigured)


@pytest.mark.parametrize("dispatcher", [tasks.get_storage_usage_batch, tasks.get_storage_quotas_batch])
@patch("storage.tasks.async_task")
def test_a_dispatcher_reports_what_it_queued(mock_async, dispatcher, handlers):
    with patch("storage.tasks.STORAGE_LOG_ONLY", False):
        result = dispatcher()

    assert result == {"message": "queued 1: hpc-storage; no task configured for tier2"}
    mock_async.assert_called_once()


@patch("storage.tasks.async_task")
def test_a_dispatcher_in_log_only_mode_says_nothing_was_queued(mock_async, handlers):
    with patch("storage.tasks.STORAGE_LOG_ONLY", True):
        result = tasks.get_storage_quotas_batch()

    assert result == {"message": "would have queued (STORAGE_LOG_ONLY) 1: hpc-storage; no task configured for tier2"}
    mock_async.assert_not_called()
