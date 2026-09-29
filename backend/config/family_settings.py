"""HTTPS-only settings for the loopback gateway used by the family trial."""

from .settings import *  # noqa: F403

DEBUG = False
# Only the loopback Caddy gateway may reach Gunicorn; it overwrites this header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
# A rotating provider-owned hostname must not opt into subdomain policy or preload.
# Keep all other deployment warnings fatal in the startup gate.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]
MIDDLEWARE = [*MIDDLEWARE, "django.middleware.clickjacking.XFrameOptionsMiddleware"]  # noqa: F405
