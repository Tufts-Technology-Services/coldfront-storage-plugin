from django.db import models
from coldfront.core.allocation.models import AllocationAttributeType
from coldfront.core.resource.models import Resource
    

class StorageHandler(models.Model):
    resource = models.ForeignKey(Resource, on_delete=models.CASCADE, blank=False, null=False)
    get_quotas_batch_task = models.CharField(max_length=255, blank=True, null=True)
    set_quota_task = models.CharField(max_length=255, blank=True, null=True)
    quota_client_id = models.CharField(max_length=255, blank=True, null=True)
    create_share_task = models.CharField(max_length=255, blank=True, null=True)
    create_client_id = models.CharField(max_length=255, blank=True, null=True)
    get_usage_batch_task = models.CharField(max_length=255, blank=True, null=True)
    usage_client_id = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"Storage system handler for resource: {self.resource.name}"


class AllocationAttributeBlueprint(models.Model):
    """
    This model represents a blueprint for an allocation attribute, which defines the type of the attribute and an initial value.
    
    """
    allocation_attribute_type = models.ForeignKey(AllocationAttributeType, on_delete=models.CASCADE)
    value = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.allocation_attribute_type.name}: {self.value}"
    

class AllocationBlueprint(models.Model):
    """
    This model represents a blueprint for an allocation of a resource. when a resource is created, an allocation blueprint can be 
    associated with it to specify the attributes that should be automatically created for any allocations of that resource.
    """
    resource = models.OneToOneField(Resource, on_delete=models.CASCADE)
    attribute_blueprints = models.ForeignKey(AllocationAttributeBlueprint, on_delete=models.CASCADE)

    def __str__(self):
        return f"Allocation blueprint for resource: {self.resource.name}"
