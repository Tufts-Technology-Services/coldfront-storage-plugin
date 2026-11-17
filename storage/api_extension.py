"""
The storage extension of the REST API: a person's home and scratch, at
/api/v2/users/<username>/storage/ and, for one area, /api/v2/users/<username>/storage/<area>/.

Registered with coldfront_utils.api_extensions in StorageConfig.ready(); the API (coldfront-rest-api-
plugin) serves it without importing this plugin. See "Plugin extensions" in that plugin's
docs/rest-api-plan.md.
"""

import logging

from coldfront_utils.api_extensions import ExtensionNotFound, ExtensionUnavailable, PersonExtension

from storage.user_storage import StorageLookupsOff, StorageUnavailable, configured_areas, lookup_user_storage

logger = logging.getLogger(__name__)

# the storage plugin's own permission, for service accounts: ColdFront has none covering home folders
VIEW_ANY_STORAGE = "storage.view_any_storage"
LOOKUPS_OFF = "Storage lookups are off on this host."
# VAST's own error text names the VMS, so callers get this instead; the log has the rest
UNAVAILABLE = "The storage system didn't answer. Try again later."


class StorageAreas(PersonExtension):
    """A person's storage areas by key, and their quota and usage in each."""

    name = "storage"
    description = "A person's home and scratch: their quota and usage in each, from the storage system"
    version = 1

    def allows(self, target, caller):
        """The person themselves, superusers, and holders of storage.view_any_storage."""
        return caller == target or caller.is_superuser or caller.has_perm(VIEW_ANY_STORAGE)

    def summary(self, target, caller, params):
        """The configured areas, by key, in settings order. Asks no storage system."""
        return {
            "extension": self.name,
            "version": self.version,
            "username": target.username,
            "areas": [{"key": area_name} for area_name in configured_areas()],
        }

    def item(self, target, key, caller, params):
        """The person's quota and usage in the area named key."""
        if key not in configured_areas():
            raise ExtensionNotFound(key)
        try:
            answer = lookup_user_storage(key, target.username)
        except StorageLookupsOff:
            raise ExtensionUnavailable(LOOKUPS_OFF) from None
        except StorageUnavailable as error:
            logger.error("Storage lookup failed: %s", error)
            raise ExtensionUnavailable(UNAVAILABLE) from None
        return {"extension": self.name, "version": self.version, **answer}
