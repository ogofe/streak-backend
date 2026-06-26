from contextlib import contextmanager
from typing import Iterable

from django.db import connection

from .models import Branch, Organization


TENANT_HEADER = "HTTP_X_ORGANIZATION_ID"
SUBDOMAIN_HEADER = "HTTP_X_ORGANIZATION_SUBDOMAIN"
BRANCH_HEADER = "HTTP_X_BRANCH_ID"


class TenantRequired(Exception):
    pass


class BranchAccessDenied(Exception):
    pass


def resolve_organization(request) -> Organization | None:
    org_id = request.META.get(TENANT_HEADER) or request.headers.get("X-Organization-ID")
    subdomain = request.META.get(SUBDOMAIN_HEADER) or request.headers.get("X-Organization-Subdomain")
    host = request.get_host().split(":")[0] if hasattr(request, "get_host") else ""

    qs = Organization.objects.filter(status=Organization.Status.ACTIVE)
    if org_id:
        return qs.filter(id=org_id).first()
    if subdomain:
        return qs.filter(subdomain=subdomain).first()
    if host:
        org = qs.filter(custom_domain=host).first()
        if org:
            return org
        parts = host.split(".")
        if len(parts) > 2:
            return qs.filter(subdomain=parts[0]).first()
    return None


def set_current_organization(organization_id: str | None) -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        if organization_id:
            cursor.execute("SELECT set_config('app.current_org', %s, false)", [str(organization_id)])
        else:
            cursor.execute("SELECT set_config('app.current_org', '', false)")


@contextmanager
def organization_context(organization: Organization | str | None):
    previous = getattr(connection, "_current_organization_id", None)
    organization_id = getattr(organization, "id", organization)
    connection._current_organization_id = str(organization_id) if organization_id else None
    set_current_organization(connection._current_organization_id)
    try:
        yield
    finally:
        connection._current_organization_id = previous
        set_current_organization(previous)


def require_tenant(request) -> Organization:
    organization = getattr(request, "organization", None)
    if not organization:
        raise TenantRequired("A valid organization context is required.")
    return organization


def actor_is_owner(actor) -> bool:
    role = getattr(actor, "role", None)
    return getattr(role, "key", None) == "owner"


def resolve_branch(request, organization: Organization) -> Branch | None:
    actor = getattr(request, "actor", None)
    requested = request.META.get(BRANCH_HEADER) or request.headers.get("X-Branch-ID")
    actor_branch = getattr(actor, "branch", None)

    if actor_is_owner(actor):
        if not requested or requested == "all":
            return None
        return Branch.objects.filter(
            id=requested,
            organization=organization,
            status=Branch.Status.ACTIVE,
        ).first()

    if actor_branch and actor_branch.organization_id == organization.id:
        if requested and requested not in {"all", str(actor_branch.id)}:
            raise BranchAccessDenied("You can only access your assigned branch.")
        return actor_branch

    if requested and requested != "all":
        return Branch.objects.filter(
            id=requested,
            organization=organization,
            status=Branch.Status.ACTIVE,
        ).first()
    return Branch.objects.filter(
        organization=organization,
        status=Branch.Status.ACTIVE,
        is_default=True,
    ).first()


def bind_branch_context(request, branch: Branch | None) -> None:
    setattr(request, "branch", branch)
    django_request = getattr(request, "_request", None)
    if django_request is not None:
        setattr(django_request, "branch", branch)


def require_branch(request) -> Branch | None:
    return getattr(request, "branch", None)


def ensure_same_organization(organization: Organization, *objects: Iterable[object]) -> None:
    for obj in objects:
        if obj is None:
            continue
        obj_org_id = getattr(obj, "organization_id", None)
        if obj_org_id and str(obj_org_id) != str(organization.id):
            raise TenantRequired("Object does not belong to the active organization.")
