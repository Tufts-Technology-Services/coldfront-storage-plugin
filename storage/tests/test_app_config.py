"""StorageConfig.ready() leaves new allocations' attributes to allocation_blueprint when it's installed.

With STORAGE_ENABLE_ATTRIBUTES_ON_NEW_ALLOCATION on, storage adds its attributes to each new storage
allocation itself, unless the allocation_blueprint plugin is installed to do that instead.
"""

from unittest.mock import patch

from django.apps import apps


def run_ready_with(*, installed_apps):
    """Run storage's ready() with the setting on and only `installed_apps` reported installed.

    Gives the mock standing in for enable_add_attributes, so a test can check whether it was called.
    """
    with (
        patch("storage.apps.ENABLE_ATTRIBUTES_ON_NEW_ALLOCATION", True),
        patch.object(apps, "is_installed", side_effect=lambda name: name in installed_apps),
        patch("storage.signals.enable_add_attributes") as enable_add_attributes,
    ):
        apps.get_app_config("storage").ready()
    return enable_add_attributes


def test_storage_adds_the_attributes_without_allocation_blueprint():
    enable_add_attributes = run_ready_with(installed_apps={"storage"})

    enable_add_attributes.assert_called_once_with()


def test_storage_leaves_the_attributes_to_allocation_blueprint():
    enable_add_attributes = run_ready_with(installed_apps={"storage", "allocation_blueprint"})

    enable_add_attributes.assert_not_called()
