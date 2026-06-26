from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import authentication, exceptions

from .models import Courier, ImpersonationSession, OrganizationUser
from .security import decode_access_token, platform_profile_for_user, validate_api_key
from .tenant import BranchAccessDenied, bind_branch_context, organization_context, resolve_branch, set_current_organization


def _bind_request_context(request, **attrs):
    for name, value in attrs.items():
        setattr(request, name, value)
        django_request = getattr(request, "_request", None)
        if django_request is not None:
            setattr(django_request, name, value)


class JWTAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode("utf-8")
        if not header or not header.startswith(f"{self.keyword} "):
            return None
        raw_token = header.split(" ", 1)[1].strip()
        try:
            claims = decode_access_token(raw_token)
        except Exception as exc:
            raise exceptions.AuthenticationFailed("Invalid or expired token.") from exc

        subject_type = claims.get("typ")
        if subject_type == "organization":
            user = OrganizationUser.objects.select_related("organization", "role", "branch").filter(
                id=claims["sub"],
                status=OrganizationUser.Status.ACTIVE,
            ).first()
            if not user:
                raise exceptions.AuthenticationFailed("Organization user is inactive.")
            _bind_request_context(request, organization=user.organization, actor=user)
            try:
                bind_branch_context(request, resolve_branch(request, user.organization))
            except BranchAccessDenied as exc:
                raise exceptions.AuthenticationFailed(str(exc)) from exc
            set_current_organization(user.organization_id)
            with organization_context(user.organization):
                user.last_active_at = timezone.now()
                user.save(update_fields=["last_active_at", "updated_at"])
            set_current_organization(user.organization_id)
            return user, claims

        if subject_type == "platform":
            user = get_user_model().objects.select_related("platform_profile__role").filter(
                id=claims["sub"],
                is_active=True,
            ).first()
            if not user or not user.is_staff:
                raise exceptions.AuthenticationFailed("Platform user is inactive.")
            try:
                platform_profile_for_user(user)
            except ValueError as exc:
                raise exceptions.AuthenticationFailed(str(exc)) from exc
            _bind_request_context(request, actor=user)
            return user, claims

        if subject_type == "courier":
            courier = Courier.objects.select_related("organization", "branch").filter(
                id=claims["sub"],
            ).exclude(status=Courier.Status.INACTIVE).first()
            if not courier:
                raise exceptions.AuthenticationFailed("Courier is inactive.")
            if str(courier.organization_id) != str(claims.get("organization_id")):
                raise exceptions.AuthenticationFailed("Courier organization mismatch.")
            _bind_request_context(request, organization=courier.organization, actor=courier)
            bind_branch_context(request, courier.branch)
            set_current_organization(courier.organization_id)
            return courier, claims

        if subject_type == "impersonation":
            session = ImpersonationSession.objects.select_related(
                "platform_user__user",
                "platform_user__role",
                "organization",
            ).filter(
                id=claims.get("impersonation_session_id"),
                status=ImpersonationSession.Status.ACTIVE,
            ).first()
            if not session or not session.is_active:
                raise exceptions.AuthenticationFailed("Impersonation session is inactive or expired.")
            if str(session.organization_id) != str(claims.get("organization_id")):
                raise exceptions.AuthenticationFailed("Impersonation organization mismatch.")
            _bind_request_context(
                request,
                actor=session.platform_user.user,
                organization=session.organization,
                impersonation_session=session,
            )
            bind_branch_context(request, resolve_branch(request, session.organization))
            set_current_organization(session.organization_id)
            return session.platform_user.user, claims

        raise exceptions.AuthenticationFailed("Unsupported token subject.")


class APIKeyAuthentication(authentication.BaseAuthentication):
    keyword = "Api-Key"

    def authenticate(self, request):
        raw_key = request.headers.get("X-API-Key")
        if not raw_key:
            header = authentication.get_authorization_header(request).decode("utf-8")
            if header.startswith(f"{self.keyword} "):
                raw_key = header.split(" ", 1)[1].strip()
        if not raw_key:
            return None
        key = validate_api_key(raw_key)
        if not key:
            raise exceptions.AuthenticationFailed("Invalid API key.")
        _bind_request_context(request, organization=key.organization, actor=key)
        bind_branch_context(request, resolve_branch(request, key.organization))
        set_current_organization(key.organization_id)
        return key, {"typ": "api_key", "scopes": key.scopes}
