from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Notification, NotificationAttempt, Organization
from .services import aggregate_analytics_snapshot, cleanup_expired_security_state, release_due_scheduled_deliveries


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 5})
def send_notification(self, notification_id: str) -> str:
    with transaction.atomic():
        notification = Notification.objects.select_for_update().get(id=notification_id)
        attempt_number = notification.attempts + 1
        provider = _provider_for(notification.channel)
        try:
            provider_message_id = _dispatch(notification, provider)
        except Exception as exc:
            notification.attempts = attempt_number
            notification.status = Notification.Status.FAILED
            notification.last_error = str(exc)
            notification.save(update_fields=["attempts", "status", "last_error", "updated_at"])
            NotificationAttempt.objects.create(
                notification=notification,
                attempt_number=attempt_number,
                provider=provider,
                success=False,
                error=str(exc),
            )
            raise
        notification.attempts = attempt_number
        notification.status = Notification.Status.SENT
        notification.sent_at = timezone.now()
        notification.last_error = ""
        notification.save(update_fields=["attempts", "status", "sent_at", "last_error", "updated_at"])
        NotificationAttempt.objects.create(
            notification=notification,
            attempt_number=attempt_number,
            provider=provider,
            success=True,
            metadata={"provider_message_id": provider_message_id},
        )
        return str(notification.id)


@shared_task
def aggregate_daily_metrics(organization_id: str, period_start: str | None = None) -> dict:
    organization = Organization.objects.get(id=organization_id)
    snapshot = aggregate_analytics_snapshot(organization, period_start=period_start)
    return {
        "organization_id": str(organization.id),
        "snapshot_id": str(snapshot.id),
        "period_start": snapshot.period_start.isoformat(),
        "delivery_volume": snapshot.delivery_volume,
        "completion_rate": str(snapshot.completion_rate),
    }


@shared_task
def release_scheduled_deliveries(limit: int = 500) -> dict:
    return release_due_scheduled_deliveries(limit=limit)


@shared_task
def cleanup_expired_sessions_and_tokens(refresh_retention_days: int = 30) -> dict:
    return cleanup_expired_security_state(refresh_retention_days=refresh_retention_days)


def _provider_for(channel: str) -> str:
    if channel == Notification.Channel.IN_APP:
        return "in_app"
    if channel == Notification.Channel.EMAIL:
        return settings.NOTIFICATION_EMAIL_PROVIDER
    if channel == Notification.Channel.SMS:
        return settings.NOTIFICATION_SMS_PROVIDER
    if channel == Notification.Channel.PUSH:
        return settings.NOTIFICATION_PUSH_PROVIDER
    return ""


def _dispatch(notification: Notification, provider: str) -> str:
    if notification.channel == Notification.Channel.IN_APP:
        return f"in_app:{notification.id}"
    if not provider:
        raise RuntimeError(f"No provider configured for {notification.channel} notifications.")
    return f"{provider}:{notification.id}"
