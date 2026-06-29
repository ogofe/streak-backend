"""Production-minded Django settings for the Streak logistics backend."""

import os
import re
from pathlib import Path
from datetime import timedelta

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


load_env_file(BASE_DIR / ".env")

def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def cors_origin_regex(pattern: str) -> str:
    """Convert a wildcard origin like ``*.example.com`` (or ``https://*.example.com``)
    into a regex that matches the apex and any subdomain over http/https."""
    host = re.sub(r"^https?://", "", pattern.strip())
    host = host[2:] if host.startswith("*.") else host.lstrip("*").lstrip(".")
    return rf"^https?://([a-z0-9-]+\.)*{re.escape(host)}$"

SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "dev-only-change-me-streak-logistics-backend",
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env_bool("DJANGO_DEBUG", True)

# Production traffic is allowed from the deployment IP and these domains
# (apex + any subdomain). A leading dot lets Django match all subdomains.
PRODUCTION_HOSTS = "13.51.176.215,.vercel.app,.streakdelivery.com,.onstreak.online,ec2-13-51-176-215.eu-north-1.compute.amazonaws.com"
ALLOWED_HOSTS = env_list(
    "DJANGO_ALLOWED_HOSTS",
    f"localhost,127.0.0.1,[::1],{PRODUCTION_HOSTS}")

# Trusted origins for CSRF / unsafe requests (scheme is required; wildcards allowed).
CSRF_TRUSTED_ORIGINS = env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "https://*.vercel.app,https://*.streakdelivery.com,https://*.onstreak.online,"
    "https://13.51.176.215,http://13.51.176.215,https://ec2-13-51-176-215.eu-north-1.compute.amazonaws.com",
)

# CORS: cross-origin browser requests from the dashboard, customer sites and previews.
CORS_ALLOW_CREDENTIALS = env_bool("DJANGO_CORS_ALLOW_CREDENTIALS", True)
CORS_ALLOW_ALL_ORIGINS = env_bool("DJANGO_CORS_ALLOW_ALL_ORIGINS", DEBUG)

# Exact origins must carry a scheme; any wildcard ("*") entries are converted to
# regexes so configs that list "*.example.com" under origins still work.
_raw_cors_origins = env_list(
    "DJANGO_CORS_ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://13.51.176.215,https://13.51.176.215,http://ec2-13-51-176-215.eu-north-1.compute.amazonaws.com,https://ec2-13-51-176-215.eu-north-1.compute.amazonaws.com",
)
CORS_ALLOWED_ORIGINS = [origin for origin in _raw_cors_origins if "*" not in origin and "://" in origin]

# Wildcard origins (apex + any subdomain) over http or https.
CORS_ALLOWED_ORIGIN_REGEXES = [
    cors_origin_regex(origin) for origin in _raw_cors_origins if "*" in origin
] + env_list(
    "DJANGO_CORS_ALLOWED_ORIGIN_REGEXES",
    r"^https?://([a-z0-9-]+\.)*vercel\.app$,"
    r"^https?://([a-z0-9-]+\.)*streakdelivery\.com$,"
    r"^https?://([a-z0-9-]+\.)*onstreak\.online$",
)

# Application definition

INSTALLED_APPS = [
        'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'channels',
    'core',
    'corsheaders',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'core.middleware.RequestMetricsMiddleware',
    'core.middleware.TenantResolutionMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'streak.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'streak.wsgi.application'
ASGI_APPLICATION = 'streak.asgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

# When DB_USE_IAM_AUTH is on (production Aurora), connect with a short-lived AWS
# IAM auth token instead of a static password (see streak.db.aurora_iam).
DB_USE_IAM_AUTH = env_bool("DB_USE_IAM_AUTH", False)

# Production Aurora cluster defaults (overridable via env).
AURORA_DEFAULT_HOST = "streak-db-1.cluster-cnegeasq66h2.eu-north-1.rds.amazonaws.com"

if os.getenv("POSTGRES_DB") or DB_USE_IAM_AUTH:
    # IAM auth tokens expire after 15 minutes; keep persistent connections under
    # that window so each one is recycled and re-tokenised in time. Hard-capped
    # at 840s (14 min) regardless of the configured value.
    _conn_max_age = min(int(os.getenv("POSTGRES_CONN_MAX_AGE", "600")), 840)
    DATABASES = {
        'default': {
            'ENGINE': 'streak.db.aurora_iam' if DB_USE_IAM_AUTH else 'django.db.backends.postgresql',
            'NAME': os.getenv("POSTGRES_DB", "postgres"),
            'USER': os.getenv("POSTGRES_USER", "postgres"),
            # Password is unused under IAM auth (a token is generated per connection).
            'PASSWORD': "" if DB_USE_IAM_AUTH else os.getenv("POSTGRES_PASSWORD", ""),
            'HOST': os.getenv("POSTGRES_HOST", AURORA_DEFAULT_HOST if DB_USE_IAM_AUTH else "localhost"),
            'PORT': os.getenv("POSTGRES_PORT", "5432"),
            'CONN_MAX_AGE': _conn_max_age,
            'OPTIONS': {
                # Aurora IAM auth requires TLS.
                'sslmode': os.getenv("POSTGRES_SSLMODE", "require" if DB_USE_IAM_AUTH else "prefer"),
            },
        }
    }
else:
    DATABASES = {
        'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        }
    }


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
    'django.contrib.auth.hashers.ScryptPasswordHasher',
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / "staticfiles"

# WhiteNoise serves collected static (admin, DRF) without a separate web server.
# Use the hashed/compressed manifest in production; plain storage in DEBUG so a
# missing collectstatic run doesn't break local development.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Production security (enabled whenever DEBUG is off). Assumes TLS terminates at a
# reverse proxy / load balancer that sets X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = env_bool("DJANGO_USE_X_FORWARDED_HOST", not DEBUG)

if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", True)
    SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", True)
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SESSION_COOKIE_HTTPONLY = True

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'core.authentication.JWTAuthentication',
        'core.authentication.APIKeyAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'core.permissions.IsAuthenticatedActor',
    ],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_THROTTLE_CLASSES': [ # Rate Limiting
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': { # Rate limits
        'anon': os.getenv('DRF_ANON_RATE', '60/minute'),
        'user': os.getenv('DRF_USER_RATE', '600/minute'),
    },
}

JWT_ACCESS_TTL = timedelta(minutes=int(os.getenv("JWT_ACCESS_TTL_MINUTES", "15")))
JWT_REFRESH_TTL = timedelta(days=int(os.getenv("JWT_REFRESH_TTL_DAYS", "30")))
JWT_ISSUER = os.getenv("JWT_ISSUER", "streak-backend")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "streak-dashboard")

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
# Default to the in-memory layer (fine for a single dev process). In production,
# set CHANNEL_LAYER_BACKEND=channels_redis.core.RedisChannelLayer so realtime
# events fan out across multiple ASGI workers via Redis.
CHANNEL_LAYER_BACKEND = os.getenv("CHANNEL_LAYER_BACKEND", "channels.layers.InMemoryChannelLayer")
if "redis" in CHANNEL_LAYER_BACKEND.lower():
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": CHANNEL_LAYER_BACKEND,
            "CONFIG": {"hosts": env_list("CHANNEL_REDIS_HOSTS", REDIS_URL)},
        }
    }
else:
    CHANNEL_LAYERS = {"default": {"BACKEND": CHANNEL_LAYER_BACKEND}}

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", DEBUG)
CELERY_TASK_EAGER_PROPAGATES = True

FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("FILE_UPLOAD_MAX_MEMORY_SIZE", str(10 * 1024 * 1024)))
AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET", "streak-dev-uploads")
UPLOAD_SIGNING_TTL_SECONDS = int(os.getenv("UPLOAD_SIGNING_TTL_SECONDS", "900"))
UPLOAD_STORAGE_BACKEND = os.getenv("UPLOAD_STORAGE_BACKEND", "local")
AWS_REGION = os.getenv("AWS_REGION", "eu-north-1")
UPLOAD_ALLOWED_MIME_TYPES = env_list(
    "UPLOAD_ALLOWED_MIME_TYPES",
    "image/jpeg,image/png,image/webp,application/pdf",
)
LOGIN_MAX_FAILED_ATTEMPTS = int(os.getenv("LOGIN_MAX_FAILED_ATTEMPTS", "5"))
LOGIN_LOCKOUT_MINUTES = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15"))
TOTP_ISSUER = os.getenv("TOTP_ISSUER", "Streak")
NOTIFICATION_EMAIL_PROVIDER = os.getenv("NOTIFICATION_EMAIL_PROVIDER", "")
NOTIFICATION_SMS_PROVIDER = os.getenv("NOTIFICATION_SMS_PROVIDER", "")
NOTIFICATION_PUSH_PROVIDER = os.getenv("NOTIFICATION_PUSH_PROVIDER", "")
GOOGLE_MAPS_API_KEY = (
    os.getenv("GOOGLE_MAPS_API_KEY")
    or os.getenv("GOOGLE_API_KEY")
    or os.getenv("GoogleAPIkey")
    or os.getenv("GOOGLEAPIKEY")
    or os.getenv("GOOGLE_CLOUD_API_KEY")
    or ""
)
GOOGLE_MAPS_MAP_ID = os.getenv("GOOGLE_MAPS_MAP_ID", "")

SLOW_REQUEST_MS = int(os.getenv("SLOW_REQUEST_MS", "1000"))
REQUEST_METRICS_SAMPLE_SIZE = int(os.getenv("REQUEST_METRICS_SAMPLE_SIZE", "500"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(levelname)s %(asctime)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "plain",
        },
    },
    "loggers": {
        "streak.requests": {"handlers": ["console"], "level": os.getenv("REQUEST_LOG_LEVEL", "INFO"), "propagate": False},
        "streak.operations": {"handlers": ["console"], "level": os.getenv("OPERATIONS_LOG_LEVEL", "INFO"), "propagate": False},
    },
}
