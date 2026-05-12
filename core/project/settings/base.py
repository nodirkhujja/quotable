DEBUG = False
SECRET_KEY = NotImplemented

ALLOWED_HOSTS = []


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",  # required by allauth
    # project apps FIRST so their templates override allauth defaults
    "clips",
    "users",
    "learning",
    "quiz",
    "vocab",
    # allauth
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
]

SITE_ID = 1

MIDDLEWARE = [
    # Stamp every request with a unique ID and bind it into structlog context.
    # Must be first so all downstream middleware and views see request.request_id.
    "core.project.middleware.RequestIDMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # Strip Safari-hostile headers from media responses (video fix).
    # Must come right after SecurityMiddleware so it can override.
    "core.project.middleware.MediaAwareSecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # allauth — must come after AuthenticationMiddleware
    "allauth.account.middleware.AccountMiddleware",
    # Vocabulary onboarding gate — must come after AuthenticationMiddleware
    "learning.middleware.OnboardingGateMiddleware",
]

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# ── allauth config ────────────────────────────────────────
# Google-only auth — no email/password forms
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_SESSION_REMEMBER = True
ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"
# Disable the built-in password signup flow completely
ACCOUNT_ADAPTER = "users.adapters.NoPasswordSignupAdapter"
# Allow Google social signup (this is the intended path)
SOCIALACCOUNT_ADAPTER = "users.adapters.OpenSocialAdapter"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
        "OAUTH_PKCE_ENABLED": True,
        # Auto-populate first_name, last_name, email on signup
        "FETCH_USERINFO": True,
    },
}

# Auto-create a user from the Google profile without showing a signup form
SOCIALACCOUNT_AUTO_SIGNUP = True
# Trust the email Google gives us — no verification needed
SOCIALACCOUNT_EMAIL_VERIFICATION = "none"
# Skip the intermediate "Continue with Google" confirmation page — go straight to Google
SOCIALACCOUNT_LOGIN_ON_GET = True

ROOT_URLCONF = "core.project.urls"


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
                "learning.context_processors.addiction_layer",
            ],
        },
    },
]

WSGI_APPLICATION = "core.project.wsgi.application"


# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": "/Users/nodirxojahamidov/Projects/habit-tracker/db.sqlite3",
    }
}


# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

from pathlib import Path as _Path

_BASE_DIR = _Path(__file__).resolve().parent.parent.parent.parent

STATIC_URL = "static/"
STATIC_ROOT = _BASE_DIR / "staticfiles"
STATICFILES_DIRS = [_BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = _BASE_DIR / "media"

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── Logging ────────────────────────────────────────────────────────────────────
# Django's stdlib logging: suppress INFO noise, only surface errors.
# App code should use structlog.get_logger(__name__) instead.
import logging  # noqa: E402

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}

import structlog  # noqa: E402

# Production default: JSON lines to stdout (structured, machine-parseable).
# Dev settings override this with ConsoleRenderer.
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.ExceptionRenderer(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)
