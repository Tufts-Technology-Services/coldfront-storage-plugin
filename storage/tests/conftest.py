from unittest.mock import patch

import pytest
from coldfront.core.allocation.models import (
    Allocation,
    AllocationAttributeType,
    AllocationStatusChoice,
    AttributeType,
)
from coldfront.core.field_of_science.models import FieldOfScience
from coldfront.core.project.models import AttributeType as ProjectAttributeTypeBase
from coldfront.core.project.models import (
    Project,
    ProjectAttribute,
    ProjectAttributeType,
    ProjectStatusChoice,
)
from coldfront.core.resource.models import Resource, ResourceType
from django.contrib.auth.models import User


@pytest.fixture(autouse=True)
def ad_search():
    """Stand in for AD everywhere, so no test can reach a real directory. By default every user and
    group exists with its ids; a test makes one missing by changing get_ad_user/get_ad_group."""
    with patch("storage.utils.ADSearch") as ad_search_class:
        instance = ad_search_class.return_value
        instance.get_ad_user.return_value = {"uidNumber": [1000]}
        instance.get_ad_group.return_value = {"gidNumber": [2000], "objectSid": ["S-1-5-21-1"]}
        yield instance


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser("admin", "admin@example.com", "secret")


@pytest.fixture
def regular_user(db):
    return User.objects.create_user("user", "user@example.com", "secret")


@pytest.fixture
def pi(db):
    return User.objects.create_user("pi", "pi@example.com", "secret", first_name="Pat", last_name="Iota")


@pytest.fixture
def project(db, pi):
    """A project whose title exercises CSV quoting."""
    return Project.objects.create(
        title='Big, "Quoted" Project',
        pi=pi,
        description="a project used by the tests",
        field_of_science=FieldOfScience.objects.get_or_create(description="Other")[0],
        status=ProjectStatusChoice.objects.get_or_create(name="Active")[0],
        project_code="P1",
        institution="Tufts",
    )


@pytest.fixture
def storage_resource(db):
    return Resource.objects.create(
        resource_type=ResourceType.objects.get_or_create(name="Storage", description="storage")[0],
        name="hpc-storage",
        description="a storage resource",
    )


@pytest.fixture
def make_attribute_type(db):
    """Get or create an AllocationAttributeType of attribute type Text, by name."""

    def _make(name):
        return AllocationAttributeType.objects.get_or_create(
            name=name,
            defaults={"attribute_type": AttributeType.objects.get_or_create(name="Text")[0]},
        )[0]

    return _make


@pytest.fixture
def make_allocation(db, project, make_attribute_type):
    """Build an allocation on the given resources, with optional allocation attributes."""

    def _make(*resources, attributes=None, **kwargs):
        allocation = Allocation.objects.create(
            project=project,
            status=AllocationStatusChoice.objects.get_or_create(name="Active")[0],
            justification=kwargs.pop("justification", "because"),
            **kwargs,
        )
        allocation.resources.add(*resources)
        for name, value in (attributes or {}).items():
            allocation.allocationattribute_set.create(
                allocation_attribute_type=make_attribute_type(name),
                value=value,
            )
        return allocation

    return _make


@pytest.fixture
def make_project_attribute(db, project):
    def _make(name, value):
        return ProjectAttribute.objects.create(
            proj_attr_type=ProjectAttributeType.objects.get_or_create(
                name=name,
                defaults={"attribute_type": ProjectAttributeTypeBase.objects.get_or_create(name="Text")[0]},
            )[0],
            project=project,
            value=value,
        )

    return _make
