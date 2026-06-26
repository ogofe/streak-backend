from django.contrib.auth import get_user_model
from rest_framework.permissions import BasePermission

from .models import APIKey, Courier, OrganizationUser
from .security import platform_profile_for_user


class IsAuthenticatedActor(BasePermission):
    def has_permission(self, request, view):
        return bool(getattr(request, "actor", None) or getattr(request, "user", None))


class OrganizationScopedPermission(BasePermission):
    required_permission: str | None = None

    def has_permission(self, request, view):
        actor = getattr(request, "actor", None)
        if isinstance(actor, APIKey):
            required = getattr(view, "required_permission", self.required_permission)
            return not required or required in actor.scopes
        if isinstance(actor, OrganizationUser):
            required = getattr(view, "required_permission", self.required_permission)
            return not required or actor.role.permissions.filter(code=required).exists()
        if isinstance(actor, get_user_model()):
            session = getattr(request, "impersonation_session", None)
            if not session or not session.is_active:
                return False
            required = getattr(view, "required_permission", self.required_permission)
            return not required or required in session.allowed_permissions
        return False


class CourierTrackingPermission(BasePermission):
    def has_permission(self, request, view):
        actor = getattr(request, "actor", None)
        if isinstance(actor, Courier):
            return getattr(view, "action", None) == "create"
        return OrganizationScopedPermission().has_permission(request, view)


class PlatformPermission(BasePermission):
    required_permission: str | None = None

    def has_permission(self, request, view):
        actor = getattr(request, "actor", None)
        required = getattr(view, "required_permission", self.required_permission)
        if not isinstance(actor, get_user_model()):
            return False
        try:
            profile = platform_profile_for_user(actor)
        except ValueError:
            return False
        return not required or profile.role.permissions.filter(code=required).exists()
