# Streak Backend

Django backend for the multi-tenant delivery infrastructure platform described in `PRD_extracted.txt`.

## Implemented Foundation

- Shared-database tenant model with `organization_id` on tenant-owned tables.
- PostgreSQL RLS migration for tenant tables, no-op on SQLite for local development.
- Separate platform and organization auth domains.
- JWT access tokens plus rotating refresh tokens.
- Argon2 password hashing.
- TOTP MFA setup/verify/disable endpoints for platform and organization users.
- Login attempt history and lockout state for brute-force protection.
- Platform support impersonation with explicit activation, short-lived scoped tokens, read-only permission filtering, and audit logs.
- Dynamic platform and organization RBAC.
- REST APIs under `/api/`.
- GraphQL endpoint at `/graphql/`.
- Organization websocket endpoint at `/ws/organization/?token=<access>`.
- Courier websocket endpoint at `/ws/courier/?token=<access>`.
- Delivery creation, assignment, lifecycle validation, tracking throttling, analytics events, notifications, audit logs, API keys, domain verification, and upload intent signing/completion.
- Celery task hooks for async notifications and analytics aggregation.
- Post-commit websocket broadcasts for delivery creation, assignment, status changes, and courier location updates.
- Notification dispatch attempt logging with retryable Celery execution.
- Daily analytics snapshots for delivery volume, completion rate, average delivery time, revenue, courier counts, and rider efficiency.

## Local Setup

Use the existing virtualenv in the parent folder:

```powershell
..\venv\Scripts\python.exe manage.py migrate
..\venv\Scripts\python.exe manage.py seed_streak
..\venv\Scripts\python.exe manage.py runserver
```

Seeded local credentials:

- Organization login: `swift`, `owner@swiftcouriers.com`, `ChangeMe123!`
- Platform login: `admin@streak.local`, `ChangeMe123!`

Change seeded passwords before any non-local use.

## Key Endpoints

- `GET /api/health/`
- `GET /api/health/ready/`
- `GET /api/platform/metrics/`
- `POST /api/auth/organization/login/`
- `POST /api/auth/platform/login/`
- `POST /api/auth/refresh/`
- `POST /api/auth/mfa/setup/`
- `POST /api/auth/mfa/verify/`
- `POST /api/auth/mfa/disable/`
- `POST /api/platform/impersonations/`
- `POST /api/platform/impersonations/{session_id}/end/`
- `GET|POST /api/platform/organizations/`
- `POST /api/platform/organizations/{id}/suspend/`
- `GET|POST /api/deliveries/`
- `POST /api/deliveries/{id}/assign/`
- `POST /api/deliveries/{id}/transition/`
- `GET|POST /api/couriers/`
- `GET /api/couriers/nearest/?latitude=6.524&longitude=3.379&radius_km=10`
- `GET|POST /api/tracking/`
- `GET /api/notifications/`
- `GET /api/analytics/overview/`
- `GET /api/analytics/snapshots/?days=7`
- `GET /api/analytics/heatmap/?days=30`
- `POST /api/analytics/aggregate/`
- `POST /api/api-keys/`
- `POST /api/domains/`
- `POST /api/uploads/intent/`
- `POST /api/uploads/{upload_id}/complete/`

Organization-scoped requests require a Bearer token or `X-API-Key`. Tenant context is derived from the token/API key where possible, and can also be resolved through `X-Organization-ID`, `X-Organization-Subdomain`, subdomain, or custom domain.

When MFA is enabled for a user, login returns `202` with `mfa_required: true` until a valid `mfa_code` is provided. TOTP secrets are signed for tamper detection in local development; production should replace this with KMS/envelope encryption or a dedicated secrets store.

Platform support users cannot access tenant APIs with a normal platform token. They must start an impersonation session, provide a reason, and use the returned short-lived impersonation token. Requested impersonation permissions are filtered to `view_*` permissions to keep support access read-only by default.

Operational monitoring includes structured request logs, an `X-Response-Time-Ms` response header, a public readiness probe at `/api/health/ready/`, and a platform-protected metrics endpoint at `/api/platform/metrics/` requiring `view_platform_metrics`. The metrics endpoint reports recent API latency samples, delivery/notification status counts, auth attempts, active impersonations, and queue configuration state.

## Production Notes

Set these environment variables for production:

- `DJANGO_SECRET_KEY`
- `DJANGO_DEBUG=False`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- `DJANGO_CORS_ALLOWED_ORIGINS`
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`
- `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`
- `AWS_S3_BUCKET`
- `UPLOAD_STORAGE_BACKEND` (`local` or `s3`), `AWS_REGION`, `UPLOAD_ALLOWED_MIME_TYPES`, `UPLOAD_SIGNING_TTL_SECONDS`
- `LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_LOCKOUT_MINUTES`
- `TOTP_ISSUER`
- `NOTIFICATION_EMAIL_PROVIDER`, `NOTIFICATION_SMS_PROVIDER`, `NOTIFICATION_PUSH_PROVIDER`
- `SLOW_REQUEST_MS`, `REQUEST_METRICS_SAMPLE_SIZE`, `REQUEST_LOG_LEVEL`, `OPERATIONS_LOG_LEVEL`

For PostgreSQL, migration `core.0002_enable_postgres_rls` enables and forces row-level security on tenant-owned tables using `app.current_org`.

Analytics snapshots are tenant-scoped and also protected by RLS when PostgreSQL is used. Use `aggregate_daily_metrics` as the Celery entrypoint for scheduled daily aggregation.

Upload intents use a deterministic local presigner by default for development. Set `UPLOAD_STORAGE_BACKEND=s3` with AWS credentials available to generate real S3 presigned PUT URLs. Upload completion validates declared size/checksum, records object state, and marks malware scanning as pending for future scanner integration.

Background task entrypoints include `send_notification`, `aggregate_daily_metrics`, `release_scheduled_deliveries`, and `cleanup_expired_sessions_and_tokens`. Schedule the latter two with Celery Beat or the deployment scheduler.
