import logging

from django.apps import AppConfig, apps
from .constants import ENABLE_ATTRIBUTES_ON_NEW_ALLOCATION

logger = logging.getLogger(__name__)


class StorageConfig(AppConfig):
    """
    Configuration for the storage plugin.
    This class initializes signal receivers based on settings and tests the storage client configuration."""

    name = "storage"

    def ready(self):
        StorageConfig.validate_settings()
        # tests whether the client has the appropriate configuration and any dependencies can be imported
        import storage.signals

        if not apps.is_installed('allocation_blueprints') and ENABLE_ATTRIBUTES_ON_NEW_ALLOCATION:
            storage.signals.enable_add_attributes()
        # Override allocation create view so users specify quota when requesting storage
        import coldfront.core.allocation.views as allocation_views

        from storage.views import AllocationCreateView, AllocationAttributeEditView

        allocation_views.AllocationCreateView = AllocationCreateView
        allocation_views.AllocationAttributeEditView = AllocationAttributeEditView

    @staticmethod
    def validate_settings():
        pass
