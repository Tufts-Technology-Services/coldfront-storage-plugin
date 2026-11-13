"""Receivers of ColdFront's attribute and change-request signals, whatever view subclass sends them.

ColdFront sends these signals with sender=self.__class__, and plugins replace its views with
subclasses in ready(): storage replaces AllocationAttributeEditView, billing AllocationChangeView. So
the receivers take any sender and check it with coldfront_utils.signals.sent_by.
"""

from unittest.mock import patch

import pytest
from coldfront_utils.task_context import acting_as
from django.urls import reverse

from coldfront.core.allocation import views as allocation_views
from coldfront.core.allocation.signals import allocation_attribute_changed, allocation_change_created

from storage import signals
from storage.constants import QUOTA_ATTRIBUTE_NAME
from storage.views import AllocationAttributeEditView as StorageAttributeEditView

pytestmark = pytest.mark.django_db


class BillingAllocationChangeView(allocation_views.AllocationChangeView):
    """Stands in for billing's replacement of ColdFront's change request view."""


@pytest.fixture
def quota_allocation(storage_resource, make_allocation):
    return make_allocation(storage_resource, attributes={QUOTA_ATTRIBUTE_NAME: "10", "Storage Path": "/projects/p"})


def quota_attribute(allocation):
    return allocation.allocationattribute_set.get(allocation_attribute_type__name=QUOTA_ATTRIBUTE_NAME)


def has_stamp(allocation, name):
    return allocation.allocationattribute_set.filter(allocation_attribute_type__name=name).exists()


# -- An approved change request starts a quota update ---------------------------------------------


def test_approving_a_quota_change_request_starts_a_quota_update(quota_allocation):
    attribute = quota_attribute(quota_allocation)

    with patch.object(signals, "set_storage_quota") as set_storage_quota, acting_as("admin01"):
        allocation_attribute_changed.send(
            sender=allocation_views.AllocationChangeDetailView,
            attribute_pk=attribute.pk,
            allocation_pk=quota_allocation.pk,
        )

    set_storage_quota.assert_called_once_with(quota_allocation.pk, allocation_attribute_change_id=attribute.pk)
    assert has_stamp(quota_allocation, "quota_approver")


@pytest.mark.parametrize(
    "sender",
    [StorageAttributeEditView, allocation_views.AllocationAttributeEditView, None],
    ids=["storage's edit page", "coldfront's edit page", "elsewhere"],
)
def test_a_quota_change_that_isnt_an_approved_change_request_is_ignored(quota_allocation, sender):
    """Quota changes go through change requests (the edit page shows the quota read-only), so ticketing
    can follow each one from request to done."""
    with patch.object(signals, "set_storage_quota") as set_storage_quota:
        allocation_attribute_changed.send(
            sender=sender, attribute_pk=quota_attribute(quota_allocation).pk, allocation_pk=quota_allocation.pk
        )

    set_storage_quota.assert_not_called()


def test_a_change_to_another_attribute_is_ignored(quota_allocation):
    path = quota_allocation.allocationattribute_set.get(allocation_attribute_type__name="Storage Path")

    with patch.object(signals, "set_storage_quota") as set_storage_quota:
        allocation_attribute_changed.send(
            sender=allocation_views.AllocationChangeDetailView, attribute_pk=path.pk, allocation_pk=quota_allocation.pk
        )

    set_storage_quota.assert_not_called()


# -- The attribute edit page shows the quota read-only --------------------------------------------


def edit_url(allocation):
    return reverse("allocation-attribute-edit", args=[allocation.pk])


def test_the_edit_page_shows_the_quota_read_only_with_a_link_to_a_change_request(client, superuser, quota_allocation):
    client.force_login(superuser)

    response = client.get(edit_url(quota_allocation))

    rows = {form["name"].value(): form for form in response.context["formset"]}
    assert rows[QUOTA_ATTRIBUTE_NAME]["value"].field.disabled
    assert not rows["Storage Path"]["value"].field.disabled
    assert reverse("allocation-change", args=[quota_allocation.pk]) in response.content.decode()


def test_posting_a_new_quota_on_the_edit_page_changes_nothing_but_other_attributes_still_save(
    client, superuser, quota_allocation
):
    client.force_login(superuser)
    attributes = client.get(edit_url(quota_allocation)).context["attributes"]
    new_values = {QUOTA_ATTRIBUTE_NAME: "99", "Storage Path": "/projects/q"}
    data = {
        "attributeform-TOTAL_FORMS": str(len(attributes)),
        "attributeform-INITIAL_FORMS": str(len(attributes)),
        "attributeform-MIN_NUM_FORMS": "0",
        "attributeform-MAX_NUM_FORMS": str(len(attributes)),
    }
    for index, attribute in enumerate(attributes):
        data[f"attributeform-{index}-value"] = new_values.get(attribute["name"], attribute["value"])

    with patch.object(signals, "set_storage_quota") as set_storage_quota:
        client.post(edit_url(quota_allocation), data)

    assert quota_attribute(quota_allocation).value == "10"
    assert (
        quota_allocation.allocationattribute_set.get(allocation_attribute_type__name="Storage Path").value
        == "/projects/q"
    )
    set_storage_quota.assert_not_called()


# -- A change request is stamped with who asked -----------------------------------------------------


def test_a_change_request_through_billings_page_is_stamped(storage_resource, make_allocation):
    allocation = make_allocation(storage_resource)

    with acting_as("pi01"):
        allocation_change_created.send(sender=BillingAllocationChangeView, allocation_pk=allocation.pk)

    assert has_stamp(allocation, "quota_requester")


def test_a_change_request_from_elsewhere_is_not_stamped(storage_resource, make_allocation):
    allocation = make_allocation(storage_resource)

    with acting_as("pi01"):
        allocation_change_created.send(sender=None, allocation_pk=allocation.pk)

    assert not has_stamp(allocation, "quota_requester")
