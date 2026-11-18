"""
One storage allocation's quota and usage, for the REST API's usage extension (storage.api_extension):
live from VAST for an allocation on a VAST client, ColdFront's stored values for one on TrueNAS. With
its Storage Path, the path people use, but never its path on the storage system.

Where an allocation lives: its resource's StorageHandler names a STORAGE_PLUGIN_CLIENTS entry
(quota_client_id), whose "type" says which kind of system it is and whose native_path_attribute_name
names the allocation attribute holding its path there. See "GET /api/v2/allocations/<id>/usage/" in
coldfront-rest-api-plugin's docs/rest-api-plan.md.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from coldfront_utils import ttl_cache, validate_posix_path
from django.utils import timezone

from storage.constants import (
    CLUSTER_PATH_ATTRIBUTE_NAME,
    QUOTA_IN_BYTES_ATTRIBUTE_NAME,
    QUOTA_REPORT_DATE_ATTRIBUTE_NAME,
    STORAGE_LOG_ONLY,
    USAGE_IN_BYTES_ATTRIBUTE_NAME,
    USAGE_REPORT_DATE_ATTRIBUTE_NAME,
)
from storage.models import StorageHandler
from storage.user_storage import StorageLookupsOff, StorageUnavailable, vast_client_for
from storage.utils import get_client_config

logger = logging.getLogger(__name__)

# VAST keeps a directory quota's usage current itself; this only spares it a dashboard refreshing
ANSWER_SECONDS = 2 * 60


@dataclass
class StorageUsage:
    """
    What the usage extension reports, whatever the source: the same keys from VAST and from
    ColdFront's stored values, null where a source has nothing.

    limit_bytes is the one limit people are told: the soft limit if there is one, else the hard one
    (see limit_of()). measured_at is when used_bytes was measured, limit_reported_at when the limit
    was read. stored_used_bytes is ColdFront's own figure, the allocation's reported_usage_bytes from
    the Starfish task, as on its allocation page; for stored answers it is used_bytes again.
    """

    limit_bytes: int | None = None
    used_bytes: int | None = None
    measured_at: str | None = None
    limit_reported_at: str | None = None
    stored_used_bytes: int | None = None
    stored_usage_reported_at: str | None = None

    @property
    def state(self):
        """ "ok", "over_limit", or None when there's no limit or no usage to compare."""
        if self.limit_bytes is None or self.used_bytes is None:
            return None
        return "over_limit" if self.used_bytes > self.limit_bytes else "ok"

    def as_dict(self) -> dict:
        return {
            "limit_bytes": self.limit_bytes,
            "used_bytes": self.used_bytes,
            "state": self.state,
            "measured_at": self.measured_at,
            "limit_reported_at": self.limit_reported_at,
            "stored_used_bytes": self.stored_used_bytes,
            "stored_usage_reported_at": self.stored_usage_reported_at,
        }


def limit_of(soft, hard):
    """The soft limit if there is one, else the hard one; None if neither. 0 is no limit: VAST's batch
    task and TrueNAS (ZFS) both record no quota as 0."""
    return soft or hard or None


class NoUsage(Exception):
    """Nothing to report: not a storage allocation, or one with no path or no quota on its system."""


def lookup_allocation_usage(allocation, caller) -> dict:
    """
    The allocation's quota and usage, and its Storage Path as caller may see it, without the
    extension's name and version.
    :raises NoUsage: not a storage allocation, or no path or quota for it
    :raises StorageLookupsOff: a VAST allocation, and STORAGE_LOG_ONLY is on; nothing was asked
    :raises StorageUnavailable: VAST failed, or the client isn't set up (no such entry, or no type)
    """
    handler = (
        StorageHandler.objects.filter(resource__in=allocation.resources.all())
        .select_related("resource")
        .order_by("resource__pk")
        .first()
    )
    if handler is None:
        raise NoUsage("no storage handler for its resources")
    client_key = handler.quota_client_id
    try:
        client_config = get_client_config(client_key)
    except ValueError as error:
        raise StorageUnavailable(str(error)) from error

    answer = {
        "allocation": allocation.pk,
        "project": {"id": allocation.project.pk, "title": allocation.project.title},
        "resource": handler.resource.name,
        "path": visible_storage_path(allocation, caller),
    }
    client_type = client_config.get("type")
    if client_type == "vast":
        return {**answer, **_vast_usage(allocation, client_key, client_config).as_dict()}
    if client_type == "truenas":
        return {**answer, **_stored_usage(allocation).as_dict()}
    raise StorageUnavailable(f"storage client {client_key!r} has type {client_type!r}, not vast or truenas")


def _vast_usage(allocation, client_key, client_config) -> StorageUsage:
    # here, not at the top: vast-api-client comes with the plugin's vast extra
    from vast_api_client.exceptions import VastError

    path = stored_value(allocation, client_config.get("native_path_attribute_name"))
    if not path:
        raise NoUsage("no path attribute")
    if STORAGE_LOG_ONLY:
        logger.info("STORAGE_LOG_ONLY: not looking up allocation %s's usage on %s", allocation.pk, client_key)
        raise StorageLookupsOff
    try:
        validate_posix_path(path)
    except ValueError as error:
        raise NoUsage(f"unusable path: {error}") from error
    try:
        quota = _directory_quota(client_key, path)
    except VastError as error:
        raise StorageUnavailable(f"VAST {client_key}: {error}") from error
    if quota is None:
        raise NoUsage("no quota at its path")
    return StorageUsage(
        limit_bytes=quota["limit_bytes"],
        used_bytes=quota["used_bytes"],
        # both live, read in the one call
        measured_at=quota["measured_at"],
        limit_reported_at=quota["measured_at"],
        # what ColdFront shows: Starfish's once-a-day figure, which the live one doesn't replace
        stored_used_bytes=stored_bytes(allocation, USAGE_IN_BYTES_ATTRIBUTE_NAME),
        stored_usage_reported_at=stored_date(allocation, USAGE_REPORT_DATE_ATTRIBUTE_NAME),
    )


@ttl_cache(timeout=ANSWER_SECONDS)
def _directory_quota(client_key, path):
    """
    The limit and usage of the directory quota at exactly path, and when VAST was asked; None if
    there's none there. Usage is used_capacity, as for people's homes (vast-api-client's
    docs/vast-usage-findings.md). :raises StorageUnavailable: more than one quota at path
    """
    quotas = vast_client_for(client_key).get_quotas(path=Path(path)) or []
    matching = [quota for quota in quotas if quota.get("path") == path]
    if not matching:
        return None
    if len(matching) > 1:
        raise StorageUnavailable(f"{len(matching)} quotas at one path on {client_key}")
    quota = matching[0]
    return {
        "limit_bytes": limit_of(quota.get("soft_limit"), quota.get("hard_limit")),
        "used_bytes": quota.get("used_capacity"),
        # a directory quota carries no time of its own; VAST keeps its usage current
        "measured_at": timezone.now().isoformat(),
    }


def _stored_usage(allocation) -> StorageUsage:
    """ColdFront's stored values: the quota from the TrueNAS batch task, the usage from Starfish's."""
    used = stored_bytes(allocation, USAGE_IN_BYTES_ATTRIBUTE_NAME)
    usage_date = stored_date(allocation, USAGE_REPORT_DATE_ATTRIBUTE_NAME)
    return StorageUsage(
        # TrueNAS records one quota, which the batch task stores
        limit_bytes=limit_of(None, stored_bytes(allocation, QUOTA_IN_BYTES_ATTRIBUTE_NAME)),
        used_bytes=used,
        measured_at=usage_date,
        limit_reported_at=stored_date(allocation, QUOTA_REPORT_DATE_ATTRIBUTE_NAME),
        stored_used_bytes=used,
        stored_usage_reported_at=usage_date,
    )


def visible_storage_path(allocation, caller):
    """
    The allocation's Storage Path (CLUSTER_PATH_ATTRIBUTE_NAME), the path people use; None if it has
    none, or if the attribute type is private and caller isn't a superuser, ColdFront's rule for
    private attributes and the API's. Never the path on the storage system itself.
    """
    attribute = (
        allocation.allocationattribute_set.filter(allocation_attribute_type__name=CLUSTER_PATH_ATTRIBUTE_NAME)
        .select_related("allocation_attribute_type")
        .first()
    )
    if attribute is None or (attribute.allocation_attribute_type.is_private and not caller.is_superuser):
        return None
    return attribute.value.strip() or None


def stored_value(allocation, attribute_name):
    """The allocation's value for the named attribute, stripped; None if it has none."""
    if not attribute_name:
        return None
    attribute = allocation.allocationattribute_set.filter(allocation_attribute_type__name=attribute_name).first()
    return attribute.value.strip() if attribute is not None else None


def stored_bytes(allocation, attribute_name):
    """A stored byte count as an int; None if missing or not a number."""
    value = stored_value(allocation, attribute_name)
    try:
        return int(Decimal(value))
    except (ArithmeticError, TypeError, ValueError):
        return None


def stored_date(allocation, attribute_name):
    """
    A stored report date in ISO 8601 with its offset; None if missing or unreadable. The tasks store
    local time with no zone (datetime.now().isoformat()), so a date without one is taken as the
    site's TIME_ZONE.
    """
    value = stored_value(allocation, attribute_name)
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    return moment.isoformat()
