"""Storage allocations and quota changes are stamped with who requested them.

The receivers take that user from coldfront_utils.task_context, which the deployment's
ActingUserMiddleware sets for each web request, and which a running task carries for the request
that queued it.
"""

from coldfront.core.allocation.signals import allocation_change_created, allocation_new
from coldfront_utils.task_context import acting_as

# the classes the receivers are connected to: StorageConfig.ready() then swaps storage's own
# AllocationCreateView into coldfront.core.allocation.views, so importing it from there would send
# as a class nothing listens for
from storage.signals import AllocationChangeView, AllocationCreateView


def stamp_value(allocation, attribute_name):
    """The username half of an allocation's "<username>:<timestamp>" stamp."""
    value = allocation.allocationattribute_set.get(allocation_attribute_type__name=attribute_name).value
    return value.split(":", 1)[0]


def test_a_new_allocation_is_stamped_with_whoever_is_acting(storage_resource, make_allocation):
    allocation = make_allocation(storage_resource)

    with acting_as("pi01"):
        allocation_new.send(sender=AllocationCreateView, allocation_pk=allocation.pk)

    assert stamp_value(allocation, "allocation_requester") == "pi01"


def test_a_change_request_is_stamped_with_whoever_is_acting(storage_resource, make_allocation):
    allocation = make_allocation(storage_resource)

    with acting_as("pi01"):
        allocation_change_created.send(sender=AllocationChangeView, allocation_pk=allocation.pk)

    assert stamp_value(allocation, "quota_requester") == "pi01"


def test_with_nobody_acting_the_requester_is_unknown(storage_resource, make_allocation):
    allocation = make_allocation(storage_resource)

    allocation_new.send(sender=AllocationCreateView, allocation_pk=allocation.pk)

    assert stamp_value(allocation, "allocation_requester") == "unknown"
