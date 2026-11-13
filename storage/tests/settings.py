"""Django settings for the plugin's test suite.

Builds on ColdFront's own settings so the core apps, models and URLs match a real
deployment, then swaps in an in-memory database so the tests need no services.
"""

from coldfront.config.settings import *  # noqa: F403

if "storage" not in INSTALLED_APPS:  # noqa: F405
    # near the front, as the deployment puts it (coldfront-ansible's `first: true`): its templates, such as
    # allocation/allocation_attribute_edit.html, override ColdFront's
    INSTALLED_APPS = [INSTALLED_APPS[0], "storage", *INSTALLED_APPS[1:]]  # noqa: F405
# the request form the storage_quota section adds its card to, as a deployment installs it
INSTALLED_APPS = [*INSTALLED_APPS, "coldfront_utils.allocation_request"]

# storage/urls.py is mounted by the deployment's local_urls.py, so the tests mount it too
ROOT_URLCONF = "storage.tests.urls"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# the tests never send mail; fail loudly rather than reaching a relay
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# a deliberately weak hasher: creating users is much faster, and nothing here checks its strength
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
