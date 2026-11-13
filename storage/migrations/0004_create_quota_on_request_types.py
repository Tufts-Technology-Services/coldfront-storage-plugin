"""Create the attribute types the storage_quota request section reads and writes.

quota_on_request (a Yes/No resource attribute, named by QUOTA_ON_REQUEST_ATTRIBUTE_NAME) says whether a
request for a resource asks for a quota; the section saves the quota as the QUOTA_ATTRIBUTE_NAME
allocation attribute, so it must exist before anyone requests. A type that already exists under the name
is left exactly as it is: deployments made the quota type by hand.

The names are the settings' when this migration runs, once: a setting renamed later isn't followed
(checks storage.W001 and W002 warn about it; the README says how to rename).
"""

from django.db import migrations

from storage.constants import QUOTA_ATTRIBUTE_NAME, QUOTA_ON_REQUEST_ATTRIBUTE_NAME


def create_quota_types(apps, schema_editor):
    ResourceAttributeType = apps.get_model("resource", "ResourceAttributeType")
    ResourceValueType = apps.get_model("resource", "AttributeType")
    AllocationAttributeType = apps.get_model("allocation", "AllocationAttributeType")
    AllocationValueType = apps.get_model("allocation", "AttributeType")

    # by name only, and with filter rather than get: a hand-made duplicate must not stop the migration
    created = []
    if not ResourceAttributeType.objects.filter(name=QUOTA_ON_REQUEST_ATTRIBUTE_NAME).exists():
        ResourceAttributeType.objects.create(
            name=QUOTA_ON_REQUEST_ATTRIBUTE_NAME,
            attribute_type=ResourceValueType.objects.get_or_create(name="Yes/No")[0],
        )
        created.append(QUOTA_ON_REQUEST_ATTRIBUTE_NAME)
    if not AllocationAttributeType.objects.filter(name=QUOTA_ATTRIBUTE_NAME).exists():
        # public, so users see their quota, and changeable, so they can ask for a new one
        AllocationAttributeType.objects.create(
            name=QUOTA_ATTRIBUTE_NAME,
            attribute_type=AllocationValueType.objects.get_or_create(name="Float")[0],
            is_private=False,
            is_changeable=True,
        )
        created.append(QUOTA_ATTRIBUTE_NAME)
    if created:
        print(f"  created attribute types: {', '.join(created)}")


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0003_create_task_attribute_types"),
        ("allocation", "0006_alter_historicalallocation_options_and_more"),
        ("resource", "0003_alter_historicalresource_options_and_more"),
    ]

    operations = [
        # not reversed: the types may be in use by then, and deleting one deletes its attributes
        migrations.RunPython(create_quota_types, migrations.RunPython.noop),
    ]
