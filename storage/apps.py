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

        if not apps.is_installed("allocation_blueprint") and ENABLE_ATTRIBUTES_ON_NEW_ALLOCATION:
            storage.signals.enable_add_attributes()

        # The quota is asked for on ColdFront's own request form, as a section of
        # coldfront_utils.allocation_request, which must be installed too (check E001)
        from coldfront_utils.allocation_request import register_request_section

        from . import checks  # noqa: F401 -- registers them
        from .request_section import StorageQuotaSection

        register_request_section(StorageQuotaSection)

        import coldfront.core.allocation.views as allocation_views

        from storage.views import AllocationAttributeEditView

        allocation_views.AllocationAttributeEditView = AllocationAttributeEditView

    @staticmethod
    def validate_settings():
        pass
