# coldfront-storage-plugin
Coldfront plugin for managing storage

The Storage Plugin provides an interface for managing storage within the ColdFront system. It allows for the integration of external storage systems, such as VAST, to handle storage allocation and usage.

## Installation
1. Install the plugin using uv:
   ```bash
   uv add coldfront-storage-plugin@git+https://github.com/Tufts-Technology-Services/coldfront-storage-plugin.git
   ```
2. Copy `storage.py` to the `coldfront/config/plugins/` directory in your ColdFront project.
3. Update `coldfront/config/settings.py` with the following:
```python
plugin_configs['PLUGIN_STORAGE'] = 'plugins/storage.py'
```
4. Add the following to your environment variables (e.g., in `/etc/coldfront/coldfront.env`):
```
# User Management Plugin Settings
PLUGIN_USER_MANAGEMENT=True
UNIX_GROUP_ATTRIBUTE_NAME=ad_group  # name of the group attribute that holds your unix group name
USER_MANAGEMENT_CLIENT_PATH=    # defaults to the included Grouper client; set to your custom client path if you implement your own
USER_MANAGEMENT_ENABLE_SIGNALS=True  # plugin won't listen to Coldfront signals unless this is True
MANAGE_GROUPS_AT_PROJECT_LEVEL=False  # if True, groups are managed at the project level; if False, at the allocation level
USER_MANAGEMENT_REMOVE_USERS_ON_PROJECT_ARCHIVE=False
```
5. Add info for your storage client(s) to the `STORAGE_PLUGIN_CLIENTS` setting in `local_settings.py`. For example, for VAST:
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
        "snapshot_name_template": "{0}_proj_snap"
    },
    "truenas": {
        "host": "truenas_host",
        "api_key": "truenas_api_key"
    } 
}

## Additional Information
## Relevant Signals
Coldfront emits signals to notify plugins of certain events. The Storage Plugin connects to several signals to manage 
storage allocation and storage quotas based on allocation events. These include:
- `allocation_activate`: Triggered when an allocation is activated (moved to status 'Active'). The plugin will create 
the necessary storage for the allocation and set the appropriate quotas.
- `allocation_attribute_changed`: Triggered when an allocation attribute is changed. The plugin will update the 
storage usage and quotas accordingly if the changed attribute is relevant to storage (e.g., allocation size).

