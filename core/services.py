import secrets
import re
from math import asin, cos, radians, sin, sqrt
from decimal import Decimal
from datetime import datetime, time as datetime_time, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from .audit import write_audit
from .models import (
    AnalyticsEvent,
    AnalyticsSnapshot,
    Courier,
    CustomDomain,
    Delivery,
    DeliveryEvent,
    ImpersonationSession,
    Notification,
    Organization,
    RefreshToken,
    PublicSite,
    PublicSiteBlock,
    PublicSitePage,
    PublicSiteServiceArea,
    TrackingLog,
    Upload,
)
from .realtime import broadcast_organization_event
from .storage import build_presigned_upload
from .tenant import ensure_same_organization, organization_context


VALID_TRANSITIONS = {
    Delivery.Status.REQUESTED: {Delivery.Status.PENDING, Delivery.Status.CANCELLED},
    Delivery.Status.PENDING: {Delivery.Status.ASSIGNED, Delivery.Status.CANCELLED},
    Delivery.Status.ASSIGNED: {Delivery.Status.ACCEPTED, Delivery.Status.PICKED_UP, Delivery.Status.CANCELLED},
    Delivery.Status.ACCEPTED: {Delivery.Status.PICKED_UP, Delivery.Status.CANCELLED},
    Delivery.Status.PICKED_UP: {Delivery.Status.IN_TRANSIT, Delivery.Status.FAILED},
    Delivery.Status.IN_TRANSIT: {Delivery.Status.DELIVERED, Delivery.Status.FAILED},
    Delivery.Status.DELIVERED: set(),
    Delivery.Status.FAILED: set(),
    Delivery.Status.CANCELLED: set(),
}


class DeliveryStateError(ValueError):
    pass


def tenant_queryset(model, organization: Organization):
    return model.objects.for_organization(organization)


@transaction.atomic
def create_delivery(*, organization: Organization, actor=None, request=None, **data) -> Delivery:
    with organization_context(organization):
        reference = data.pop("reference", None) or _delivery_reference()
        delivery = Delivery.objects.create(organization=organization, reference=reference, **data)
        DeliveryEvent.objects.create(
            organization=organization,
            delivery=delivery,
            label="Delivery created",
            status=delivery.status,
            done=True,
            sort_order=10,
        )
        AnalyticsEvent.objects.create(
            organization=organization,
            type=AnalyticsEvent.Type.ORDER,
            message=f"Delivery {delivery.reference} created",
            detail=delivery.customer_name,
        )
        write_audit(
            action="delivery.created",
            organization=organization,
            actor=actor,
            request=request,
            metadata={"delivery_id": str(delivery.id), "reference": delivery.reference},
        )
        _broadcast_after_commit(
            organization.id,
            "delivery.created",
            {"delivery": _delivery_payload(delivery)},
        )
        return delivery


@transaction.atomic
def assign_courier(*, organization: Organization, delivery: Delivery, courier: Courier, actor=None, request=None) -> Delivery:
    with organization_context(organization):
        delivery = Delivery.objects.select_for_update().get(id=delivery.id, organization=organization)
        courier = Courier.objects.select_for_update().get(id=courier.id, organization=organization)
        ensure_same_organization(organization, delivery, courier)
        if delivery.branch_id and courier.branch_id and delivery.branch_id != courier.branch_id:
            raise DeliveryStateError("Courier and delivery must belong to the same branch.")
        if delivery.is_terminal:
            raise DeliveryStateError("Completed, failed, and cancelled deliveries cannot be reassigned.")
        if delivery.courier_id and delivery.courier_id != courier.id:
            raise DeliveryStateError("Delivery already has a courier. Reassign explicitly through support workflow.")
        delivery.courier = courier
        if delivery.status == Delivery.Status.PENDING:
            delivery.status = Delivery.Status.ASSIGNED
        delivery.save(update_fields=["courier", "status", "updated_at"])
        courier.active_delivery_count = Courier.objects.filter(
            organization=organization,
            deliveries__status__in=[
                Delivery.Status.ASSIGNED,
                Delivery.Status.ACCEPTED,
                Delivery.Status.PICKED_UP,
                Delivery.Status.IN_TRANSIT,
            ],
            id=courier.id,
        ).count()
        courier.status = Courier.Status.DELIVERING
        courier.save(update_fields=["active_delivery_count", "status", "updated_at"])
        DeliveryEvent.objects.create(
            organization=organization,
            delivery=delivery,
            label=f"Assigned to {courier.name}",
            status=Delivery.Status.ASSIGNED,
            done=True,
            sort_order=20,
        )
        notification = Notification.objects.create(
            organization=organization,
            channel=Notification.Channel.IN_APP,
            event="delivery.assigned",
            recipient=str(courier.id),
            payload={"delivery_id": str(delivery.id), "reference": delivery.reference},
        )
        _enqueue_notification_after_commit(notification)
        write_audit(
            action="delivery.assigned",
            organization=organization,
            actor=actor,
            request=request,
            metadata={"delivery_id": str(delivery.id), "courier_id": str(courier.id)},
        )
        _broadcast_after_commit(
            organization.id,
            "delivery.assigned",
            {"delivery": _delivery_payload(delivery), "courier": _courier_payload(courier)},
        )
        return delivery


@transaction.atomic
def transition_delivery(
    *,
    organization: Organization,
    delivery: Delivery,
    status: str,
    actor=None,
    request=None,
) -> Delivery:
    with organization_context(organization):
        delivery = Delivery.objects.select_for_update().get(id=delivery.id, organization=organization)
        allowed = VALID_TRANSITIONS[delivery.status]
        if status not in allowed:
            raise DeliveryStateError(f"Cannot transition delivery from {delivery.status} to {status}.")
        delivery.status = status
        now = timezone.now()
        timestamp_field = {
            Delivery.Status.ACCEPTED: "accepted_at",
            Delivery.Status.PICKED_UP: "picked_up_at",
            Delivery.Status.DELIVERED: "completed_at",
            Delivery.Status.FAILED: "failed_at",
            Delivery.Status.CANCELLED: "cancelled_at",
        }.get(status)
        if timestamp_field:
            setattr(delivery, timestamp_field, now)
        delivery.save()
        DeliveryEvent.objects.create(
            organization=organization,
            delivery=delivery,
            label=status.replace("_", " ").title(),
            status=status,
            done=True,
            sort_order=_status_sort(status),
        )
        AnalyticsEvent.objects.create(
            organization=organization,
            type=AnalyticsEvent.Type.DELIVERY,
            message=f"Delivery {delivery.reference} {status.replace('_', ' ')}",
            detail=delivery.customer_name,
        )
        if status in {Delivery.Status.DELIVERED, Delivery.Status.FAILED}:
            notification = Notification.objects.create(
                organization=organization,
                channel=Notification.Channel.IN_APP,
                event=f"delivery.{status}",
                recipient=delivery.customer_phone or delivery.customer_name,
                payload={"delivery_id": str(delivery.id), "reference": delivery.reference, "status": status},
            )
            _enqueue_notification_after_commit(notification)
        write_audit(
            action="delivery.status_changed",
            organization=organization,
            actor=actor,
            request=request,
            metadata={"delivery_id": str(delivery.id), "status": status},
        )
        _broadcast_after_commit(
            organization.id,
            "delivery.status_changed",
            {"delivery": _delivery_payload(delivery)},
        )
        return delivery


@transaction.atomic
def record_tracking(
    *,
    organization: Organization,
    courier: Courier,
    latitude,
    longitude,
    accuracy=None,
    battery_level=None,
    delivery: Delivery | None = None,
) -> TrackingLog | None:
    with organization_context(organization):
        courier = Courier.objects.select_for_update().get(id=courier.id, organization=organization)
        ensure_same_organization(organization, courier, delivery)
        if delivery and courier.branch_id and delivery.branch_id and courier.branch_id != delivery.branch_id:
            raise DeliveryStateError("Courier and delivery must belong to the same branch.")
        now = timezone.now()
        if courier.location_updated_at and (now - courier.location_updated_at).total_seconds() < 10:
            return None
        log = TrackingLog.objects.create(
            organization=organization,
            courier=courier,
            delivery=delivery,
            latitude=latitude,
            longitude=longitude,
            accuracy=accuracy,
            battery_level=battery_level,
            timestamp=now,
        )
        courier.current_latitude = latitude
        courier.current_longitude = longitude
        courier.location_updated_at = now
        if battery_level is not None:
            courier.battery_level = battery_level
        courier.save(
            update_fields=[
                "current_latitude",
                "current_longitude",
                "location_updated_at",
                "battery_level",
                "updated_at",
            ]
        )
        _broadcast_after_commit(
            organization.id,
            "courier.location_updated",
            {
                "courier_id": str(courier.id),
                "latitude": str(latitude),
                "longitude": str(longitude),
                "accuracy": str(accuracy) if accuracy is not None else None,
                "battery_level": battery_level,
                "timestamp": now.isoformat(),
            },
        )
        return log


def overview_metrics(organization: Organization, branch=None) -> dict:
    deliveries = Delivery.objects.for_organization(organization)
    couriers_qs = Courier.objects.for_organization(organization)
    if branch:
        deliveries = deliveries.filter(branch=branch)
        couriers_qs = couriers_qs.filter(branch=branch)
    counts = deliveries.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(status__in=[Delivery.Status.ASSIGNED, Delivery.Status.ACCEPTED, Delivery.Status.PICKED_UP, Delivery.Status.IN_TRANSIT])),
        pending=Count("id", filter=Q(status=Delivery.Status.PENDING)),
        delivered=Count("id", filter=Q(status=Delivery.Status.DELIVERED)),
        failed=Count("id", filter=Q(status=Delivery.Status.FAILED)),
        revenue=Sum("delivery_fee", filter=Q(status=Delivery.Status.DELIVERED)),
        avg_fee=Avg("delivery_fee", filter=Q(status=Delivery.Status.DELIVERED)),
    )
    couriers = couriers_qs.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(status__in=[Courier.Status.AVAILABLE, Courier.Status.DELIVERING])),
    )
    finished = (counts["delivered"] or 0) + (counts["failed"] or 0)
    return {
        "total_deliveries": counts["total"] or 0,
        "active_deliveries": counts["active"] or 0,
        "pending_orders": counts["pending"] or 0,
        "active_couriers": couriers["active"] or 0,
        "total_couriers": couriers["total"] or 0,
        "revenue": counts["revenue"] or Decimal("0"),
        "average_delivery_fee": counts["avg_fee"] or Decimal("0"),
        "success_rate": round(((counts["delivered"] or 0) / finished) * 100) if finished else 100,
    }


def aggregate_analytics_snapshot(organization: Organization, period_start=None) -> AnalyticsSnapshot:
    period_start = period_start or timezone.localdate()
    if isinstance(period_start, str):
        period_start = datetime.fromisoformat(period_start).date()
    start = timezone.make_aware(datetime.combine(period_start, datetime_time.min))
    end = start + timedelta(days=1)

    deliveries = Delivery.objects.for_organization(organization).filter(created_at__gte=start, created_at__lt=end)
    total = deliveries.count()
    completed = deliveries.filter(status=Delivery.Status.DELIVERED).count()
    failed = deliveries.filter(status=Delivery.Status.FAILED).count()
    finished = completed + failed
    revenue = deliveries.filter(status=Delivery.Status.DELIVERED).aggregate(total=Sum("delivery_fee"))["total"] or Decimal("0")
    avg_seconds = _average_delivery_seconds(deliveries.filter(status=Delivery.Status.DELIVERED, completed_at__isnull=False))
    couriers = Courier.objects.for_organization(organization)
    total_couriers = couriers.count()
    active_couriers = couriers.filter(status__in=[Courier.Status.AVAILABLE, Courier.Status.DELIVERING]).count()
    rider_efficiency = _rider_efficiency(organization, start, end)

    snapshot, _ = AnalyticsSnapshot.objects.update_or_create(
        organization=organization,
        period_type=AnalyticsSnapshot.PeriodType.DAILY,
        period_start=period_start,
        defaults={
            "delivery_volume": total,
            "completed_deliveries": completed,
            "failed_deliveries": failed,
            "completion_rate": Decimal(str(round((completed / finished) * 100, 2))) if finished else Decimal("0"),
            "average_delivery_seconds": avg_seconds,
            "revenue": revenue,
            "delivery_fees": revenue,
            "active_courier_count": active_couriers,
            "total_courier_count": total_couriers,
            "rider_efficiency": rider_efficiency,
            "metadata": {"source": "aggregate_analytics_snapshot"},
        },
    )
    return snapshot


def analytics_snapshot_series(organization: Organization, days: int = 7):
    days = max(1, min(days, 90))
    start = timezone.localdate() - timedelta(days=days - 1)
    return AnalyticsSnapshot.objects.for_organization(organization).filter(
        period_type=AnalyticsSnapshot.PeriodType.DAILY,
        period_start__gte=start,
    ).order_by("period_start")


def nearest_couriers(
    organization: Organization,
    *,
    latitude,
    longitude,
    radius_km: float = 10,
    limit: int = 10,
    branch=None,
) -> list[dict]:
    latitude = float(latitude)
    longitude = float(longitude)
    radius_km = max(0.1, min(float(radius_km), 500))
    limit = max(1, min(int(limit), 50))
    candidates = Courier.objects.for_organization(organization).filter(
        current_latitude__isnull=False,
        current_longitude__isnull=False,
        status__in=[Courier.Status.AVAILABLE, Courier.Status.DELIVERING],
    )
    if branch:
        candidates = candidates.filter(branch=branch)
    results = []
    for courier in candidates:
        distance_km = _haversine_km(latitude, longitude, float(courier.current_latitude), float(courier.current_longitude))
        if distance_km <= radius_km:
            results.append(
                {
                    "courier": courier,
                    "distance_km": round(distance_km, 3),
                    "latitude": courier.current_latitude,
                    "longitude": courier.current_longitude,
                    "location_updated_at": courier.location_updated_at,
                }
            )
    return sorted(results, key=lambda item: (item["distance_km"], item["courier"].active_delivery_count))[:limit]


def delivery_zone_heatmap(organization: Organization, days: int = 30, branch=None) -> dict:
    days = max(1, min(int(days), 365))
    since = timezone.now() - timedelta(days=days)
    deliveries = Delivery.objects.for_organization(organization).filter(created_at__gte=since)
    if branch:
        deliveries = deliveries.filter(branch=branch)
    rows = (
        deliveries
        .values("zone")
        .annotate(
            total=Count("id"),
            delivered=Count("id", filter=Q(status=Delivery.Status.DELIVERED)),
            failed=Count("id", filter=Q(status=Delivery.Status.FAILED)),
            revenue=Sum("delivery_fee", filter=Q(status=Delivery.Status.DELIVERED)),
        )
        .order_by("-total", "zone")
    )
    return {
        "window_days": days,
        "zones": [
            {
                "zone": row["zone"] or "unassigned",
                "total_deliveries": row["total"],
                "delivered": row["delivered"],
                "failed": row["failed"],
                "revenue": row["revenue"] or Decimal("0"),
            }
            for row in rows
        ],
    }


def release_due_scheduled_deliveries(now=None, limit: int = 500) -> dict:
    now = now or timezone.now()
    released = []
    due = (
        Delivery.objects.filter(
            status=Delivery.Status.PENDING,
            scheduled_time__isnull=False,
            scheduled_time__lte=now,
        )
        .select_related("organization")
        .order_by("scheduled_time")[: max(1, min(limit, 5000))]
    )
    for delivery in due:
        if delivery.metadata.get("scheduled_release_at"):
            continue
        with transaction.atomic(), organization_context(delivery.organization):
            locked = Delivery.objects.select_for_update().get(id=delivery.id, organization=delivery.organization)
            if locked.metadata.get("scheduled_release_at"):
                continue
            locked.metadata = {**locked.metadata, "scheduled_release_at": now.isoformat()}
            locked.save(update_fields=["metadata", "updated_at"])
            AnalyticsEvent.objects.create(
                organization=locked.organization,
                type=AnalyticsEvent.Type.ORDER,
                message=f"Scheduled delivery {locked.reference} is ready for dispatch",
                detail=locked.customer_name,
            )
            _broadcast_after_commit(
                locked.organization_id,
                "delivery.scheduled_ready",
                {"delivery": _delivery_payload(locked)},
            )
            released.append(str(locked.id))
    return {"released_count": len(released), "delivery_ids": released}


def cleanup_expired_security_state(now=None, refresh_retention_days: int = 30) -> dict:
    now = now or timezone.now()
    expired_impersonations = ImpersonationSession.objects.filter(
        status=ImpersonationSession.Status.ACTIVE,
        expires_at__lte=now,
        ended_at__isnull=True,
    ).update(status=ImpersonationSession.Status.EXPIRED, ended_at=now, updated_at=now)
    refresh_cutoff = now - timedelta(days=max(1, int(refresh_retention_days)))
    deleted_refresh_tokens, _ = RefreshToken.objects.filter(
        expires_at__lt=refresh_cutoff,
    ).delete()
    return {
        "expired_impersonations": expired_impersonations,
        "deleted_refresh_tokens": deleted_refresh_tokens,
    }


def create_domain_verification(organization: Organization, domain: str) -> CustomDomain:
    value = f"streak-verification={secrets.token_urlsafe(24)}"
    return CustomDomain.objects.create(
        organization=organization,
        domain=domain.lower(),
        txt_record_name=f"_streak.{domain.lower()}",
        txt_record_value=value,
    )


def get_or_create_public_site(organization: Organization) -> PublicSite:
    site, _ = PublicSite.objects.get_or_create(
        organization=organization,
        defaults={
            "headline": f"Book deliveries with {organization.name}",
            "description": "Request pickup and dropoff deliveries from our team.",
            "contact_email": "",
            "contact_phone": "",
        },
    )
    ensure_default_public_site_page(site)
    return site


def ensure_default_public_site_page(site: PublicSite) -> PublicSitePage:
    page, created = PublicSitePage.objects.get_or_create(
        organization=site.organization,
        public_site=site,
        slug="/",
        defaults={
            "title": "Home",
            "status": PublicSitePage.Status.PUBLISHED,
            "published_at": timezone.now(),
            "sort_order": 0,
        },
    )
    if created:
        PublicSiteBlock.objects.bulk_create(
            [
                PublicSiteBlock(
                    organization=site.organization,
                    public_site=site,
                    page=page,
                    type=PublicSiteBlock.Type.HERO,
                    sort_order=10,
                    eyebrow="Delivery portal",
                    headline=site.headline or f"Book deliveries with {site.organization.name}",
                    body=site.description or "Request pickup and dropoff deliveries from our team.",
                    button_label="Request delivery",
                    button_href="/request",
                ),
                PublicSiteBlock(
                    organization=site.organization,
                    public_site=site,
                    page=page,
                    type=PublicSiteBlock.Type.SERVICE_AREAS,
                    sort_order=20,
                    headline="Service areas",
                ),
                PublicSiteBlock(
                    organization=site.organization,
                    public_site=site,
                    page=page,
                    type=PublicSiteBlock.Type.CONTACT_BAND,
                    sort_order=30,
                    headline="Contact us",
                ),
            ]
        )
    return page


@transaction.atomic
def update_public_site(*, organization: Organization, service_area_names=None, **data) -> PublicSite:
    with organization_context(organization):
        site = get_or_create_public_site(organization)
        allowed_fields = {
            "enabled",
            "headline",
            "description",
            "contact_phone",
            "contact_email",
            "opening_hours",
            "request_form_enabled",
            "tracking_enabled",
            "logo_url",
            "hero_image_url",
        }
        update_fields = ["updated_at"]
        for field in allowed_fields:
            if field in data:
                setattr(site, field, data[field])
                update_fields.append(field)
        site.save(update_fields=update_fields)

        if service_area_names is not None:
            PublicSiteServiceArea.objects.filter(organization=organization, public_site=site).delete()
            rows = []
            seen = set()
            for index, raw_name in enumerate(service_area_names):
                name = str(raw_name).strip()
                key = name.lower()
                if not name or key in seen:
                    continue
                seen.add(key)
                rows.append(
                    PublicSiteServiceArea(
                        organization=organization,
                        public_site=site,
                        name=name[:120],
                        sort_order=index,
                    )
                )
            if rows:
                PublicSiteServiceArea.objects.bulk_create(rows)
        return site


def create_upload_intent(*, organization: Organization, upload_type: str, original_name: str, mime_type: str, size_bytes: int) -> dict:
    if mime_type not in settings.UPLOAD_ALLOWED_MIME_TYPES:
        raise ValueError("Unsupported upload MIME type.")
    if size_bytes > settings.FILE_UPLOAD_MAX_MEMORY_SIZE:
        raise ValueError("Upload exceeds configured size limit.")
    object_key = f"organizations/{organization.id}/{upload_type}/{secrets.token_urlsafe(24)}-{_safe_filename(original_name)}"
    presigned = build_presigned_upload(object_key=object_key, mime_type=mime_type, size_bytes=size_bytes)
    upload = Upload.objects.create(
        organization=organization,
        type=upload_type,
        object_key=object_key,
        storage_url=f"s3://{settings.AWS_S3_BUCKET}/{object_key}",
        original_name=original_name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        metadata={"signature_expires_at": presigned.expires_at, "storage_provider": presigned.provider},
    )
    return {
        "upload_id": str(upload.id),
        "method": presigned.method,
        "url": presigned.url,
        "headers": presigned.headers,
        "expires_at": presigned.expires_at,
        "object_key": object_key,
        "storage_provider": presigned.provider,
    }


def complete_upload(
    *,
    organization: Organization,
    upload_id,
    checksum: str = "",
    size_bytes: int | None = None,
) -> Upload:
    with organization_context(organization):
        upload = Upload.objects.select_for_update().get(id=upload_id, organization=organization)
        if upload.status != Upload.Status.PENDING:
            raise ValueError("Upload is not pending.")
        if size_bytes is not None and size_bytes != upload.size_bytes:
            upload.status = Upload.Status.REJECTED
            upload.metadata = {**upload.metadata, "rejection_reason": "size_mismatch", "reported_size_bytes": size_bytes}
            upload.save(update_fields=["status", "metadata", "updated_at"])
            raise ValueError("Uploaded file size does not match the signed intent.")
        if checksum and not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
            raise ValueError("checksum must be a SHA-256 hex digest.")
        upload.status = Upload.Status.UPLOADED
        upload.checksum = checksum
        upload.completed_at = timezone.now()
        upload.metadata = {**upload.metadata, "malware_scan_status": "pending"}
        upload.save(update_fields=["status", "checksum", "completed_at", "metadata", "updated_at"])
        return upload


def _delivery_reference() -> str:
    return f"DX-{secrets.randbelow(900000) + 100000}"


def _safe_filename(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", filename.strip()).strip(".-")
    return cleaned[:120] or "upload"


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0088
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * radius_km * asin(sqrt(a))


def _status_sort(status: str) -> int:
    return {
        Delivery.Status.REQUESTED: 5,
        Delivery.Status.PENDING: 10,
        Delivery.Status.ASSIGNED: 20,
        Delivery.Status.ACCEPTED: 30,
        Delivery.Status.PICKED_UP: 40,
        Delivery.Status.IN_TRANSIT: 50,
        Delivery.Status.DELIVERED: 60,
        Delivery.Status.FAILED: 60,
        Delivery.Status.CANCELLED: 60,
    }[status]


def _enqueue_notification_after_commit(notification: Notification) -> None:
    notification_id = str(notification.id)

    def enqueue() -> None:
        from .tasks import send_notification

        send_notification.delay(notification_id)

    transaction.on_commit(enqueue)


def _broadcast_after_commit(organization_id, event_type: str, payload: dict) -> None:
    transaction.on_commit(lambda: broadcast_organization_event(organization_id, event_type, payload))


def _delivery_payload(delivery: Delivery) -> dict:
    return {
        "id": str(delivery.id),
        "reference": delivery.reference,
        "status": delivery.status,
        "customer_name": delivery.customer_name,
        "courier_id": str(delivery.courier_id) if delivery.courier_id else None,
        "scheduled_time": delivery.scheduled_time.isoformat() if delivery.scheduled_time else None,
        "completed_at": delivery.completed_at.isoformat() if delivery.completed_at else None,
    }


def _courier_payload(courier: Courier) -> dict:
    return {
        "id": str(courier.id),
        "name": courier.name,
        "status": courier.status,
        "active_delivery_count": courier.active_delivery_count,
    }


def _average_delivery_seconds(deliveries) -> int:
    durations = []
    for delivery in deliveries.only("created_at", "completed_at"):
        if delivery.completed_at and delivery.completed_at >= delivery.created_at:
            durations.append((delivery.completed_at - delivery.created_at).total_seconds())
    if not durations:
        return 0
    return int(sum(durations) / len(durations))


def _rider_efficiency(organization: Organization, start, end) -> dict:
    delivered = Delivery.objects.for_organization(organization).filter(
        status=Delivery.Status.DELIVERED,
        courier__isnull=False,
        created_at__gte=start,
        created_at__lt=end,
    )
    rows = (
        delivered.values("courier_id", "courier__name")
        .annotate(deliveries=Count("id"), revenue=Sum("delivery_fee"))
        .order_by("-deliveries", "courier__name")
    )
    return {
        "couriers": [
            {
                "courier_id": str(row["courier_id"]),
                "name": row["courier__name"],
                "deliveries": row["deliveries"],
                "revenue": str(row["revenue"] or Decimal("0")),
            }
            for row in rows
        ]
    }
