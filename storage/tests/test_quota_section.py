"""The storage_quota section: a Storage request asks for its quota on ColdFront's own request form."""

import importlib
from decimal import Decimal
from unittest.mock import patch

import pytest
from coldfront_utils.allocation_request import checks as request_checks
from coldfront_utils.allocation_request import sections
from coldfront_utils.allocation_request.forms import SECTIONS_CONTAINER_ID
from coldfront_utils.allocation_request.views import AllocationRequestCreateView
from django import forms
from django.apps import apps as global_apps
from django.urls import NoReverseMatch, reverse

from coldfront.core.allocation import views as allocation_views
from coldfront.core.allocation.models import (
    Allocation,
    AllocationAttributeType,
    AllocationStatusChoice,
    AllocationUserStatusChoice,
)
from coldfront.core.allocation.models import AttributeType as AllocationAttributeTypeBase
from coldfront.core.allocation.signals import allocation_new
from coldfront.core.project.models import ProjectUser, ProjectUserRoleChoice, ProjectUserStatusChoice
from coldfront.core.resource.models import AttributeType as ResourceAttributeTypeBase
from coldfront.core.resource.models import Resource, ResourceAttribute, ResourceAttributeType, ResourceType

from storage import checks as storage_checks
from storage import request_section, signals
from storage.constants import QUOTA_ATTRIBUTE_NAME, QUOTA_ON_REQUEST_ATTRIBUTE_NAME
from storage.request_section import QUOTA_SECTION_NAME, StorageQuotaForm, StorageQuotaSection

migration = importlib.import_module("storage.migrations.0004_create_quota_on_request_types")

pytestmark = pytest.mark.django_db

# the headers htmx sends when the resource drop-down asks for the cards
CARDS_REQUEST = {"HX-Request": "true", "HX-Target": SECTIONS_CONTAINER_ID}


def ask_for_quota(resource, value="Yes", attribute_name=QUOTA_ON_REQUEST_ATTRIBUTE_NAME):
    """Give `resource` the Yes/No resource attribute that says whether its requests ask for a quota."""
    attribute_type, _ = ResourceAttributeType.objects.get_or_create(
        name=attribute_name,
        defaults={"attribute_type": ResourceAttributeTypeBase.objects.get_or_create(name="Yes/No")[0]},
    )
    ResourceAttribute.objects.create(resource=resource, resource_attribute_type=attribute_type, value=value)
    return resource


@pytest.fixture
def asking_storage(storage_resource):
    """A Storage resource whose requests ask for a quota."""
    return ask_for_quota(storage_resource)


@pytest.fixture
def cluster(db):
    return Resource.objects.create(
        resource_type=ResourceType.objects.get_or_create(name="Cluster", description="compute")[0],
        name="hpc-cluster",
        description="a cluster",
    )


@pytest.fixture
def requesting_pi(client, project, pi):
    """The PI, signed in, and allowed the project's request page: ColdFront grants it to Managers."""
    ProjectUser.objects.create(
        project=project,
        user=pi,
        role=ProjectUserRoleChoice.objects.get_or_create(name="Manager")[0],
        status=ProjectUserStatusChoice.objects.get_or_create(name="Active")[0],
    )
    AllocationStatusChoice.objects.get_or_create(name="New")
    AllocationUserStatusChoice.objects.get_or_create(name="Active")
    client.force_login(pi)
    return pi


def section_for(resource, project, pi):
    return StorageQuotaSection(resource=resource, project=project, user=pi)


def request_data(project, resource, **section_data):
    """What ColdFront's request page posts for `resource`."""
    return {
        "resource": str(resource.pk),
        "justification": "Sequencing data for the NIH R01 project.",
        "quantity": "1",
        "project": str(project.pk),
        "is_changeable": "True",
        **section_data,
    }


def create_url(project):
    return reverse("allocation-create", args=[project.pk])


# -- Registration and which requests ask ----------------------------------------------------------


def test_the_section_is_registered_when_the_app_is_ready():
    assert sections._registry[QUOTA_SECTION_NAME] is StorageQuotaSection


@pytest.mark.parametrize(("resource_type", "value", "asks"), [("Storage", "Yes", True), ("Storage", "No", False)])
def test_a_storage_resource_asks_for_a_quota_when_its_attribute_says_yes(project, pi, resource_type, value, asks):
    resource = Resource.objects.create(
        resource_type=ResourceType.objects.get_or_create(name=resource_type, description="")[0], name="tier1"
    )
    ask_for_quota(resource, value)

    assert section_for(resource, project, pi).applies() is asks


def test_a_storage_resource_without_the_attribute_asks(storage_resource, project, pi):
    # asking is the default: a resource opts out with No
    assert section_for(storage_resource, project, pi).applies() is True


def test_a_resource_that_isnt_storage_doesnt_ask_even_with_the_attribute(cluster, project, pi):
    ask_for_quota(cluster)

    assert section_for(cluster, project, pi).applies() is False


def test_the_attributes_name_comes_from_the_setting(storage_resource, project, pi, monkeypatch):
    monkeypatch.setattr(request_section, "QUOTA_ON_REQUEST_ATTRIBUTE_NAME", "ask_for_quota")
    ask_for_quota(storage_resource, "No", attribute_name="ask_for_quota")

    assert section_for(storage_resource, project, pi).applies() is False


# -- The form and saving --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("posted", "valid"),
    [
        ("2.5", True),
        ("0.1", True),
        ("2.50", True),  # a trailing zero isn't a second decimal place
        ("999999.9", True),
        ("0.05", False),
        ("0", False),
        ("", False),
        ("0.15", False),  # tenths at most, as the field's step says
        ("1000000", False),  # more than anyone should type
        ("1e9", False),
        ("1e30", False),  # too many digits to write out in Decimal's default context: refused, not a crash
        ("-1e30", False),
    ],
)
def test_the_quota_is_required_in_tenths_between_a_tenth_and_a_million(asking_storage, project, pi, posted, valid):
    form = section_for(asking_storage, project, pi).build_form({f"{QUOTA_SECTION_NAME}-quota": posted})

    assert form.is_valid() is valid, form.errors


@pytest.mark.parametrize(("posted", "stored"), [("2.5", "2.5"), ("2.50", "2.5"), ("1e3", "1000"), ("10", "10")])
def test_saving_stores_the_quota_in_plain_notation(asking_storage, project, pi, make_allocation, posted, stored):
    section = section_for(asking_storage, project, pi)
    form = section.build_form({f"{QUOTA_SECTION_NAME}-quota": posted})
    assert form.is_valid(), form.errors
    allocation = make_allocation()

    section.save(allocation, form)

    assert allocation.allocationattribute_set.get(allocation_attribute_type__name=QUOTA_ATTRIBUTE_NAME).value == stored


class SiteQuotaForm(StorageQuotaForm):
    """A site's own quota form, whose clean() doesn't call StorageQuotaForm's."""

    def clean(self):
        return forms.Form.clean(self)


class SiteQuotaSection(StorageQuotaSection):
    form_class = SiteQuotaForm


def test_saving_doesnt_depend_on_the_forms_clean(asking_storage, project, pi, make_allocation):
    section = SiteQuotaSection(resource=asking_storage, project=project, user=pi)
    form = section.build_form({f"{QUOTA_SECTION_NAME}-quota": "2.5"})
    assert form.is_valid(), form.errors
    allocation = make_allocation()

    section.save(allocation, form)

    assert allocation.allocationattribute_set.get(allocation_attribute_type__name=QUOTA_ATTRIBUTE_NAME).value == "2.5"


def test_without_the_quota_type_the_request_is_refused_not_a_server_error(
    client, requesting_pi, project, asking_storage, caplog
):
    AllocationAttributeType.objects.filter(name=QUOTA_ATTRIBUTE_NAME).delete()

    response = client.post(
        create_url(project), request_data(project, asking_storage, **{f"{QUOTA_SECTION_NAME}-quota": "2.5"})
    )

    assert response.status_code == 200
    assert "can&#x27;t be saved" in response.content.decode()
    assert not Allocation.objects.exists()
    assert QUOTA_ATTRIBUTE_NAME in caplog.text


def test_with_two_quota_types_the_first_is_used(asking_storage, project, pi, make_allocation):
    # hand-made duplicates, which migration 0004 tolerates too
    first = AllocationAttributeType.objects.get(name=QUOTA_ATTRIBUTE_NAME)
    AllocationAttributeType.objects.create(name=QUOTA_ATTRIBUTE_NAME, attribute_type=first.attribute_type)
    section = section_for(asking_storage, project, pi)
    form = section.build_form({f"{QUOTA_SECTION_NAME}-quota": "2.5"})
    assert form.is_valid(), form.errors
    allocation = make_allocation()

    section.save(allocation, form)

    assert allocation.allocationattribute_set.get().allocation_attribute_type == first


# -- On the request page --------------------------------------------------------------------------


def test_the_request_page_shows_the_quota_card(client, requesting_pi, project, asking_storage):
    response = client.get(create_url(project), {"resource": asking_storage.pk}, headers=CARDS_REQUEST)

    html = response.content.decode()
    assert f'data-request-section="{QUOTA_SECTION_NAME}"' in html
    assert f'name="{QUOTA_SECTION_NAME}-quota"' in html


def test_a_resource_that_doesnt_ask_gets_no_quota_card(client, requesting_pi, project, storage_resource):
    ask_for_quota(storage_resource, "No")

    response = client.get(create_url(project), {"resource": storage_resource.pk}, headers=CARDS_REQUEST)

    assert "data-request-section" not in response.content.decode()


def test_a_request_with_its_quota_is_complete_when_submitted(client, requesting_pi, project, asking_storage):
    response = client.post(
        create_url(project), request_data(project, asking_storage, **{f"{QUOTA_SECTION_NAME}-quota": "2.5"})
    )

    allocation = Allocation.objects.get()
    assert response.status_code == 302
    assert "storage-request-details" not in response.url
    quota = allocation.allocationattribute_set.get(allocation_attribute_type__name=QUOTA_ATTRIBUTE_NAME)
    assert Decimal(quota.value) == Decimal("2.5")


def test_a_request_without_its_quota_is_refused_and_creates_nothing(client, requesting_pi, project, asking_storage):
    response = client.post(create_url(project), request_data(project, asking_storage))

    assert response.status_code == 200
    assert not Allocation.objects.exists()


# -- The old second page and view are gone ----------------------------------------------------------


def test_storage_no_longer_replaces_the_request_view():
    assert allocation_views.AllocationCreateView is AllocationRequestCreateView


def test_the_old_quota_page_is_gone():
    with pytest.raises(NoReverseMatch):
        reverse("storage-allocation-request-details", args=[1])


# -- Receivers of allocation_new --------------------------------------------------------------------


def test_no_storage_receiver_filters_on_coldfronts_view():
    # coldfront_utils' E004: the request page now sends allocation_new with its own view as sender
    assert request_checks.check_sender_filters(None) == []


def test_adding_attributes_runs_for_the_request_pages_view(storage_resource, make_allocation):
    requested, elsewhere = make_allocation(storage_resource), make_allocation(storage_resource)

    with patch.object(signals, "add_attributes_to_new_storage_allocation") as add_attributes:
        signals.enable_add_attributes()
        try:
            allocation_new.send(sender=AllocationRequestCreateView, allocation_pk=requested.pk)
            allocation_new.send(sender=None, allocation_pk=elsewhere.pk)
        finally:
            allocation_new.disconnect(dispatch_uid="add_storage_attributes_to_new_allocation")

    add_attributes.assert_called_once_with(requested.pk)


# -- The check: the section needs the app -----------------------------------------------------------


def test_the_check_passes_with_the_request_form_app_installed():
    assert storage_checks.check_allocation_request_app(None) == []


def test_the_check_fails_without_the_request_form_app(monkeypatch):
    real_is_installed = global_apps.is_installed

    def without_request_app(app_name):
        return app_name != "coldfront_utils.allocation_request" and real_is_installed(app_name)

    monkeypatch.setattr(global_apps, "is_installed", without_request_app)

    [error] = storage_checks.check_allocation_request_app(None)

    assert error.id == "storage.E001"


# -- The check: the types the settings name exist ----------------------------------------------------


def test_the_type_check_passes_after_migrating():
    assert storage_checks.check_quota_types(None, databases=["default"]) == []


def test_the_type_check_is_a_database_check():
    # so it runs from migrate and `check --database`, not on every start
    assert "database" in storage_checks.check_quota_types.tags


def test_a_renamed_quota_on_request_setting_is_a_warning(monkeypatch):
    monkeypatch.setattr(storage_checks, "QUOTA_ON_REQUEST_ATTRIBUTE_NAME", "ask_for_quota")

    [warning] = storage_checks.check_quota_types(None, databases=["default"])

    assert warning.id == "storage.W001" and "ask_for_quota" in warning.msg


@pytest.mark.parametrize("copies", [0, 2])
def test_no_quota_type_or_two_is_a_warning(copies):
    existing = AllocationAttributeType.objects.get(name=QUOTA_ATTRIBUTE_NAME)
    if copies == 0:
        existing.delete()
    else:
        AllocationAttributeType.objects.create(name=QUOTA_ATTRIBUTE_NAME, attribute_type=existing.attribute_type)

    [warning] = storage_checks.check_quota_types(None, databases=["default"])

    assert warning.id == "storage.W002" and QUOTA_ATTRIBUTE_NAME in warning.msg


# -- The migration ----------------------------------------------------------------------------------


def run_migration():
    migration.create_quota_types(global_apps, None)


def test_the_types_exist_after_migrating():
    """The test database is built from the migrations, as a deployment's is."""
    assert ResourceAttributeType.objects.get(name=QUOTA_ON_REQUEST_ATTRIBUTE_NAME).attribute_type.name == "Yes/No"
    assert AllocationAttributeType.objects.filter(name=QUOTA_ATTRIBUTE_NAME).exists()


def test_missing_types_are_created():
    ResourceAttributeType.objects.filter(name=QUOTA_ON_REQUEST_ATTRIBUTE_NAME).delete()
    AllocationAttributeType.objects.filter(name=QUOTA_ATTRIBUTE_NAME).delete()

    run_migration()

    assert ResourceAttributeType.objects.get(name=QUOTA_ON_REQUEST_ATTRIBUTE_NAME).attribute_type.name == "Yes/No"
    quota_type = AllocationAttributeType.objects.get(name=QUOTA_ATTRIBUTE_NAME)
    assert quota_type.attribute_type.name == "Float"
    assert quota_type.is_changeable and not quota_type.is_private


def test_existing_types_are_left_as_they_are():
    """Deployments made the quota type by hand, possibly with other flags."""
    AllocationAttributeType.objects.filter(name=QUOTA_ATTRIBUTE_NAME).delete()
    existing = AllocationAttributeType.objects.create(
        name=QUOTA_ATTRIBUTE_NAME,
        attribute_type=AllocationAttributeTypeBase.objects.get_or_create(name="Text")[0],
        is_changeable=False,
    )

    run_migration()

    existing.refresh_from_db()
    assert existing.attribute_type.name == "Text" and not existing.is_changeable
    assert AllocationAttributeType.objects.filter(name=QUOTA_ATTRIBUTE_NAME).count() == 1
    assert ResourceAttributeType.objects.filter(name=QUOTA_ON_REQUEST_ATTRIBUTE_NAME).count() == 1
