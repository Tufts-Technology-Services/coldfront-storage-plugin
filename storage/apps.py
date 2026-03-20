
import logging

from django.apps import AppConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class StorageConfig(AppConfig):
    """
    Configuration for the storage plugin.
    This class initializes signal receivers based on settings and tests the storage client configuration."""

    name = "storage"

    def ready(self):
        StorageConfig.validate_settings()
        # tests whether the client has the appropriate configuration and any dependencies can be imported
        logger.debug("Testing StorageClient configuration...")
        # pylint: disable=import-outside-toplevel
        from user_management.utils import get_client_class

        get_client_class().test_config()

        if settings.USER_MANAGEMENT_ENABLE_SIGNALS:
            logger.info("Initializing User Management Plugin signal receivers...")
            # pylint: disable=import-outside-toplevel
            from user_management.signals import init_signal_receivers

            # default is to manage group membership at the allocation level
            init_signal_receivers(
                settings.MANAGE_GROUPS_AT_PROJECT_LEVEL, settings.USER_MANAGEMENT_REMOVE_USERS_ON_PROJECT_ARCHIVE
            )
        else:
            logger.warning(
                "User Management Plugin signal receivers are disabled. No users will be added or removed from groups automatically."
            )