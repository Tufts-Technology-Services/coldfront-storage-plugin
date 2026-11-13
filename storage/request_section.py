"""The storage_quota section of ColdFront's allocation request form (coldfront_utils.allocation_request).

A request for a Storage resource asks for the quota on the request form itself, unless the resource's
quota_on_request resource attribute is No, and the quota is saved with the allocation, before ColdFront sends its admin
email and allocation_new.
"""

import logging
from decimal import Decimal, InvalidOperation

from coldfront_utils.allocation_request import RequestSection, RequestSectionForm
from django import forms
from django.core.exceptions import ValidationError

from coldfront.core.allocation.models import AllocationAttributeType

from .constants import (
    QUOTA_ATTRIBUTE_NAME,
    QUOTA_ON_REQUEST_ATTRIBUTE_NAME,
    STORAGE_PLUGIN_STORAGE_UNITS,
    STORAGE_RESOURCE_TYPE_NAME,
)

logger = logging.getLogger(__name__)

QUOTA_SECTION_NAME = "storage_quota"


def quota_attribute_type():
    """The allocation attribute type the quota is saved as: the first named QUOTA_ATTRIBUTE_NAME, or None.
    The first, so a hand-made duplicate (which check storage.W002 reports) doesn't stop requests."""
    return AllocationAttributeType.objects.filter(name=QUOTA_ATTRIBUTE_NAME).order_by("pk").first()


class QuotaField(forms.DecimalField):
    """A DecimalField that reads the number in its shortest form before validating: "2.50" is 2.5, so it
    counts as one decimal place, and "1e3" is 1000."""

    def to_python(self, value):
        value = super().to_python(value)
        if value is None or not value.is_finite():
            return value  # DecimalField's own validation refuses NaN and infinity
        if value == value.to_integral_value():
            try:
                return value.quantize(Decimal(1))
            except InvalidOperation:
                # too many digits to write out (1e30): left as it is, for max_digits to refuse
                return value
        return value.normalize()


class StorageQuotaForm(RequestSectionForm):
    """The quota asked for, in the plugin's units: tenths, from 0.1 to 999999.9."""

    quota = QuotaField(
        label=QUOTA_ATTRIBUTE_NAME,
        min_value=Decimal("0.1"),
        max_digits=7,
        decimal_places=1,
        widget=forms.NumberInput(attrs={"step": "0.1", "min": "0.1"}),
        help_text=f"The storage quota in {STORAGE_PLUGIN_STORAGE_UNITS} (minimum 0.1).",
    )

    def clean(self):
        cleaned = super().clean()
        # checked here as well as looked up in save(): a missing type refuses the request instead of a
        # server error
        if quota_attribute_type() is None:
            logger.error(
                "No allocation attribute type is named %r, so storage requests that ask for a quota can't be "
                "saved (check storage.W002)",
                QUOTA_ATTRIBUTE_NAME,
            )
            raise ValidationError(
                "This request can't be saved: the storage quota isn't set up. Please contact support."
            )
        return cleaned


class StorageQuotaSection(RequestSection):
    """Asks for the quota of a Storage resource, unless its quota_on_request resource attribute is No."""

    name = QUOTA_SECTION_NAME
    order = 10
    form_class = StorageQuotaForm
    template = "storage/allocation_request/quota.html"

    def applies(self):
        """A Storage resource, unless its quota_on_request attribute is No. Asking is the default, as the
        storage tasks need a quota: a resource opts out explicitly, and its allocations then get a quota
        when an admin sets one."""
        return (
            self.resource.resource_type.name == STORAGE_RESOURCE_TYPE_NAME
            and self.resource.get_attribute(QUOTA_ON_REQUEST_ATTRIBUTE_NAME, expand=False, typed=False) != "No"
        )

    def save(self, allocation, form):
        """Store the quota as the QUOTA_ATTRIBUTE_NAME allocation attribute, in plain notation ("1000", not
        "1E+3"). The type is looked up here, not taken from the form, so a form whose clean() doesn't call
        StorageQuotaForm's still saves; StorageQuotaForm.clean() checks the type exists."""
        allocation.allocationattribute_set.create(
            allocation_attribute_type=quota_attribute_type(),
            value=format(form.cleaned_data["quota"], "f"),
        )
