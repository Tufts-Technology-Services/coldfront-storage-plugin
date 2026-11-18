"""System checks for the storage plugin."""

from coldfront_utils.attribute.checks import attribute_type_warnings
from django.apps import apps
from django.conf import settings
from django.core import checks

from coldfront.core.allocation.models import AllocationAttributeType
from coldfront.core.resource.models import ResourceAttributeType

from .constants import QUOTA_ATTRIBUTE_NAME, QUOTA_ON_REQUEST_ATTRIBUTE_NAME


@checks.register()
def check_allocation_request_app(app_configs, **kwargs):
    """E001: the storage_quota request section is registered, but coldfront_utils.allocation_request isn't
    installed, so no request page would ask for the quota."""
    if apps.is_installed("coldfront_utils.allocation_request"):
        return []
    return [
        checks.Error(
            "The storage plugin asks for a Storage request's quota on the allocation request form, but "
            "coldfront_utils.allocation_request isn't installed, so requests would have no quota.",
            hint='Add "coldfront_utils.allocation_request" to INSTALLED_APPS.',
            id="storage.E001",
        )
    ]


@checks.register(checks.Tags.database)
def check_quota_types(app_configs, databases=None, **kwargs):
    """W001: no resource attribute type is named QUOTA_ON_REQUEST_ATTRIBUTE_NAME, so no resource can be set
    not to ask for a quota (and one set before a rename asks again). W002: no allocation attribute type, or more than one, is named QUOTA_ATTRIBUTE_NAME, so requests
    that ask are refused. Migration 0004 makes both; these catch a setting renamed or a type deleted
    later. A database check, so `migrate` and `check --database` run it."""
    return attribute_type_warnings(
        ResourceAttributeType,
        [QUOTA_ON_REQUEST_ATTRIBUTE_NAME],
        databases=databases,
        check_id="storage.W001",
        consequence="no resource can be set not to ask for a quota on the allocation request form",
    ) + attribute_type_warnings(
        AllocationAttributeType,
        [QUOTA_ATTRIBUTE_NAME],
        databases=databases,
        check_id="storage.W002",
        consequence="allocation requests that ask for a quota are refused",
    )


@checks.register()
def check_user_storage_areas(app_configs, **kwargs):
    """E002 for each way USER_STORAGE_AREAS is malformed, if it's set. Unset, there are no storage
    areas."""
    areas = getattr(settings, "USER_STORAGE_AREAS", None)
    if areas is None:
        return []
    if not isinstance(areas, dict):
        return [user_storage_area_error("USER_STORAGE_AREAS must be a dict of areas.")]
    client_keys = {entry.get("client_key") for entry in getattr(settings, "STORAGE_PLUGIN_CLIENTS", None) or []}
    errors = []
    for name, area in areas.items():
        if not name or "/" in str(name):
            # the name is a URL segment: storage/<area>/
            errors.append(user_storage_area_error(f"The storage area name {name!r} must be non-empty, with no slash."))
        elif not isinstance(area, dict):
            errors.append(
                user_storage_area_error(f"USER_STORAGE_AREAS[{name!r}] must be a dict with a client and a path.")
            )
        elif not area.get("client"):
            errors.append(user_storage_area_error(f"USER_STORAGE_AREAS[{name!r}] names no client."))
        elif not str(area.get("path") or "").startswith("/"):
            errors.append(
                user_storage_area_error(
                    f"USER_STORAGE_AREAS[{name!r}] needs an absolute path, not {area.get('path')!r}."
                )
            )
        elif area["client"] not in client_keys:
            errors.append(
                user_storage_area_error(
                    f"USER_STORAGE_AREAS[{name!r}] names client {area['client']!r}, which isn't in "
                    "STORAGE_PLUGIN_CLIENTS."
                )
            )
    return errors


def user_storage_area_error(message):
    return checks.Error(
        message,
        hint=(
            "USER_STORAGE_AREAS maps each area to the STORAGE_PLUGIN_CLIENTS entry it lives on and its "
            'directory quota there: {"home": {"client": "hpc", "path": "/homedir"}}.'
        ),
        id="storage.E002",
    )


# the kinds of system a STORAGE_PLUGIN_CLIENTS entry can be; the usage extension reads it
STORAGE_CLIENT_TYPES = ("vast", "truenas", "starfish")


@checks.register()
def check_client_types(app_configs, **kwargs):
    """W003 for each STORAGE_PLUGIN_CLIENTS entry whose "type" isn't one the plugin knows. Only the
    usage extension needs it, so the rest of the plugin works without: a warning, not an error."""
    return [
        checks.Warning(
            f"STORAGE_PLUGIN_CLIENTS entry {entry.get('client_key')!r} has type {entry.get('type')!r}, "
            f"not one of {', '.join(STORAGE_CLIENT_TYPES)}.",
            hint=(
                'Add "type" to the entry. Until then, /api/v2/allocations/<id>/usage/ answers 503 for '
                "allocations on it."
            ),
            id="storage.W003",
        )
        for entry in getattr(settings, "STORAGE_PLUGIN_CLIENTS", None) or []
        if entry.get("type") not in STORAGE_CLIENT_TYPES
    ]
