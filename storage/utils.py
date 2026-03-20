from datetime import datetime
import logging
import re
from django.core.exceptions import ValidationError
from coldfront.coldfront.config.env import ENV
from coldfront.coldfront.plugins.storage.tasks import QUOTA_DISPLAY_ATTRIBUTE_NAME, USAGE_IN_BYTES_ATTRIBUTE_NAME, USAGE_REPORT_DATE_ATTRIBUTE_NAME
from coldfront.core.allocation.models import AllocationAttribute, AllocationAttributeType

logger = logging.getLogger(__name__)



def bytes_to_units(value, units=ENV.str("STORAGE_PLUGIN_STORAGE_UNITS", default="TB")):
    units = units.lower().strip()
    if units == "tb":
        return value / (1000 ** 4)
    elif units == "gb":
        return value / (1000 ** 3)
    elif units == "tib":
        return value / (1024 ** 4)
    elif units == "gib":
        return value / (1024 ** 3)
    else:
        raise ValueError(f"Unrecognized storage unit '{units}'")


def units_to_bytes(value, units=ENV.str("STORAGE_PLUGIN_STORAGE_UNITS", default="TB")):
    units = units.lower().strip()
    if units == "tb":
        return value * (1000 ** 4)
    elif units == "gb":
        return value * (1000 ** 3)
    elif units == "tib":
        return value * (1024 ** 4)
    elif units == "gib":
        return value * (1024 ** 3)
    else:
        raise ValueError(f"Unrecognized storage unit '{units}'")


def get_client_config(client_id):
    client_config = ENV.dict("STORAGE_PLUGIN_CLIENTS").get(client_id)
    if not client_config:
        raise ValueError(f"No configuration found for storage plugin client with id '{client_id}'")
    return client_config


def validate_posix_path(path):
    # add any path validation logic here if needed, for example to ensure the path is in a valid format for the storage system or does not contain any invalid characters. For now we will just return the path as is.
    if not re.fullmatch(r'^(/)?([^/\0]+(/)?)+$', path):
        raise ValidationError(f"Invalid path '{path}'. Path must be a valid Unix-style path and cannot contain null characters.")


def update_allocation_usage(allocation, new_usage_bytes, report_date=None):
    """
    Task to update storage usage for an allocation. This is intended to be called when an allocation is created or updated for a resource, in order to ensure that the storage usage for the allocation is updated in the storage system accordingly.
    """
    update_allocation_attribute_value(allocation, USAGE_IN_BYTES_ATTRIBUTE_NAME, new_usage_bytes)
    update_allocation_attribute_usage(allocation, QUOTA_DISPLAY_ATTRIBUTE_NAME, round(float(bytes_to_units(new_usage_bytes)), 2))
    if report_date:
        update_allocation_attribute_value(allocation, USAGE_REPORT_DATE_ATTRIBUTE_NAME, report_date.isoformat())
    else:
        update_allocation_attribute_value(allocation, USAGE_REPORT_DATE_ATTRIBUTE_NAME, datetime.now().isoformat())

        
def update_allocation_attribute_usage(allocation, attribute_name, usage_value: float):
    """Task to update the usage value of an allocation attribute for a specific allocation"""

    if not isinstance(usage_value, float):
        raise ValidationError('Provided usage value is not a float')
    attribute = allocation.allocationattribute_set.filter(allocation_attribute_type__name=attribute_name).first()
    if attribute:
        if attribute.allocation_attribute_type.has_usage:
            allocation.set_usage(attribute_name, usage_value)
            logger.info(f"Updated usage for {attribute_name} of allocation {allocation} to {usage_value}")
        else:
            logger.warning(f"{attribute_name} attribute for allocation {allocation} does not have usage enabled. Cannot update usage.")
    else:
        logger.warning(f"No {attribute_name} attribute found for allocation {allocation}. Creating new attribute.")
        attr, _ = AllocationAttribute.objects.get_or_create(
            allocation=allocation,
            allocation_attribute_type=AllocationAttributeType.objects.get(name=attribute_name),
            value=usage_value # setting to usage_value for now, but this should be set to the actual value for the attribute when that information is available
        )
        if attr.allocation_attribute_type.has_usage:
            allocation.set_usage(attribute_name, usage_value)
            logger.info(f"Created {attribute_name} attribute and set usage for allocation {allocation} to {usage_value}")
        else:
            logger.warning(f"{attribute_name} attribute for allocation {allocation} does not have usage enabled. Cannot set usage.")


def update_allocation_attribute_value(allocation, attribute_name, attribute_value):
    """Task to update the value of an allocation attribute for a specific allocation"""
    attribute = allocation.allocationattribute_set.filter(allocation_attribute_type__name=attribute_name).first()
    attribute_value_str = str(attribute_value).strip()
    if attribute:
        attribute.value = attribute_value_str
        attribute.save()
        logger.info(f"Updated {attribute_name} for allocation {allocation} to {attribute_value_str}")
    else:
        logger.warning(f"No {attribute_name} attribute found for allocation {allocation}. Creating new attribute.")
        AllocationAttribute.objects.create(
            allocation=allocation,
            allocation_attribute_type=AllocationAttributeType.objects.get(name=attribute_name),
            value=attribute_value_str
        )
        logger.info(f"Created {attribute_name} attribute and set value for allocation {allocation} to {attribute_value_str}")