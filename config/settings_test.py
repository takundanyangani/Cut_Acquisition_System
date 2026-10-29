"""Settings for the automated test suite: `python manage.py test`."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-key-not-for-production-0123456789abcdefghijklmnop")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "testserver,localhost")
os.environ.setdefault("DJANGO_SECURE_COOKIES", "false")
os.environ.setdefault("DJANGO_SECURE_SSL_REDIRECT", "false")
os.environ.setdefault("DJANGO_HSTS_SECONDS", "0")
os.environ.setdefault("ACQ_PLAIN_STATIC", "true")

import tempfile  # noqa: E402

from .settings import *  # noqa: E402,F403

# Argon2 is deliberately slow. Tests use a fast hasher; production keeps Argon2.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
ACQ_EMAIL_DELIVERY = "inline"
UPLOAD_ROOT = Path(tempfile.mkdtemp(prefix="cutacq-test-uploads-"))  # noqa: F405
STATIC_ROOT = Path(tempfile.mkdtemp(prefix="cutacq-test-static-"))  # noqa: F405
MEDIA_ROOT = UPLOAD_ROOT
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(UPLOAD_ROOT)},
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
LOGGING = {"version": 1, "disable_existing_loggers": False, "root": {"level": "CRITICAL"}}
