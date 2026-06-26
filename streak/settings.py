"""Production-minded Django settings for the Streak logistics backend."""

import os
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

SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "dev-only-change-me-streak-logistics-backend",
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env_bool("DJANGO_DEBUG", True)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
CORS_ALLOW_ALL_ORIGINS = env_bool("DJANGO_CORS_ALLOW_ALL_ORIGINS", DEBUG)
CORS_ALLOWED_ORIGINS = [] if CORS_ALLOW_ALL_ORIGINS else env_list(
    "DJANGO_CORS_ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000",
)

# Application definition

INSTALLED_APPS = [
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

if os.getenv("POSTGRES_DB"):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv("POSTGRES_DB"),
            'USER': os.getenv("POSTGRES_USER", "postgres"),
            'PASSWORD': os.getenv("POSTGRES_PASSWORD", ""),
            'HOST': os.getenv("POSTGRES_HOST", "localhost"),
            'PORT': os.getenv("POSTGRES_PORT", "5432"),
            'CONN_MAX_AGE': int(os.getenv("POSTGRES_CONN_MAX_AGE", "60")),
            'OPTIONS': {
                'sslmode': os.getenv("POSTGRES_SSLMODE", "prefer"),
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

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

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
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": os.getenv(
            "CHANNEL_LAYER_BACKEND",
            "channels.layers.InMemoryChannelLayer",
        ),
    }
}

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", DEBUG)
CELERY_TASK_EAGER_PROPAGATES = True

FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("FILE_UPLOAD_MAX_MEMORY_SIZE", str(10 * 1024 * 1024)))
AWS_S3_BUCKET = os.getenv("AWS_S3_BUCKET", "streak-dev-uploads")
UPLOAD_SIGNING_TTL_SECONDS = int(os.getenv("UPLOAD_SIGNING_TTL_SECONDS", "900"))
UPLOAD_STORAGE_BACKEND = os.getenv("UPLOAD_STORAGE_BACKEND", "local")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
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
