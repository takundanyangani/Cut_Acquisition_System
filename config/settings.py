"""
Settings for the CUT ICT Acquisition System.

Every value that differs between a laptop and the production server comes
from an environment variable. A `.env` file in the project root is read on
start-up for convenience; real environment variables take precedence.
See docs/06-deployment.md for the full list.
"""

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path):
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else default


def env_list(name, default=""):
    value = os.environ.get(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def database_from_url(url):
    parsed = urlparse(url)
    if parsed.scheme in {"postgres", "postgresql", "pgsql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "",
            "PORT": str(parsed.port or ""),
            "CONN_MAX_AGE": env_int("DATABASE_CONN_MAX_AGE", 60),
            "CONN_HEALTH_CHECKS": True,
        }
    if parsed.scheme == "sqlite":
        # sqlite:///relative/path.db or sqlite:////absolute/path.db
        path = url[len("sqlite:///") :]
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": path if os.path.isabs(path) else str(BASE_DIR / path),
        }
    raise ImproperlyConfigured(f"Unsupported DATABASE_URL scheme: {parsed.scheme!r}")


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "dev-only-insecure-key-change-me-0123456789abcdefghijklmnopqrstuvwxyz"
    else:
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY. It must be a long random string.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1" if DEBUG else "")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")

# Running under a sub-path of the CUT portal, for example /ict-acquisitions
FORCE_SCRIPT_NAME = env("DJANGO_SCRIPT_NAME") or None

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.core",
    "apps.accounts",
    "apps.assets",
    "apps.requisitions",
    "apps.api",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.accounts.middleware.IdleTimeoutMiddleware",
    "apps.accounts.middleware.ForcePasswordChangeMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.app_context",
            ],
        },
    },
]

DATABASES = {
    "default": database_from_url(env("DATABASE_URL", "sqlite:///db.sqlite3")),
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = ["apps.accounts.backends.EmailOrStaffNumberBackend"]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"
PASSWORD_RESET_TIMEOUT = 60 * 60 * 2  # reset links work for two hours

# Optional sign-in against the CUT staff directory (Active Directory or
# OpenLDAP). Requires `pip install -r requirements-ldap.txt` and the system
# LDAP libraries. See docs/07-integration.md.
LDAP_SERVER_URI = env("LDAP_SERVER_URI")
if LDAP_SERVER_URI:
    import ldap  # noqa: E402
    from django_auth_ldap.config import LDAPSearch  # noqa: E402

    AUTH_LDAP_SERVER_URI = LDAP_SERVER_URI
    AUTH_LDAP_BIND_DN = env("LDAP_BIND_DN", "")
    AUTH_LDAP_BIND_PASSWORD = env("LDAP_BIND_PASSWORD", "")
    AUTH_LDAP_USER_SEARCH = LDAPSearch(
        env("LDAP_USER_BASE_DN", ""),
        ldap.SCOPE_SUBTREE,
        env("LDAP_USER_FILTER", "(mail=%(user)s)"),
    )
    AUTH_LDAP_USER_ATTR_MAP = {"first_name": "givenName", "last_name": "sn", "email": "mail"}
    AUTH_LDAP_ALWAYS_UPDATE_USER = True
    AUTH_LDAP_START_TLS = env_bool("LDAP_START_TLS", False)
    AUTHENTICATION_BACKENDS = [
        "apps.accounts.ldap_backend.DirectoryBackend",
        "apps.accounts.backends.EmailOrStaffNumberBackend",
    ]

# ---------------------------------------------------------------------------
# Sessions, cookies and transport security
# ---------------------------------------------------------------------------

SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", 60 * 60 * 8)
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_NAME = "cutacq_session"
CSRF_COOKIE_NAME = "cutacq_csrftoken"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_FAILURE_VIEW = "apps.core.views.csrf_failure"
if FORCE_SCRIPT_NAME:
    SESSION_COOKIE_PATH = FORCE_SCRIPT_NAME
    CSRF_COOKIE_PATH = FORCE_SCRIPT_NAME

_secure_default = not DEBUG
SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", _secure_default)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", _secure_default)
SECURE_HSTS_SECONDS = env_int("DJANGO_HSTS_SECONDS", 0 if DEBUG else 31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

if env_bool("DJANGO_BEHIND_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-gb"
TIME_ZONE = env("DJANGO_TIME_ZONE", "Africa/Harare")
USE_I18N = True
USE_TZ = True
DATE_FORMAT = "j M Y"
DATETIME_FORMAT = "j M Y, H:i"
SHORT_DATE_FORMAT = "d/m/Y"
DATE_INPUT_FORMATS = ["%Y-%m-%d", "%d/%m/%Y"]

# ---------------------------------------------------------------------------
# Static files and uploads
# ---------------------------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = Path(env("DJANGO_STATIC_ROOT", str(BASE_DIR / "staticfiles")))
STATICFILES_DIRS = [BASE_DIR / "static"]

UPLOAD_ROOT = Path(env("ACQ_UPLOAD_ROOT", str(BASE_DIR / "var" / "uploads")))
# Uploads are never served directly; see requisitions.views.download_attachment
MEDIA_ROOT = UPLOAD_ROOT

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(UPLOAD_ROOT)},
    },
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG or env_bool("ACQ_PLAIN_STATIC", False)
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

FILE_UPLOAD_PERMISSIONS = 0o640
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o750
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 25)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 15)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "ICT Acquisitions <noreply@localhost>")
SERVER_EMAIL = env("SERVER_EMAIL", DEFAULT_FROM_EMAIL)
ADMINS = [("ICT Systems", addr) for addr in env_list("DJANGO_ADMINS")]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

# ---------------------------------------------------------------------------
# Application settings (prefix ACQ_)
# ---------------------------------------------------------------------------

ACQ_SITE_URL = env("ACQ_SITE_URL", "http://localhost:8000").rstrip("/")
ACQ_ORG_NAME = env("ACQ_ORG_NAME", "Chinhoyi University of Technology")
ACQ_ORG_SHORT_NAME = env("ACQ_ORG_SHORT_NAME", "CUT")
ACQ_UNIT_NAME = env("ACQ_UNIT_NAME", "ICT Services")
ACQ_HELPDESK_CONTACT = env("ACQ_HELPDESK_CONTACT", "the ICT Helpdesk")

# Signing stages ask for the user's password again.
ACQ_SIGNATURE_REQUIRES_PASSWORD = env_bool("ACQ_SIGNATURE_REQUIRES_PASSWORD", True)

ACQ_IDLE_TIMEOUT_MINUTES = env_int("ACQ_IDLE_TIMEOUT_MINUTES", 30)
ACQ_LOGIN_MAX_FAILURES = env_int("ACQ_LOGIN_MAX_FAILURES", 5)
ACQ_LOGIN_MAX_FAILURES_PER_IP = env_int("ACQ_LOGIN_MAX_FAILURES_PER_IP", 20)
ACQ_LOGIN_LOCK_MINUTES = env_int("ACQ_LOGIN_LOCK_MINUTES", 15)
# Only trust X-Forwarded-For when a reverse proxy you control sets it.
ACQ_TRUST_X_FORWARDED_FOR = env_bool("ACQ_TRUST_X_FORWARDED_FOR", False)

ACQ_UPLOAD_MAX_MB = env_int("ACQ_UPLOAD_MAX_MB", 10)
ACQ_CURRENCY = env("ACQ_CURRENCY", "USD")

# How queued email leaves the outbox: "thread" sends right after the database
# commit without holding up the page; "cron" leaves it for the
# deliver_emails command; "inline" sends during the request (tests only).
ACQ_EMAIL_DELIVERY = env("ACQ_EMAIL_DELIVERY", "thread")

# Working-day targets per stage. They feed due dates, reminders and reports.
ACQ_STAGE_TARGET_DAYS = {
    "returned": env_int("ACQ_TARGET_RETURNED", 5),
    "pa_review": env_int("ACQ_TARGET_PA_REVIEW", 1),
    "director_review": env_int("ACQ_TARGET_DIRECTOR_REVIEW", 2),
    "csm_review": env_int("ACQ_TARGET_CSM_REVIEW", 1),
    "technician_review": env_int("ACQ_TARGET_TECHNICIAN_REVIEW", 3),
    "ops_review": env_int("ACQ_TARGET_OPS_REVIEW", 2),
    "director_approval": env_int("ACQ_TARGET_DIRECTOR_APPROVAL", 2),
    "approved": env_int("ACQ_TARGET_APPROVED", 1),
    "ready_for_issue": env_int("ACQ_TARGET_READY_FOR_ISSUE", 2),
    "awaiting_ack": env_int("ACQ_TARGET_AWAITING_ACK", 3),
}
ACQ_FOLLOW_UP_INTERVAL_DAYS = env_int("ACQ_FOLLOW_UP_INTERVAL_DAYS", 5)

# Retention before closed records move to the archive (Retained Documented
# Information tables in the SOPs). One semester is taken as 183 days.
ACQ_RETENTION_DAYS = {
    "EQR": env_int("ACQ_RETENTION_EQR_DAYS", 183),  # CUT ICT 38: 1 semester
    "RPL": env_int("ACQ_RETENTION_RPL_DAYS", 365),  # CUT ICT 42: 1 year
    "ALC": env_int("ACQ_RETENTION_ALC_DAYS", 365),  # CUT ICT 40: 1 year
}
# Issue vouchers (CUT ICT 40, 8.1: 1 year) stay in the voucher list for this
# long, even when their request has already moved to the archive.
ACQ_RETENTION_VOUCHER_DAYS = env_int("ACQ_RETENTION_VOUCHER_DAYS", 365)
