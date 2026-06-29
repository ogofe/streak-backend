release: python manage.py migrate --noinput && python manage.py collectstatic --noinput
web: daphne -b 0.0.0.0 -p ${PORT:-8000} streak.asgi:application
worker: celery -A streak worker -l info
