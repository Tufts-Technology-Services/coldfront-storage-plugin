from django.contrib import admin

from .models import StorageHandler


@admin.register(StorageHandler)
class StorageHandlerAdmin(admin.ModelAdmin):
    list_display = ['resource', 'get_quotas_batch_task', 'set_quota_task', 'quota_client_id', 'create_share_task', 'create_client_id', 'get_usage_batch_task', 'usage_client_id']
    search_fields = ['resource__name']
    ordering = ['resource__name']
