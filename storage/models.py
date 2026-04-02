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

    def __str__(self):
        return f"Storage system handler for resource: {self.resource.name}"
