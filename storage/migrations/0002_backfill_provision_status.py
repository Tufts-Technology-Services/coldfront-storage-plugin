"""Move the share creation and quota update state attributes onto ProvisionStatus.

Before ProvisionStatus existed, the tasks wrote free-form strings into the state attribute, and
there was nowhere else to put the reason, so it was packed into the same string: 'success',
'pending', 'failed: missing information'. This splits those apart, into one of the four statuses
plus a message attribute holding the reason.

A value that this does not recognise is left exactly as it is and reported at the end, so that an
unexpected string is something to go and look at rather than something quietly overwritten.
"""

from django.db import migrations

from storage.constants import (
    QUOTA_UPDATE_MESSAGE_ATTRIBUTE_NAME,
    QUOTA_UPDATE_STATE_ATTRIBUTE_NAME,
    SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME,
    SHARE_CREATION_STATE_ATTRIBUTE_NAME,
)

#: A frozen copy of ProvisionStatus. Deliberately not imported: a migration has to keep doing what
#: it did when it ran, so it must not follow the enum if the enum later gains or loses a member.
STATUSES = frozenset({"started", "failed", "scheduled", "successful"})

#: Legacy values that carry no reason of their own, mapped to (status, message).
LEGACY_STATUSES = {
    "success": ("successful", ""),
    "pending": ("started", ""),
    # seen on two allocations in production and written by no code still in the tree; it says the
    # share was there already, which is a success, so the wording is kept as the reason
    "already created": ("successful", "already created"),
}

#: AllocationAttribute.value is a CharField(max_length=128).
MAX_VALUE_LENGTH = 128

#: The pairs to convert: (state attribute name, message attribute name).
ATTRIBUTE_PAIRS = (
    (SHARE_CREATION_STATE_ATTRIBUTE_NAME, SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME),
    (QUOTA_UPDATE_STATE_ATTRIBUTE_NAME, QUOTA_UPDATE_MESSAGE_ATTRIBUTE_NAME),
)


def convert_legacy_status(value):
    """Split a legacy state value into (status, message), or return None to leave it alone.

    None means 'not ours to touch': either the value is already a ProvisionStatus, or it is
    something this migration has never heard of.
    """
    raw = (value or "").strip()
    if raw in STATUSES:
        # already converted; its message attribute belongs to whichever task wrote it
        return None

    head, separator, tail = raw.partition(":")
    head = head.strip().lower()
    if separator:
        # the new code never writes a colon into the state attribute, so a colon means legacy
        return (head, tail.strip()) if head in STATUSES else None
    return LEGACY_STATUSES.get(head)


def _message_attribute_type(apps, name):
    """Get or create the attribute type the reason goes into, matching what the tasks create."""
    AttributeType = apps.get_model("allocation", "AttributeType")
    AllocationAttributeType = apps.get_model("allocation", "AllocationAttributeType")

    existing = AllocationAttributeType.objects.filter(name=name).first()
    if existing:
        return existing
    text_type = AttributeType.objects.filter(name="Text").first()
    if text_type is None:
        text_type = AttributeType.objects.create(name="Text")
    return AllocationAttributeType.objects.create(
        name=name, attribute_type=text_type, is_private=True
    )


def _set_message(AllocationAttribute, allocation_id, message_type, message):
    """Write the reason, but do not create an empty row where there was nothing before."""
    message = message[:MAX_VALUE_LENGTH]
    existing = AllocationAttribute.objects.filter(
        allocation_id=allocation_id, allocation_attribute_type=message_type
    ).first()
    if existing:
        if existing.value != message:
            existing.value = message
            existing.save(update_fields=["value"])
    elif message:
        AllocationAttribute.objects.create(
            allocation_id=allocation_id, allocation_attribute_type=message_type, value=message
        )


def backfill(apps, schema_editor):
    AllocationAttribute = apps.get_model("allocation", "AllocationAttribute")

    for state_name, message_name in ATTRIBUTE_PAIRS:
        state_rows = AllocationAttribute.objects.filter(allocation_attribute_type__name=state_name)
        if not state_rows.exists():
            continue

        message_type = _message_attribute_type(apps, message_name)
        converted, unrecognised = 0, {}

        for row in state_rows.iterator():
            result = convert_legacy_status(row.value)
            if result is None:
                if (row.value or "").strip() not in STATUSES:
                    unrecognised[row.value] = unrecognised.get(row.value, 0) + 1
                continue
            status, message = result
            row.value = status
            row.save(update_fields=["value"])
            _set_message(AllocationAttribute, row.allocation_id, message_type, message)
            converted += 1

        print(f"  {state_name}: converted {converted} value(s)")
        for value, count in sorted(unrecognised.items()):
            print(f"  {state_name}: left {count} row(s) alone, value not recognised: {value!r}")


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0001_initial"),
        # old enough to exist in every supported ColdFront, and late enough that the attribute
        # models are in their final shape
        ("allocation", "0005_auto_20211117_1413"),
    ]

    operations = [
        # not reversible: 'successful' could have come from 'success' or from 'already created',
        # and going back would have to pick one. The new values are plain strings that the old
        # code would read without complaint, so there is nothing to undo.
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
