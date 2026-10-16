import functools
import inspect
import logging
import re
from datetime import datetime, timezone

from coldfront.core.allocation.models import Allocation, AllocationAttributeType, AttributeType
from coldfront.core.project.models import ProjectAttribute
from coldfront.core.resource.models import ResourceAttribute
from coldfront_utils import bytes_to_units, update_allocation_attribute_usage, update_allocation_attribute_value
from coldfront_utils.attribute.attribute import create_allocation_attribute_type
from coldfront_utils.signals import (
    quota_update_failed,
    quota_update_successful,
    share_creation_failed,
    share_creation_successful,
)
from django.conf import settings

from .constants import (
    GROUP_ATTRIBUTE_NAME,
    QUOTA_ATTRIBUTE_NAME,
    QUOTA_UPDATE_MESSAGE_ATTRIBUTE_NAME,
    QUOTA_UPDATE_STATE_ATTRIBUTE_NAME,
    SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME,
    SHARE_CREATION_STATE_ATTRIBUTE_NAME,
    STORAGE_PLUGIN_STORAGE_UNITS,
    USAGE_IN_BYTES_ATTRIBUTE_NAME,
    USAGE_REPORT_DATE_ATTRIBUTE_NAME,
    ProvisionStatus,
)

logger = logging.getLogger(__name__)


class TaskRetryScheduled(Exception):
    """Raised by a task body that has already scheduled its own retry.

    Tells the state decorator to record ProvisionStatus.SCHEDULED rather than FAILED, and not to re-raise:
    the task has not failed, it is just set to run again later.
    """


def _records_task_state(setter):
    """Build a decorator that records a task's state through the given setter.

    The wrapped task must take allocation_pk, positionally or by keyword. It is entered with the
    state set to started; returning records successful, raising TaskRetryScheduled records scheduled,
    and any other exception records failed with the exception as the message before re-raising.

    This means a task body does the work and nothing else: every state write lives here, so a task
    cannot forget a transition or leave an allocation parked in started. A decorated task must not
    call another decorated task, or the two would both record state for the same run.

    Whatever the body returns, the task returns a dict carrying a "message" of "<state>:<reason>",
    so the outcome is readable from the django_q Task result and not only from the allocation.

    Recording successful or failed also sends the matching outcome signal (see the setters), with
    the task function as sender.
    """

    def decorate(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            allocation = _allocation_from_call(func, args, kwargs)
            # the wrapper, not func, is the sender: the decorated name is what a receiver has to
            # hand, so that is what sender=... has to match
            setter(allocation, ProvisionStatus.STARTED, sender=wrapper)
            try:
                result = func(*args, **kwargs)
            except TaskRetryScheduled as e:
                logger.info(f"{func.__qualname__} is scheduled to run again for allocation {allocation.pk}: {e}")
                setter(allocation, ProvisionStatus.SCHEDULED, str(e), sender=wrapper)
                return _task_result(ProvisionStatus.SCHEDULED, str(e))
            except Exception as e:
                logger.error(f"{func.__qualname__} failed for allocation {allocation.pk}: {e}")
                setter(allocation, ProvisionStatus.FAILED, str(e), sender=wrapper, exception=e)
                raise
            payload = _task_result(ProvisionStatus.SUCCESSFUL, "", result)
            setter(allocation, ProvisionStatus.SUCCESSFUL, sender=wrapper, result=payload)
            return payload

        return wrapper

    return decorate


def _announce_outcome(successful, failed, sender, allocation, state, message, result, exception) -> None:
    """Send the outcome signal for a final state, without letting a receiver change the outcome.

    Nothing is sent for started or scheduled, which aren't outcomes. send_robust, not send: the
    state is recorded by this point, so a receiver blowing up must not propagate out of the task --
    have django_q record as failed something that succeeded, or replace a failed task's own
    exception. Receiver failures are logged instead.
    """
    if state == ProvisionStatus.SUCCESSFUL:
        signal, arguments = successful, {"result": result if result is not None else _task_result(state, message)}
    elif state == ProvisionStatus.FAILED:
        signal, arguments = failed, {"message": message, "exception": exception}
    else:
        return
    for receiver, response in signal.send_robust(sender=sender, allocation=allocation, **arguments):
        if isinstance(response, Exception):
            logger.exception(
                f"receiver {getattr(receiver, '__qualname__', receiver)} failed handling the {state} outcome of "
                f"{getattr(sender, '__qualname__', sender)} for allocation {allocation.pk}: {response}",
                exc_info=response,
            )


def _task_result(state: ProvisionStatus, reason: str, returned=None) -> dict:
    """Build what a decorated task returns: its status, plus anything the body returned.

    A body that returns a dict has its keys carried through; any other value is kept under
    "result". "message" is reserved for the status and always wins, so that a caller can rely on
    it being present and well formed whatever the body did.
    """
    if isinstance(returned, dict):
        result = dict(returned)
    elif returned is not None:
        result = {"result": returned}
    else:
        result = {}
    result["message"] = f"{state}:{reason}"
    return result


def _allocation_from_call(func, args, kwargs):
    """Find the allocation a task was called for, however it was given allocation_pk."""
    bound = inspect.signature(func).bind(*args, **kwargs)
    bound.apply_defaults()
    return Allocation.objects.get(id=bound.arguments["allocation_pk"])


def set_share_creation_state(
    allocation, state: ProvisionStatus, message: str = "", *, sender, result=None, exception=None
) -> None:
    """Record how share creation is going for an allocation, and why.

    Recording successful or failed then sends share_creation_successful or share_creation_failed
    (coldfront_utils.signals) from `sender`, the task recording it: with `result` on success (the
    dict the task returns; built from the state when not given), and with the message and
    `exception` on failure. Every path that reaches a final state comes through here, so they all
    send, decorated tasks or not.
    """
    _set_task_state(allocation, SHARE_CREATION_STATE_ATTRIBUTE_NAME, SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME,
                    state, message)
    _announce_outcome(
        share_creation_successful, share_creation_failed, sender, allocation, state, message, result, exception
    )


def set_quota_update_state(
    allocation, state: ProvisionStatus, message: str = "", *, sender, result=None, exception=None
) -> None:
    """Record how a quota update is going for an allocation, and why.

    Sends quota_update_successful or quota_update_failed on a final state; see
    set_share_creation_state.
    """
    _set_task_state(allocation, QUOTA_UPDATE_STATE_ATTRIBUTE_NAME, QUOTA_UPDATE_MESSAGE_ATTRIBUTE_NAME,
                    state, message)
    _announce_outcome(
        quota_update_successful, quota_update_failed, sender, allocation, state, message, result, exception
    )


def _set_task_state(allocation, state_attribute_name, message_attribute_name, state: ProvisionStatus, message: str) -> None:
    update_allocation_attribute_value(allocation, state_attribute_name, state)
    # the message is written on every transition, including the ones with nothing to say, so that a
    # reason can never outlive the state that it explained
    # created on first use, so a deployment doesn't need it set up in advance; checked by name alone,
    # one query, because create_allocation_attribute_type costs three on every transition
    if not AllocationAttributeType.objects.filter(name=message_attribute_name).exists():
        create_allocation_attribute_type(message_attribute_name, "Text", is_private=True)
    update_allocation_attribute_value(allocation, message_attribute_name, message)


#: Decorate a share creation task; it records into the share creation state and message attributes.
records_share_creation_state = _records_task_state(set_share_creation_state)

#: Decorate a quota update task; it records into the quota update state and message attributes.
records_quota_update_state = _records_task_state(set_quota_update_state)


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


def validate_username(name):
    """
    Validate the username. Must be alphanumeric, lowercase, and start with a letter.
    """
    is_valid = name.isalnum() and len(name) < 10 and len(name) > 2 and name.islower() and name[0].isalpha()
    if not is_valid:
        raise ValueError(f'Invalid name for user: {name}')


def validate_groupname(name, max_length=35):
    """
    Validate the group name. a little longer than usernames, also allows underscores and dashes 
    except for the first or last character.
    """
    if name.lower() in ['tts_rsch_beta_cluster_login', 'tts_rsch_hpc_cluster_login']:
        raise ValueError(f'Group name {name.lower()} is reserved and cannot be used.')
    is_valid = re.match("^[a-z0-9_-]+$", name) and len(name) < max_length and name.islower() and name[0].isalpha() and name[-1].isalnum()
    if not is_valid:
        raise ValueError(f'Invalid name for group: {name}')


def validate_dirname(name, max_length=30):
    """
    Validate the directory name. a little longer than usernames, also allows underscores and dashes
    except for the first character."""
    is_valid = re.match("^[a-z0-9_-]+$", name) and len(name) < max_length and name.islower() and name[0].isalpha()
    if not is_valid:
        raise ValueError(f'Invalid name for directory: {name}')


def get_allocation_group(allocation):
    group = None
    aa = allocation.allocationattribute_set.filter(allocation_attribute_type__name=GROUP_ATTRIBUTE_NAME) # make sure the group attribute type exists
    if aa.exists():
        group = aa.first().value
    else:            
        pa = allocation.project.projectattribute_set.filter(proj_attr_type__name=GROUP_ATTRIBUTE_NAME)
        if pa.exists():
            group = pa.first().value
    return group


class ActiveDirectoryNotFoundError(Exception):
    def __init__(self, message="Active Directory entry not found"):
        self.message = message
        super().__init__(self.message)


class UserNotFoundError(ActiveDirectoryNotFoundError):
    def __init__(self, message="User not found in Active Directory"):
        self.message = message
        super().__init__(self.message)


class UIDNotFoundError(ActiveDirectoryNotFoundError):
    def __init__(self, message="UID not found in Active Directory"):
        self.message = message
        super().__init__(self.message)


class GroupNotFoundError(ActiveDirectoryNotFoundError):
    def __init__(self, message="Group not found in Active Directory"):
        self.message = message
        super().__init__(self.message)


class GIDNotFoundError(ActiveDirectoryNotFoundError):
    def __init__(self, message="GID not found in Active Directory"):
        self.message = message
        super().__init__(self.message)
