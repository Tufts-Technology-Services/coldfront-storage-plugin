# coldfront-storage-plugin
Coldfront plugin for managing storage

The Storage Plugin provides an interface for managing storage within the ColdFront system. It allows for the integration of external storage systems, such as VAST, to handle storage allocation and usage.

## Currently Supported Actions


## Installation
1. Install the plugin using uv:
   ```bash
   uv add coldfront-storage-plugin@git+https://github.com/Tufts-Technology-Services/coldfront-storage-plugin.git
   ```
2. Add the following to your `local_settings.py` file:
```python
from coldfront.config.env import ENV
from coldfront.config.base import INSTALLED_APPS

if ENV.bool("PLUGIN_USER_MANAGEMENT", default=False):
    INSTALLED_APPS += ["storage"]
```

3. Update `local_urls.py` with the following:
```python
from django.urls import include, path
from coldfront.config.base import INSTALLED_APPS
from coldfront.config.env import ENV
from coldfront.config.urls import urlpatterns

if ENV.bool("PLUGIN_USER_MANAGEMENT", default=False) and "storage" in INSTALLED_APPS:
    urlpatterns.append(path("storage/", include("storage.urls")))
```
4. Add the following to your environment variables (e.g., in `/etc/coldfront/coldfront.env`, shown with default values):
```
# User Management Plugin Settings
PLUGIN_USER_MANAGEMENT=False  # set to True to enable the plugin
STORAGE_PLUGIN_STORAGE_UNITS=TB  # units for storage attributes (e.g., TB, GB)
QUOTA_ATTRIBUTE_NAME="Storage Quota (TB)"  # name of the allocation attribute that holds the storage quota value
QUOTA_REPORT_DATE_ATTRIBUTE_NAME=quota_report_date  # name of the allocation attribute that holds the date when the quota was last updated
USAGE_IN_BYTES_ATTRIBUTE_NAME=usage_in_bytes  # name of the allocation attribute that holds the current storage usage in bytes
USAGE_REPORT_DATE_ATTRIBUTE_NAME=usage_report_date  # name of the allocation attribute that holds the date when the usage was last updated
GROUP_ATTRIBUTE_NAME=Group  # name of the group attribute that holds your unix group name
STORAGE_LOG_ONLY=True  # if True, the plugin will only log actions instead of performing them (for testing purposes)
ENABLE_ATTRIBUTES_ON_NEW_ALLOCATION=False  # if True, the plugin will add the necessary storage attributes to new allocations based on the storage client configuration

```
5. Run migrations to create the necessary database tables for the plugin:
```bash
uv run coldfront makemigrations
uv run coldfront migrate
```

6. Create a Resource of type 'Storage' that corresponds to your storage system. Your resource should be a unique combination of storage system parameters.

7. Create a StorageHandler that corresponds to the Resource you just created and configure it with the appropriate client IDs for usage, quota, and creation. 
The client IDs should correspond to the keys in the `STORAGE_PLUGIN_CLIENTS` setting that you will add in the next step.


8. Add info for your storage client(s) to the `STORAGE_PLUGIN_CLIENTS` setting in `local_settings.py`. For example, for VAST:
```python
STORAGE_PLUGIN_CLIENTS = {
    "hpc": {
        "host": "vast_host",
        "user": "vast_user",
        "password": "vast_password",
        "include_share": False,
        "view_policy_id": 7, 
        "protection_policy_id": 10, 
        "tenant_id": 1,
        "protocols": ["NFS"],
        "quota_margin_percent": 10, # percentage to add as a margin to the quota between soft and hard limits
        "snapshot_name_template": "{0}_proj_snap",
        "native_path_attribute_name": "vast_path", # name of the allocation attribute that will hold the native path value for this client
    },
    "truenas": {
        "host": "truenas_host",
        "api_key": "truenas_api_key",
        "native_path_attribute_name": "truenas_path", # name of the allocation attribute that will hold the native path value for this client
    } 
}

## Additional Information
## Relevant Signals
Coldfront emits signals to notify plugins of certain events. The Storage Plugin connects to several signals to manage 
storage allocation and storage quotas based on allocation events. These include:
- `allocation_new`: Triggered when a new allocation is created. The plugin will add needed storage attributes to the allocation based on the storage client configuration.
- `allocation_activate`: Triggered when an allocation is activated (moved to status 'Active'). The plugin will create 
the necessary storage for the allocation and set the appropriate quotas.
- `allocation_attribute_changed`: Triggered when an allocation attribute is changed. The plugin will update the 
storage usage and quotas accordingly if the changed attribute is relevant to storage (e.g., allocation size).


## Security Considerations
By necessity, the Storage Plugin requires credentials to access the storage system(s) and manage storage. 
It is important to ensure that these credentials are stored securely and that access to them is restricted as much as possible.
Additionally, the plugin should be configured with the principle of least privilege in mind, granting only the necessary permissions to perform its functions.

The plugin operates by submitting tasks to Django Q2. A more secure way to run the plugin would be to run redis and the Django Q workers on a separate server from the
main ColdFront application. This can improve security in a couple of ways:
1. Isolation: only allow the task server access to the storage system(s) at the network level
2. The Coldfront web application doesn't need access to the storage system credentials, as it will just submit tasks to the task server which has the credentials and access to the storage system(s). 