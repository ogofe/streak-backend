from django.contrib.auth import get_user_model

from .models import APIKey, AuditLog, OrganizationUser, PlatformUser


def write_audit(
    *,
    action: str,
    request=None,
    organization=None,
    actor=None,
    metadata: dict | None = None,
) -> AuditLog:
    actor = actor or getattr(request, "actor", None)
    organization = organization or getattr(request, "organization", None)
    actor_type = AuditLog.ActorType.SYSTEM
    actor_id = None
    if isinstance(actor, OrganizationUser):
        actor_type = AuditLog.ActorType.ORGANIZATION
        actor_id = actor.id
        organization = organization or actor.organization
    elif isinstance(actor, PlatformUser):
        actor_type = AuditLog.ActorType.PLATFORM
        actor_id = str(actor.id)
    elif isinstance(actor, get_user_model()):
        actor_type = AuditLog.ActorType.PLATFORM
        actor_id = str(actor.id)
    elif isinstance(actor, APIKey):
        actor_type = AuditLog.ActorType.API_KEY
        actor_id = actor.id
        organization = organization or actor.organization

    return AuditLog.objects.create(
        organization=organization,
        actor_type=actor_type,
        actor_id=actor_id or "",
        action=action,
        ip_address=_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "") if request else "",
        metadata=metadata or {},
    )


def _ip(request) -> str | None:
    if not request:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
