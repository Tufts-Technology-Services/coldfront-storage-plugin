from datetime import datetime
import logging
from django.conf import settings
from .constants import QUOTA_DISPLAY_ATTRIBUTE_NAME, USAGE_IN_BYTES_ATTRIBUTE_NAME, STORAGE_PLUGIN_STORAGE_UNITS, USAGE_REPORT_DATE_ATTRIBUTE_NAME
from coldfront_utils import bytes_to_units, update_allocation_attribute_usage, update_allocation_attribute_value


logger = logging.getLogger(__name__)


def get_client_config(client_id):
    client_config = settings.STORAGE_PLUGIN_CLIENTS.get(client_id)
    if not client_config:
        raise ValueError(f"No configuration found for storage plugin client with id '{client_id}'")
    return client_config


def update_allocation_usage(allocation, new_usage_bytes, report_date=None):
    """
    Task to update storage usage for an allocation. This is intended to be called when an allocation is created or updated for a resource, in order to ensure that the storage usage for the allocation is updated in the storage system accordingly.
    """
    update_allocation_attribute_value(allocation, USAGE_IN_BYTES_ATTRIBUTE_NAME, new_usage_bytes)
    update_allocation_attribute_usage(allocation, QUOTA_DISPLAY_ATTRIBUTE_NAME, round(bytes_to_units(new_usage_bytes, STORAGE_PLUGIN_STORAGE_UNITS), 2))
    if report_date:
        update_allocation_attribute_value(allocation, USAGE_REPORT_DATE_ATTRIBUTE_NAME, report_date.isoformat())
    else:
        update_allocation_attribute_value(allocation, USAGE_REPORT_DATE_ATTRIBUTE_NAME, datetime.now().isoformat())
