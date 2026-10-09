import os
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
DEBUG = os.environ.get("DEBUG", "0") == "1"
ALLOWED_HOSTS = [host for host in os.environ.get("ALLOWED_HOSTS", "").split(",") if host]
CSRF_TRUSTED_ORIGINS = [
    origin for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if origin
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "health",
    "catalog",
    "assessment",
]

MIDDLEWARE = [
    "config.observability.RequestTimingMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

database_url = urlparse(os.environ.get("DATABASE_URL", ""))
if database_url.scheme not in {"postgres", "postgresql"}:
    raise RuntimeError("DATABASE_URL must be a PostgreSQL URL")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(database_url.path.lstrip("/")),
        "USER": unquote(database_url.username or ""),
        "PASSWORD": unquote(database_url.password or ""),
        "HOST": database_url.hostname or "localhost",
        "PORT": database_url.port or 5432,
    }
}

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = os.environ.get("APP_TIME_ZONE", "Asia/Shanghai")
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Uploaded book photographs are served only by authenticated catalog views.
MEDIA_ROOT = Path(os.environ.get("INTAKE_MEDIA_ROOT", BASE_DIR / "private-media"))
INTAKE_MAX_PHOTOS = int(os.environ.get("INTAKE_MAX_PHOTOS", "30"))
INTAKE_MAX_PHOTO_BYTES = int(os.environ.get("INTAKE_MAX_PHOTO_BYTES", str(8 * 1024 * 1024)))
INTAKE_MEDIA_RETENTION_DAYS = int(os.environ.get("INTAKE_MEDIA_RETENTION_DAYS", "30"))
INTAKE_ENRICHMENT_PROXY_URL = os.environ.get("INTAKE_ENRICHMENT_PROXY_URL", "")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "EXCEPTION_HANDLER": "catalog.api_errors.exception_handler",
}

# Server-only MiMo credentials. Batch uses its own regional endpoint, never a fallback.
MIMO_API_KEY = os.environ.get("MIMO_API_KEY", "")
MIMO_BASE_URL = os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")
MIMO_BATCH_BASE_URL = os.environ.get("MIMO_BATCH_BASE_URL", "")
MIMO_TEXT_MODEL = os.environ.get("MIMO_TEXT_MODEL", "mimo-v2.6-flash")
MIMO_IMAGE_MODEL = os.environ.get("MIMO_IMAGE_MODEL", "mimo-v2.6-flash")
MIMO_AUDIO_MODEL = os.environ.get("MIMO_AUDIO_MODEL", "mimo-v2.6-flash")
# Dated domestic estimates only; blank/other regions disable monetary estimates.
MIMO_COST_PRICING_REGION = os.environ.get("MIMO_COST_PRICING_REGION", "cn")
MIMO_TIMEOUT_SECONDS = float(os.environ.get("MIMO_TIMEOUT_SECONDS", "60"))
MIMO_CONNECT_TIMEOUT_SECONDS = float(os.environ.get("MIMO_CONNECT_TIMEOUT_SECONDS", "10"))
MIMO_MAX_RETRIES = int(os.environ.get("MIMO_MAX_RETRIES", "2"))
MIMO_RETRY_BACKOFF_SECONDS = float(os.environ.get("MIMO_RETRY_BACKOFF_SECONDS", "1"))
MIMO_RETRY_MAX_DELAY_SECONDS = float(os.environ.get("MIMO_RETRY_MAX_DELAY_SECONDS", "30"))
MIMO_MAX_COMPLETION_TOKENS = int(os.environ.get("MIMO_MAX_COMPLETION_TOKENS", "4096"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"diagnostics": {"()": "config.observability.JsonFormatter"}},
    "handlers": {"diagnostics": {"class": "logging.StreamHandler", "formatter": "diagnostics"}},
    "loggers": {"baby_study": {"handlers": ["diagnostics"], "level": "INFO", "propagate": False}},
}
