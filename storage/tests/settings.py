"""Django settings for the plugin's test suite.

Builds on ColdFront's own settings so the core apps, models and URLs match a real
deployment, then swaps in an in-memory database so the tests need no services.
"""

from coldfront.config.settings import *  # noqa: F403

if "storage" not in INSTALLED_APPS:  # noqa: F405
    INSTALLED_APPS = [*INSTALLED_APPS, "storage"]  # noqa: F405

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
