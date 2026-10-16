"""Create the attribute types the share creation and quota update tasks record into.

The tasks write their task id, state and message on every run, and update_allocation_attribute_value
needs the type to exist, so they are created here, once, rather than assumed or created on first use.
A type that already exists under the name is left exactly as it is: deployments that predate this
created some of them by hand, possibly with other flags.
"""

from django.db import migrations

from storage.constants import (
    QUOTA_UPDATE_MESSAGE_ATTRIBUTE_NAME,
    QUOTA_UPDATE_STATE_ATTRIBUTE_NAME,
    QUOTA_UPDATE_TASK_ID_ATTRIBUTE_NAME,
    SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME,
    SHARE_CREATION_STATE_ATTRIBUTE_NAME,
    SHARE_CREATION_TASK_ID_ATTRIBUTE_NAME,
)

#: Bookkeeping for the tasks, not for users: private, so ColdFront and other plugins (such as the
#: ticketing plugin's customer messages) don't show them.
TASK_ATTRIBUTE_TYPE_NAMES = (
    SHARE_CREATION_TASK_ID_ATTRIBUTE_NAME,
    SHARE_CREATION_STATE_ATTRIBUTE_NAME,
    SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME,
    QUOTA_UPDATE_TASK_ID_ATTRIBUTE_NAME,
    QUOTA_UPDATE_STATE_ATTRIBUTE_NAME,
    QUOTA_UPDATE_MESSAGE_ATTRIBUTE_NAME,
)


def create_task_attribute_types(apps, schema_editor):
    AttributeType = apps.get_model("allocation", "AttributeType")
    AllocationAttributeType = apps.get_model("allocation", "AllocationAttributeType")

    # by name only, and with filter rather than get: a hand-made duplicate must not stop the migration
    missing = [
        name for name in TASK_ATTRIBUTE_TYPE_NAMES if not AllocationAttributeType.objects.filter(name=name).exists()
    ]
    if not missing:
        return
    text_type = AttributeType.objects.filter(name="Text").first() or AttributeType.objects.create(name="Text")
    for name in missing:
        AllocationAttributeType.objects.create(name=name, attribute_type=text_type, is_private=True)
    print(f"  created allocation attribute types: {', '.join(missing)}")


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0002_backfill_provision_status"),
        ("allocation", "0005_auto_20211117_1413"),
    ]

    operations = [
        # not reversed: the types may be in use by then, and deleting one deletes its attributes
        migrations.RunPython(create_task_attribute_types, migrations.RunPython.noop),
    ]
