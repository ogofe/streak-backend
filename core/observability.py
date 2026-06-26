import json
import logging
import threading
from collections import deque
from time import perf_counter
from typing import Any

from django.conf import settings
from django.db import connection
from django.db.models import Count
from django.utils import timezone

from .models import Delivery, ImpersonationSession, LoginAttempt, Notification, NotificationAttempt


request_logger = logging.getLogger("streak.requests")
operations_logger = logging.getLogger("streak.operations")

_request_samples = deque(maxlen=int(getattr(settings, "REQUEST_METRICS_SAMPLE_SIZE", 500)))
_request_samples_lock = threading.Lock()


def monotonic_time() -> float:
    return perf_counter()


def record_request_metric(request, response, duration_ms: float) -> None:
    sample = {
        "method": request.method,
        "path": request.path,
        "status_code": response.status_code,
        "duration_ms": round(duration_ms, 2),
        "organization_id": _organization_id(request),
        **_actor_metadata(request),
        "timestamp": timezone.now().isoformat(),
    }
    with _request_samples_lock:
        _request_samples.append(sample)

    level = logging.WARNING if duration_ms >= settings.SLOW_REQUEST_MS or response.status_code >= 500 else logging.INFO
    request_logger.log(level, json.dumps({**sample, "event": "http_request"}, separators=(",", ":")))


def record_request_exception(request, duration_ms: float, exc: Exception) -> None:
    request_logger.exception(
        json.dumps(
            {
                "event": "http_request_exception",
                "method": request.method,
                "path": request.path,
                "duration_ms": round(duration_ms, 2),
                "organization_id": _organization_id(request),
                **_actor_metadata(request),
                "exception": exc.__class__.__name__,
            },
            separators=(",", ":"),
        )
    )


def readiness_checks() -> dict[str, Any]:
    checks = {
        "database": _database_check(),
        "celery": {
            "ok": bool(settings.CELERY_BROKER_URL),
            "broker_configured": bool(settings.CELERY_BROKER_URL),
            "eager": bool(settings.CELERY_TASK_ALWAYS_EAGER),
        },
        "channel_layer": {
            "ok": bool(settings.CHANNEL_LAYERS.get("default", {}).get("BACKEND")),
            "backend": settings.CHANNEL_LAYERS.get("default", {}).get("BACKEND", ""),
        },
        "uploads": {
            "ok": bool(settings.UPLOAD_STORAGE_BACKEND),
            "backend": settings.UPLOAD_STORAGE_BACKEND,
            "bucket": settings.AWS_S3_BUCKET,
        },
    }
    status = "ok" if all(check["ok"] for check in checks.values()) else "degraded"
    return {"status": status, "time": timezone.now(), "checks": checks}


def operational_metrics() -> dict[str, Any]:
    since = timezone.now() - timezone.timedelta(hours=24)
    return {
        "time": timezone.now(),
        "window_hours": 24,
        "requests": _request_metrics(),
        "deliveries": _counts_by_status(Delivery.objects.all()),
        "notifications": {
            "by_status": _counts_by_status(Notification.objects.all()),
            "failed_attempts_24h": NotificationAttempt.objects.filter(success=False, created_at__gte=since).count(),
        },
        "auth": {
            "login_successes_24h": LoginAttempt.objects.filter(success=True, created_at__gte=since).count(),
            "login_failures_24h": LoginAttempt.objects.filter(success=False, created_at__gte=since).count(),
        },
        "support": {
            "active_impersonations": ImpersonationSession.objects.filter(
                status=ImpersonationSession.Status.ACTIVE,
                expires_at__gt=timezone.now(),
                ended_at__isnull=True,
            ).count(),
        },
        "queue": {
            "celery_eager": bool(settings.CELERY_TASK_ALWAYS_EAGER),
            "broker_configured": bool(settings.CELERY_BROKER_URL),
        },
    }


def _database_check() -> dict[str, Any]:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:
        operations_logger.exception("database readiness check failed")
        return {"ok": False, "error": exc.__class__.__name__}
    return {"ok": True, "vendor": connection.vendor}


def _request_metrics() -> dict[str, Any]:
    with _request_samples_lock:
        samples = list(_request_samples)
    durations = sorted(sample["duration_ms"] for sample in samples)
    total = len(samples)
    errors = sum(1 for sample in samples if sample["status_code"] >= 500)
    slow = sum(1 for sample in samples if sample["duration_ms"] >= settings.SLOW_REQUEST_MS)
    return {
        "sample_size": total,
        "slow_request_threshold_ms": settings.SLOW_REQUEST_MS,
        "average_ms": round(sum(durations) / total, 2) if total else 0,
        "p95_ms": _percentile(durations, 95),
        "errors": errors,
        "slow_requests": slow,
    }


def _counts_by_status(queryset) -> dict[str, int]:
    return {
        row["status"]: row["count"]
        for row in queryset.values("status").annotate(count=Count("id")).order_by("status")
    }


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0
    index = max(0, min(len(values) - 1, round((percentile / 100) * len(values) + 0.5) - 1))
    return round(values[index], 2)


def _organization_id(request) -> str | None:
    organization = getattr(request, "organization", None)
    if not organization:
        return None
    return str(getattr(organization, "id", organization))


def _actor_metadata(request) -> dict[str, str | None]:
    actor = getattr(request, "actor", None)
    if not actor:
        return {"actor_type": None, "actor_id": None}
    return {
        "actor_type": actor.__class__.__name__,
        "actor_id": str(getattr(actor, "id", "")) or None,
    }
