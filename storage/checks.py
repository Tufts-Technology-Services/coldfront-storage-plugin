"""System checks for the storage plugin."""

from coldfront_utils.attribute.checks import attribute_type_warnings
from django.apps import apps
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
