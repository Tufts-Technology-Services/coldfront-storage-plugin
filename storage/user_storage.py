"""
A person's own storage areas (home, scratch): their user quota and usage in each, from VAST.

The areas are the USER_STORAGE_AREAS setting, mapping each area's name to the STORAGE_PLUGIN_CLIENTS
entry it lives on and the path of its directory quota there: {"home": {"client": "hpc", "path":
"/homedir"}}. Served through the REST API by the storage extension (storage.api_extension).

What VAST gives, checked on the hpc VMS (vast-api-client's docs/vast-usage-findings.md): each person
has at most one user entry in an area's quota, found with the entity__identifier filter, recorded
by username or, for some people, by UID. VAST recalculates their usage every minute or two.
"""

import logging
from pathlib import Path

from coldfront_utils import get_uid, ttl_cache
from django.conf import settings

from storage.constants import STORAGE_LOG_ONLY
from storage.vast.vast import get_vast_client

logger = logging.getLogger(__name__)

# an area's quota id changes only if someone recreates the quota
AREA_QUOTA_SECONDS = 60 * 60
# VAST recalculates user usage every minute or two, so a fresher answer isn't to be had
ANSWER_SECONDS = 2 * 60


class StorageLookupsOff(Exception):
    """STORAGE_LOG_ONLY is on, so nothing is asked of the storage systems."""


class StorageUnavailable(Exception):
    """The storage system couldn't answer, or isn't set up to. The message is for logs, not callers."""


def configured_areas() -> dict:
    """USER_STORAGE_AREAS, in its order; none if it isn't set."""
    return dict(getattr(settings, "USER_STORAGE_AREAS", None) or {})


def vast_client_for(client_key):
    """A VASTClient for the STORAGE_PLUGIN_CLIENTS entry with this client_key, as the plugin's tasks
    build one. :raises StorageUnavailable: no such entry"""
    try:
        return get_vast_client(client_key)
    except ValueError as error:
        raise StorageUnavailable(str(error)) from error


def uid_of(username):
    """The person's UID, from getent on this host; None if it doesn't know them."""
    try:
        return get_uid(username)
    except ValueError:
        return None


def lookup_user_storage(area_name, username) -> dict:
    """
    A person's quota and usage in one configured area.
    :raises KeyError: the area isn't configured
    :raises StorageLookupsOff: STORAGE_LOG_ONLY is on; nothing was asked
    :raises StorageUnavailable: VAST failed, or the area isn't set up on it
    """
    # here, not at the top: vast-api-client comes with the plugin's vast extra
    from vast_api_client.exceptions import VastError

    area = configured_areas()[area_name]
    if STORAGE_LOG_ONLY:
        logger.info("STORAGE_LOG_ONLY: not looking up %s in %s (%s)", username, area_name, area["path"])
        raise StorageLookupsOff
    try:
        return _user_storage(area["client"], area["path"], area_name, username)
    except VastError as error:
        raise StorageUnavailable(f"looking up {username} in {area_name}: {error}") from error


@ttl_cache(timeout=AREA_QUOTA_SECONDS)
def _area_quota_id(client_key, path) -> int:
    """The id of the directory quota at exactly this path. :raises StorageUnavailable: not exactly one"""
    quotas = vast_client_for(client_key).get_quotas(path=Path(path)) or []
    matching = [quota for quota in quotas if quota.get("path") == path]
    if len(matching) != 1:
        raise StorageUnavailable(f"{len(matching)} quotas at {path} on {client_key}, not one")
    return matching[0]["id"]


def _own_entry(entries, system_id, identifier_type, identifier):
    """The person's own entry: in this quota, a user's (not a group's of the same name), by this identifier."""
    for entry in entries or []:
        entity = entry.get("entity") or {}
        if (
            entry.get("quota_system_id") == system_id
            and not entity.get("is_group")
            and entity.get("identifier_type") == identifier_type
            and str(entity.get("identifier")) == identifier
        ):
            return entry
    return None


@ttl_cache(timeout=ANSWER_SECONDS)
def _user_storage(client_key, path, area_name, username) -> dict:
    vast = vast_client_for(client_key)
    quota = vast.get_quota(_area_quota_id(client_key, path))
    system_id = quota.get("system_id")
    default = quota.get("default_user_quota") or {}

    # by username; then, for someone VAST records by UID, by UID
    entry = _own_entry(vast.get_user_quotas(path=Path(path), identifier=username), system_id, "username", username)
    if entry is None:
        uid = uid_of(username)
        if uid is not None:
            entry = _own_entry(vast.get_user_quotas(path=Path(path), identifier=uid), system_id, "uid", str(uid))

    # someone with no entry (who has never owned a file there) has the default limits and has used nothing
    limits = entry if entry is not None else default
    soft, hard = limits.get("soft_limit"), limits.get("hard_limit")
    is_default = (soft, hard) == (default.get("soft_limit"), default.get("hard_limit"))
    return {
        "username": username,
        "area": area_name,
        "path": path,
        "scope": "user",
        "quota_source": "default" if is_default else "override",
        "soft_limit_bytes": soft,
        "hard_limit_bytes": hard,
        "used_bytes": entry.get("used_capacity") if entry is not None else 0,
        "used_inodes": entry.get("used_inodes") if entry is not None else 0,
        "state": entry.get("state") if entry is not None else None,
        # when VAST last recalculated user usage in this quota: how fresh used_bytes really is
        "retrieved_at": quota.get("last_user_quotas_update"),
    }
