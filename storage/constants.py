from enum import Enum

from coldfront.config.env import ENV


class ProvisionStatus(str, Enum):
    """How far provisioning has got for an allocation.

    This describes the storage, not the queue: it says whether the allocation has its share or its
    quota, and survives long after the task that did the work has gone. STARTED is recorded on
    entry, then exactly one of SCHEDULED, SUCCESSFUL or FAILED.

    Why provisioning reached a status belongs in the matching message attribute, not here, so that
    this stays a closed set that can be filtered and counted on.
    """

    STARTED = "started"
    FAILED = "failed"
    SCHEDULED = "scheduled"
    SUCCESSFUL = "successful"

    def __str__(self) -> str:
        # Enum.__str__ would render "ProvisionStatus.STARTED"; the attribute must get the bare
        # value, because update_allocation_attribute_value stores str(value)
        return self.value


STORAGE_PLUGIN_STORAGE_UNITS= ENV.str("STORAGE_PLUGIN_STORAGE_UNITS", default="TB")
QUOTA_ATTRIBUTE_NAME= ENV.str("QUOTA_ATTRIBUTE_NAME", default="Storage Quota (TB)")
CLUSTER_PATH_ATTRIBUTE_NAME= ENV.str("CLUSTER_PATH_ATTRIBUTE_NAME", default="Storage Path")
QUOTA_IN_BYTES_ATTRIBUTE_NAME= ENV.str("QUOTA_IN_BYTES_ATTRIBUTE_NAME", default="reported_quota_bytes")
QUOTA_REPORT_DATE_ATTRIBUTE_NAME= ENV.str("QUOTA_REPORT_DATE_ATTRIBUTE_NAME", default="quota_report_date")
USAGE_IN_BYTES_ATTRIBUTE_NAME= ENV.str("USAGE_IN_BYTES_ATTRIBUTE_NAME", default="reported_usage_bytes")
USAGE_REPORT_DATE_ATTRIBUTE_NAME= ENV.str("USAGE_REPORT_DATE_ATTRIBUTE_NAME", default="usage_report_date")
GROUP_ATTRIBUTE_NAME = ENV.str("GROUP_ATTRIBUTE_NAME", default="Group")
STORAGE_LOG_ONLY = ENV.bool("STORAGE_LOG_ONLY", default=True)
ENABLE_ATTRIBUTES_ON_NEW_ALLOCATION = ENV.bool("STORAGE_ENABLE_ATTRIBUTES_ON_NEW_ALLOCATION", default=False)
USAGE_MATCH_ATTRIBUTE_NAME = ENV.str("USAGE_MATCH_ATTRIBUTE_NAME", default="usage_source_matched") # name of the allocation attribute that indicates whether the usage retrieval task was able to match the allocation to a usage source in the storage system (e.g., a path or share) based on the configured usage_match_attribute_name in the client config; this can be used for troubleshooting and monitoring purposes
QUOTA_MATCH_ATTRIBUTE_NAME = ENV.str("QUOTA_MATCH_ATTRIBUTE_NAME", default="quota_source_matched") # name of the allocation attribute that indicates whether the quota retrieval task was able to match the allocation to a quota source in the storage system (e.g., a path or share) based on the configured usage_match_attribute_name in the client config; this can be used for troubleshooting and monitoring purposes
SHARE_CREATION_TASK_ID_ATTRIBUTE_NAME = ENV.str("SHARE_CREATION_TASK_ID_ATTRIBUTE_NAME", default="share_creation_task_id") # name of the allocation attribute where the task id of the share creation task will be stored
SHARE_CREATION_STATE_ATTRIBUTE_NAME = ENV.str("SHARE_CREATION_STATE_ATTRIBUTE_NAME", default="share_creation_state") # name of the allocation attribute where the state of the share creation will be stored (one of ProvisionStatus)
SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME = ENV.str("SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME", default="share_creation_message") # name of the allocation attribute where the reason for the current share creation state will be stored
QUOTA_UPDATE_TASK_ID_ATTRIBUTE_NAME = ENV.str("QUOTA_UPDATE_TASK_ID_ATTRIBUTE_NAME", default="quota_update_task_id") # name of the allocation attribute where the task id of the quota
QUOTA_UPDATE_STATE_ATTRIBUTE_NAME = ENV.str("QUOTA_UPDATE_STATE_ATTRIBUTE_NAME", default="quota_update_state") # name of the allocation attribute where the state of the quota update will be stored (one of ProvisionStatus)
QUOTA_UPDATE_MESSAGE_ATTRIBUTE_NAME = ENV.str("QUOTA_UPDATE_MESSAGE_ATTRIBUTE_NAME", default="quota_update_message") # name of the allocation attribute where the reason for the current quota update state will be stored
POSIX_FILESYSTEM_HOST = ENV.str("POSIX_FILESYSTEM_HOST", default="localhost") # hostname or IP address of the filesystem server where the storage is mounted
POSIX_FILESYSTEM_USER = ENV.str("POSIX_FILESYSTEM_USER", default="") # username to use when connecting to the filesystem server
POSIX_FILESYSTEM_SSH_KEY = ENV.str("POSIX_FILESYSTEM_SSH_KEY", default="") # path to the SSH private key to use when connecting to the filesystem server