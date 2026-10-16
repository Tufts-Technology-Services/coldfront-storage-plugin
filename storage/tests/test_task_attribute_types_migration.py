"""storage.0003_create_task_attribute_types: the attribute types the tasks record into exist."""

import importlib

import pytest
from coldfront.core.allocation.models import AllocationAttributeType, AttributeType
from django.apps import apps as global_apps

migration = importlib.import_module("storage.migrations.0003_create_task_attribute_types")

pytestmark = pytest.mark.django_db


def run_migration():
    migration.create_task_attribute_types(global_apps, None)


def test_the_task_attribute_types_exist_after_migrating():
    """The test database is built from the migrations, as a deployment's is."""
    for name in migration.TASK_ATTRIBUTE_TYPE_NAMES:
        assert AllocationAttributeType.objects.get(name=name).is_private, name


def test_missing_types_are_created_as_private_text():
    AllocationAttributeType.objects.filter(name__in=migration.TASK_ATTRIBUTE_TYPE_NAMES).delete()

    run_migration()

    for name in migration.TASK_ATTRIBUTE_TYPE_NAMES:
        created = AllocationAttributeType.objects.get(name=name)
        assert created.attribute_type.name == "Text"
        assert created.is_private


def test_an_existing_type_is_left_as_it_is():
    """Deployments that predate the migration made some of these by hand, possibly with other flags."""
    name = migration.TASK_ATTRIBUTE_TYPE_NAMES[0]
    AllocationAttributeType.objects.filter(name=name).delete()
    # explicitly public: ColdFront's own default for is_private is True
    existing = AllocationAttributeType.objects.create(
        name=name, attribute_type=AttributeType.objects.get_or_create(name="Text")[0], is_private=False
    )

    run_migration()

    existing.refresh_from_db()
    assert not existing.is_private
    assert AllocationAttributeType.objects.filter(name=name).count() == 1
