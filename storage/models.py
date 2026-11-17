from django.db import models

from coldfront.core.resource.models import Resource


class StorageHandler(models.Model):
    resource = models.OneToOneField(Resource, on_delete=models.CASCADE)
    get_quotas_batch_task = models.CharField(max_length=255, blank=True, null=True)
    set_quota_task = models.CharField(max_length=255, blank=True, null=True)
    quota_client_id = models.CharField(max_length=255, blank=True, null=True)
    create_share_task = models.CharField(max_length=255, blank=True, null=True)
    create_client_id = models.CharField(max_length=255, blank=True, null=True)
    get_usage_batch_task = models.CharField(max_length=255, blank=True, null=True)
    usage_client_id = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        # for service accounts reading people's home and scratch through the REST API (storage.api_extension);
        # it's here because the plugin has no model of its own for them
        permissions = [("view_any_storage", "Can look up anyone's home and scratch through the REST API")]

    def __str__(self):
        return f"Storage system handler for resource: {self.resource.name}"
