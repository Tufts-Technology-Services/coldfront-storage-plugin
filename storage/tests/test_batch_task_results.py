"""Batch tasks report counts on the task report, and fail only when most of a run went wrong.

One allocation failing is in the counts, not a failed task: a batch covers many allocations, and a
run that refreshed all but one of them worked.
"""

from unittest.mock import patch

import pytest

from storage import starfish, tasks, truenas
from storage.models import StorageHandler
from storage.utils import BatchMostlyFailedError, BatchTally
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


@pytest.fixture
def vast_quota_mocks():
    with (
        patch("storage.vast.vast.get_client_config", return_value={"native_path_attribute_name": VAST_PATH}),
        patch("storage.vast.vast.update_allocation_attribute_value"),
        patch("storage.vast.vast.get_quota") as get_quota,
    ):
        yield get_quota


def test_vast_quotas_count_each_allocation(vast_quota_mocks, storage_resource, make_allocation):
    def quota_for(path, _client_config_id):
        if path != "/projects/good":
            raise ValueError(f"No matching quota found for {path}")
        return {"soft_limit": 10**12}

    vast_quota_mocks.side_effect = quota_for
    for path in ("/projects/good", "/projects/good", "/projects/good", "/projects/gone"):
        make_allocation(storage_resource, attributes={VAST_PATH: path})
    make_allocation(storage_resource)

    assert vast.get_quotas_batch(storage_resource.pk, "hpc") == {"message": "updated 3, skipped 1, failed 1"}


def test_one_bad_vast_path_does_not_stop_the_rest(vast_quota_mocks, storage_resource, make_allocation):
    """get_quota validates the path; a bad one used to raise out of the loop and end the run."""
    vast_quota_mocks.side_effect = [ValueError("not a posix path"), {"soft_limit": 1}, {"soft_limit": 1}]
    for path in ("bad path", "/projects/a", "/projects/b"):
        make_allocation(storage_resource, attributes={VAST_PATH: path})

    assert vast.get_quotas_batch(storage_resource.pk, "hpc") == {"message": "updated 2, failed 1"}


def test_vast_quotas_fail_when_most_lookups_fail(vast_quota_mocks, storage_resource, make_allocation):
    vast_quota_mocks.side_effect = ValueError("VAST is down")
    make_allocation(storage_resource, attributes={VAST_PATH: "/projects/a"})

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

    assert truenas.get_quotas_batch(storage_resource.pk, "truenas") == {"message": "updated 2, skipped 1, failed 1"}


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
