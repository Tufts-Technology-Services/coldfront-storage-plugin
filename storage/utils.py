from datetime import datetime, timezone
import logging
from django.conf import settings
import re
from coldfront.core.project.models import ProjectAttribute
from coldfront.core.resource.models import ResourceAttribute 
from coldfront.core.allocation.models import Allocation, AllocationAttributeType, AttributeType
from .constants import QUOTA_ATTRIBUTE_NAME, USAGE_IN_BYTES_ATTRIBUTE_NAME, STORAGE_PLUGIN_STORAGE_UNITS, USAGE_REPORT_DATE_ATTRIBUTE_NAME
from coldfront_utils import bytes_to_units, update_allocation_attribute_usage, update_allocation_attribute_value


logger = logging.getLogger(__name__)


def get_client_config(client_key):
    client_match = [i for i in settings.STORAGE_PLUGIN_CLIENTS if i['client_key'] == client_key]
    if not client_match:
        raise ValueError(f"No configuration found for storage plugin client with key '{client_key}'")
    return client_match[0]


def update_allocation_usage(allocation, new_usage_bytes, report_date=None):
    """
    Task to update storage usage for an allocation. This is intended to be called when an allocation is created or updated for a resource, in order to ensure that the storage usage for the allocation is updated in the storage system accordingly.
    """
    update_allocation_attribute_value(allocation, USAGE_IN_BYTES_ATTRIBUTE_NAME, new_usage_bytes)
    update_allocation_attribute_usage(allocation, QUOTA_ATTRIBUTE_NAME, round(bytes_to_units(new_usage_bytes, STORAGE_PLUGIN_STORAGE_UNITS), 2))
    if report_date:
        update_allocation_attribute_value(allocation, USAGE_REPORT_DATE_ATTRIBUTE_NAME, report_date.isoformat())
    else:
        update_allocation_attribute_value(allocation, USAGE_REPORT_DATE_ATTRIBUTE_NAME, datetime.now().isoformat())


def render_template_string(template_string, allocation_id):
    matches = re.findall(r'\{(.*?)\}', template_string)
    context = []
    for match in matches:
        context.append((match, parse_context_reference(match, allocation_id)))
    # Extract the key inside braces and return the value from the dict
    def replace_func(match):
        key = match.group(1) # Contents of the capture group ([^}]+)
        for context_key, context_value in context:
            if context_key == key:
                return context_value
        return match.group(0)
    # ([^}]+) matches one or more characters that are NOT a closing brace
    new_text = re.sub(r'\{([^}]+)\}', replace_func, template_string)
    return new_text


def parse_context_reference(reference, allocation_id):
    """
    Parses a context reference in the format 'model.attribute' and retrieves the corresponding value.
    The model can be 'project' or 'resource', and the attribute is the name of the attribute to retrieve.
     :param reference: The context reference string to parse.
    :param allocation_id: The ID of the allocation for which to retrieve the context.
    :return: The value of the referenced attribute."""
    reference_parts = reference.split('.')
    if len(reference_parts) < 2:
        raise ValueError(f"Invalid context reference: {reference}. Expected format 'model.attribute'.")
    if len(reference_parts) > 2:
        raise ValueError(f"Invalid context reference: {reference}. Expected format 'model.attribute', but got more than one dot.")
    model_name = reference_parts[0].strip().lower()
    attribute_name = reference_parts[1].strip().strip('"').strip("'")
    if model_name not in ['project', 'resource']:
        raise ValueError(f"Invalid model name in context reference: {model_name}. Expected 'project' or 'resource'.")
    allocation = Allocation.objects.get(id=allocation_id)
    if model_name == 'project':
        attribute_match = ProjectAttribute.objects.filter(project=allocation.project, proj_attr_type__name__iexact=attribute_name)
        if not attribute_match.exists():
            raise ValueError(f"Project attribute '{attribute_name}' not found for project '{allocation.project.name}'.")
        return attribute_match.first().value
    elif model_name == 'resource':
        attribute_match = ResourceAttribute.objects.filter(resource=allocation.get_parent_resource, resource_attribute_type__name__iexact=attribute_name)
        if not attribute_match.exists():
            raise ValueError(f"Resource attribute '{attribute_name}' not found for resource '{allocation.get_parent_resource.name}'.")
        return attribute_match.first().value


def get_attribute_value(allocation_id, template_string):
    """
    Retrieves the value for an allocation attribute based on the provided blueprint.
    If the blueprint value contains template variables, it renders the template using the allocation context.

    :param allocation_id: The ID of the allocation for which to retrieve the attribute value.
    :param template_string: The template string defining the attribute and its value template.
    :return: The rendered attribute value as a string.
    """
    if '{' in template_string and '}' in template_string:
        return render_template_string(template_string, allocation_id)
    else:
        return template_string


def stamp_allocation_requester(allocation, requester_username):
    create_private_allocation_attribute(allocation, "allocation_requester", f"{requester_username}:{datetime.now(timezone.utc).isoformat()}")


def stamp_allocation_approver(allocation, approver_username):
    create_private_allocation_attribute(allocation, "allocation_approver", f"{approver_username}:{datetime.now(timezone.utc).isoformat()}")


def stamp_quota_requester(allocation, requester_username):
    create_private_allocation_attribute(allocation, "quota_requester", f"{requester_username}:{datetime.now(timezone.utc).isoformat()}")


def stamp_quota_approver(allocation, approver_username):
    create_private_allocation_attribute(allocation, "quota_approver", f"{approver_username}:{datetime.now(timezone.utc).isoformat()}")


def create_private_allocation_attribute(allocation, attribute_name, attribute_value):
    attr_type, _ = AllocationAttributeType.objects.get_or_create(name=attribute_name,
                                                                attribute_type=AttributeType.objects.get(name='Text'),
                                                                defaults={'is_private': True, 'is_unique': False})
    allocation.allocationattribute_set.create(
        allocation_attribute_type=attr_type,
        value=attribute_value,
    )