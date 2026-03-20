from django.contrib import admin

from .models import StorageSystem, AllocationAttributeBlueprint, AllocationBlueprint


@admin.register(StorageSystem)
class StorageSystemAdmin(admin.ModelAdmin):
    list_display = ['name', 'description', 'quota_task', 'provisioning_task', 'usage_task']
    search_fields = ['name', 'description']
    ordering = ['name']
    

@admin.register(AllocationBlueprint)
class AllocationBlueprintAdmin(admin.ModelAdmin):
    list_display = ['resource', 'attribute_blueprints']
    search_fields = ['resource__name', 'attribute_blueprints__allocation_attribute_type__name']
    ordering = ['resource', 'attribute_blueprints__allocation_attribute_type__name']


@admin.register(AllocationAttributeBlueprint)
class AllocationAttributeBlueprintAdmin(admin.ModelAdmin):
    list_display = ['allocation_attribute_type', 'value']
    search_fields = ['allocation_attribute_type__name', 'value']
    ordering = ['allocation_attribute_type', 'value']
