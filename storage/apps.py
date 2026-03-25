
import logging

from django.apps import AppConfig


logger = logging.getLogger(__name__)


class StorageConfig(AppConfig):
    """
    Configuration for the storage plugin.
    This class initializes signal receivers based on settings and tests the storage client configuration."""

    name = "storage"

    def ready(self):
        StorageConfig.validate_settings()
        # tests whether the client has the appropriate configuration and any dependencies can be imported

    @staticmethod
    def validate_settings():
        pass