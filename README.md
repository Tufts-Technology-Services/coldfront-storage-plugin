# coldfront-storage-plugin
Coldfront plugin for managing storage

The Storage Plugin provides an interface for managing storage within the ColdFront system. It allows for the integration of external storage systems, such as VAST, to handle storage allocation and usage.

## Currently Supported Actions
* get_usage_batch_task: Get storage usage for a batch of allocations
* get_quotas_batch_task: Get storage quota for a batch of allocations
* set_quota_task: Set storage quota for an allocation
* create_storage_task: Create storage for an allocation

## Implemented Clients
* VAST: https://vastdata.com/
* TrueNAS: https://www.truenas.com/
* Starfish: https://www.starfishstorage.com/ (only usage reporting)

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
SHARE_CREATION_STATE_ATTRIBUTE_NAME=share_creation_state  # name of the allocation attribute that holds the share creation state (one of the ProvisionStatus values)
SHARE_CREATION_MESSAGE_ATTRIBUTE_NAME=share_creation_message  # name of the allocation attribute that holds the reason for the current share creation state
QUOTA_UPDATE_STATE_ATTRIBUTE_NAME=quota_update_state  # name of the allocation attribute that holds the quota update state (one of the ProvisionStatus values)
QUOTA_UPDATE_MESSAGE_ATTRIBUTE_NAME=quota_update_message  # name of the allocation attribute that holds the reason for the current quota update state
STORAGE_LOG_ONLY=True  # if True, the plugin will only log actions instead of performing them (for testing purposes)
ENABLE_ATTRIBUTES_ON_NEW_ALLOCATION=False  # if True, the plugin will add the necessary storage attributes to new allocations based on the storage client configuration

```
5. Run migrations to create the necessary database tables for the plugin:
```bash
uv run coldfront migrate
```

The plugin ships its own migrations, so there is no need to run `makemigrations` for it. If you are
upgrading a deployment that predates them, its `storage` tables already exist and were created from
a locally generated migration, so run the first one as already applied:

```bash
uv run coldfront migrate storage --fake-initial
```

`storage.0002_backfill_provision_status` then converts the share creation and quota update state
attributes from the free-form strings earlier versions wrote (`success`, `pending`,
`failed: missing information`) to a `ProvisionStatus` value plus a separate message attribute. It
prints what it converted, and reports any value it did not recognise rather than overwriting it.

`storage.0003_create_task_attribute_types` creates the allocation attribute types the tasks record
into, as private Text types: the task id, state and message, for share creation and for quota
updates. A type that already exists under the name is left as it is.

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
```

## Storage Allocation Export
Superusers can download a CSV of every allocation on a Resource whose resource type is `Storage` at:

```
/storage/allocation/export/
```

Each row is one allocation. Alongside the allocation and project fields, the CSV has one column per
allocation attribute type (`allocation_attribute: <name>`) and one per project attribute type
(`project_attribute: <name>`) in use across those allocations. Attributes that are not set on a given
allocation are left blank, and an attribute type with more than one value on the same allocation has
its values joined with `; `. Because the export is restricted to superusers, private attributes are
included.

Non-superusers receive a 403; anonymous users are redirected to the login page.

## Task state

Share creation and quota update tasks each record how they are going on the allocation, as a pair
of attributes: a state and a message. The state is always one of the `ProvisionStatus` values in
`storage/constants.py`, so it can be filtered and counted on. Why the task is in that state goes in
the message attribute instead, which is rewritten on every transition so a reason never outlives
the state it explained.

A task records `started` on entry, then exactly one of the other three. There are no statuses
beyond these four, and every one of them is reachable:

| status | when |
|---|---|
| `started` | the decorator has entered the task body |
| `successful` | the body returned |
| `failed` | the body raised, with the exception as the message |
| `scheduled` | the body scheduled its own retry, via `TaskRetryScheduled` |

This is a property of the allocation, not of the queue. It says whether the allocation has its
share or its quota, and it outlives the task that did the work — the Django Q task record is where
to look for the run itself.

Tasks do not write either attribute themselves. The transitions above are made for them by one of
the two decorators in `storage/utils.py` — `@records_share_creation_state` or
`@records_quota_update_state`. So a task body only has to do the work and signal what happened: it
cannot forget a transition or leave an allocation parked in `started`. Note that `scheduled` does
not re-raise, since a scheduled retry is not a failure.

Both come from the same `_records_task_state()` factory and behave identically. They differ only in
which pair of attributes they record into, which is why there are two of them rather than one: a
quota update must not overwrite the allocation's share creation state.

Every decorated task returns a dict carrying a `message` of `"<state>:<reason>"`, so the outcome
is readable from the Django Q task list and not only from the allocation:

```python
{"message": "successful:"}
{"message": "scheduled:Group g is in AD but has no GID yet"}
```

If the task body returns a dict of its own, its keys are carried through alongside `message`; any
other return value is kept under `result`. `message` is reserved and always wins, so a caller can
rely on it. A task that fails still raises rather than returning, which is what keeps django-q2
recording it as a failed task — its reason goes to the task's traceback and to the allocation's
message attribute.

### Reacting to a task's outcome

Recording `successful` or `failed` sends an outcome signal. The four signals are defined in
coldfront-utils (`coldfront_utils.signals`), so other plugins can receive them without depending on
this one:

| state | share creation | quota update | arguments |
|---|---|---|---|
| `successful` | `share_creation_successful` | `quota_update_successful` | `allocation`, `result` |
| `failed` | `share_creation_failed` | `quota_update_failed` | `allocation`, `message`, `exception` |

`result` is the dict the task returns (`{"message": "successful:"}` when it returns nothing of its
own). `message` is the reason recorded in the message attribute, and `exception` is the exception
the task raised, or `None` for a failure recorded without one (no task or client configured).

The signals are sent by `set_share_creation_state()` and `set_quota_update_state()`, the functions
that record the state, so every path that records an outcome sends one: the decorated tasks, and
the dispatch tasks in `storage/tasks.py` (`create_share`, `set_storage_quota`) when they fail before
handing over or, with `STORAGE_LOG_ONLY`, report success without doing anything. Both setters take a
keyword-only `sender`: the task recording the state, which is the decorated function for a
decorated task. So a receiver can subscribe to one task or to all of them:

```python
from coldfront_utils.signals import share_creation_failed, share_creation_successful
from django.dispatch import receiver
from storage.vast import create_project_share

@receiver(share_creation_successful, sender=create_project_share)
def announce(sender, allocation, result, **kwargs):
    ...  # result is the dict the task returned

@receiver(share_creation_failed)  # every share creation task
def report(sender, allocation, message, exception, **kwargs):
    ...
```

A signal is sent after its state has been recorded, so a receiver sees the allocation as the task
left it. It is sent with `send_robust()`: a receiver that raises is logged rather than allowed to
propagate, so it can neither have django-q2 record a successful task as failed nor replace a failed
task's own exception. One failing receiver does not stop the others.

Nothing is sent for `started` or `scheduled` — a retry is not a completion.

Two rules follow, and the tests enforce both:

- A decorated task must take `allocation_pk`, by either position or keyword.
- A decorated task must never call another decorated task, or both would record state for the same
  run. Share provisioning is therefore factored into undecorated `provision_share()` actions that
  record no state, which tasks compose freely.

The attribute types these tasks write (task id, state and message) are created by migration
`storage.0003_create_task_attribute_types`, so a deployment does not need them set up in advance.

### Waiting for Active Directory

A new allocation's group, and sometimes its owner, can reach AD after the storage tasks first run,
so every task that needs them waits: the project, course and SMB share tasks on VAST,
`create_share_wait_for_group` on TrueNAS, and folder creation. They look the owner and group up with
`lookup_ad_entries()` in `storage/utils.py`, which raises `UserNotFoundError`, `UIDNotFoundError`,
`GroupNotFoundError` or `GIDNotFoundError`, each with its own message, all subclasses of
`ActiveDirectoryNotFoundError`. A task catches that base class (and `VastViewNotFoundError`, on VAST)
and calls `schedule_retry()`, which schedules the task to run again in `wait` minutes with one retry
fewer, passing its arguments by keyword, or re-raises the error once `retries` reaches 0. A decorated
task then raises `TaskRetryScheduled`, so its state is `scheduled` until it runs again; folder
creation, which records no state, just returns. A username that never reaches AD is therefore
retried until its retries run out (five, five minutes apart, by default) before the task fails.

## Running the tests

```bash
uv sync --all-extras
uv run pytest
```

The suite runs against ColdFront's own settings with an in-memory SQLite database, so it needs no
running services. `storage/tests/settings.py` holds the test settings and `storage/tests/urls.py`
mounts the plugin's URLs where a deployment's `local_urls.py` mounts them; both are wired up in
`[tool.pytest.ini_options]`. Shared model fixtures live in `storage/tests/conftest.py`.

`python-ldap` is a dependency, through `coldfront-utils[ad]` (folder creation looks groups up in
AD). It ships as an sdist only, so installing the plugin builds it, which needs the OpenLDAP client
headers.

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