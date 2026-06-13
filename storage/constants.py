from coldfront.config.env import ENV


STORAGE_PLUGIN_STORAGE_UNITS= ENV.str("STORAGE_PLUGIN_STORAGE_UNITS", default="TB")
QUOTA_ATTRIBUTE_NAME= ENV.str("QUOTA_ATTRIBUTE_NAME", default="Storage Quota (TB)")
QUOTA_REPORT_DATE_ATTRIBUTE_NAME= ENV.str("QUOTA_REPORT_DATE_ATTRIBUTE_NAME", default="quota_report_date")
QUOTA_DISPLAY_ATTRIBUTE_NAME= ENV.str("QUOTA_DISPLAY_ATTRIBUTE_NAME", default="quota_display")
USAGE_IN_BYTES_ATTRIBUTE_NAME= ENV.str("USAGE_IN_BYTES_ATTRIBUTE_NAME", default="usage_in_bytes")
USAGE_REPORT_DATE_ATTRIBUTE_NAME= ENV.str("USAGE_REPORT_DATE_ATTRIBUTE_NAME", default="usage_report_date")
GROUP_ATTRIBUTE_NAME = ENV.str("GROUP_ATTRIBUTE_NAME", default="Group")
STORAGE_LOG_ONLY = ENV.bool("STORAGE_LOG_ONLY", default=True)
ENABLE_ATTRIBUTES_ON_NEW_ALLOCATION = ENV.bool("STORAGE_ENABLE_ATTRIBUTES_ON_NEW_ALLOCATION", default=False)
USAGE_MATCH_ATTRIBUTE_NAME = ENV.str("USAGE_MATCH_ATTRIBUTE_NAME", default="usage_source_matched") # name of the allocation attribute that indicates whether the usage retrieval task was able to match the allocation to a usage source in the storage system (e.g., a path or share) based on the configured usage_match_attribute_name in the client config; this can be used for troubleshooting and monitoring purposes
QUOTA_MATCH_ATTRIBUTE_NAME = ENV.str("QUOTA_MATCH_ATTRIBUTE_NAME", default="quota_source_matched") # name of the allocation attribute that indicates whether the quota retrieval task was able to match the allocation to a quota source in the storage system (e.g., a path or share) based on the configured usage_match_attribute_name in the client config; this can be used for troubleshooting and monitoring purposes
SHARE_CREATION_TASK_ID_ATTRIBUTE_NAME = ENV.str("SHARE_CREATION_TASK_ID_ATTRIBUTE_NAME", default="share_creation_task_id") # name of the allocation attribute where the task id of the share creation task will be stored
SHARE_CREATION_STATE_ATTRIBUTE_NAME = ENV.str("SHARE_CREATION_STATE_ATTRIBUTE_NAME", default="share_creation_state") # name of the allocation attribute where the state of the share creation will be stored (e.g., pending, successful, failed)
QUOTA_UPDATE_TASK_ID_ATTRIBUTE_NAME = ENV.str("QUOTA_UPDATE_TASK_ID_ATTRIBUTE_NAME", default="quota_update_task_id") # name of the allocation attribute where the task id of the quota
QUOTA_UPDATE_STATE_ATTRIBUTE_NAME = ENV.str("QUOTA_UPDATE_STATE_ATTRIBUTE_NAME", default="quota_update_state") # name of the allocation attribute where the state of the quota update will be stored (e.g., pending, successful, failed)