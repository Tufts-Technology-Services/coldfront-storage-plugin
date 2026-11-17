"""Add storage.view_any_storage, which lets a service account read anyone's home and scratch through
the REST API (storage.api_extension). Only the model's options change: no table does."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("storage", "0004_create_quota_on_request_types"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="storagehandler",
            options={
                "permissions": [("view_any_storage", "Can look up anyone's home and scratch through the REST API")]
            },
        ),
    ]
