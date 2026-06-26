import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .audit import write_audit
from .models import (
    AnalyticsEvent,
    AnalyticsSnapshot,
    APIKey,
    Branch,
    Courier,
    CourierMessage,
    CustomDomain,
    Customer,
    Delivery,
    ImpersonationSession,
    Notification,
    Organization,
    OrganizationPermission,
    OrganizationRole,
    OrganizationUser,
    PlatformUser,
    PublicSite,
    PublicSiteBlock,
    PublicSitePage,
    TrackingLog,
    Upload,
)
from .permissions import CourierTrackingPermission, OrganizationScopedPermission, PlatformPermission
from .observability import operational_metrics, readiness_checks
from .realtime import broadcast_organization_event
from .security import (
    confirm_mfa,
    create_mfa_setup,
    disable_mfa,
    hash_password,
    is_login_locked,
    issue_courier_pair,
    issue_org_user_pair,
    issue_platform_user_pair,
    make_api_key,
    mfa_required_for_user,
    platform_profile_for_user,
    record_login_attempt,
    rotate_refresh_token,
    create_impersonation_access_token,
    verify_password,
    verify_user_totp,
)
from .serializers import (
    AnalyticsEventSerializer,
    AnalyticsSnapshotSerializer,
    APIKeyCreateSerializer,
    APIKeySerializer,
    AssignCourierSerializer,
    BranchSerializer,
    BusinessSignupSerializer,
    CourierLoginSerializer,
    CourierMessageCreateSerializer,
    CourierMessageSerializer,
    CourierProfileUpdateSerializer,
    CourierSerializer,
    CustomDomainSerializer,
    CustomerSerializer,
    DeliveryHeatmapQuerySerializer,
    DeliveryCreateSerializer,
    DeliverySerializer,
    NotificationSerializer,
    OrganizationLoginSerializer,
    PlatformLoginSerializer,
    MFAVerifySerializer,
    ImpersonationStartSerializer,
    NearestCouriersQuerySerializer,
    OrganizationRoleSerializer,
    OrganizationSerializer,
    OrganizationUserSerializer,
    PublicDeliveryRequestSerializer,
    PublicSiteBlockSerializer,
    PublicSitePageSerializer,
    PublicSiteSerializer,
    RefreshSerializer,
    TrackingCreateSerializer,
    TrackingLogSerializer,
    TransitionDeliverySerializer,
    UploadCompleteSerializer,
    UploadIntentSerializer,
    UploadSerializer,
)
from .services import (
    DeliveryStateError,
    aggregate_analytics_snapshot,
    analytics_snapshot_series,
    assign_courier,
    create_delivery,
    create_domain_verification,
    create_upload_intent,
    get_or_create_public_site,
    complete_upload,
    delivery_zone_heatmap,
    nearest_couriers,
    overview_metrics,
    record_tracking,
    transition_delivery,
    update_public_site,
)
from .tenant import actor_is_owner, require_branch, require_tenant


DEFAULT_ORGANIZATION_PERMISSIONS = [
    "view_overview",
    "view_orders",
    "manage_orders",
    "view_fleet",
    "manage_fleet",
    "view_staff",
    "manage_staff",
    "view_customers",
    "manage_customers",
    "view_analytics",
    "view_settings",
    "manage_settings",
]


def _normalize_phone(value: str) -> str:
    return "".join(str(value).split())


def _normalize_email(value: str) -> str:
    return str(value).strip().lower()


def _initials(value: str) -> str:
    return "".join(part[0] for part in str(value).split() if part)[:2].upper()


def _broadcast_courier_message(message: CourierMessage) -> None:
    contact_user_id = str(message.contact_user_id) if message.contact_user_id else None
    broadcast_organization_event(
        message.organization_id,
        "courier.message_created",
        {
            "chat_id": f"courier:{message.courier_id}:manager:{contact_user_id or 'unassigned'}",
            "courier_id": str(message.courier_id),
            "contact_user_id": contact_user_id,
            "branch_id": str(message.branch_id) if message.branch_id else None,
            "message": CourierMessageSerializer(message).data,
        },
    )


def _broadcast_courier_messages_read(organization_id, courier_id, contact_user_id, message_ids) -> None:
    if not message_ids:
        return
    contact_user_id = str(contact_user_id) if contact_user_id else None
    broadcast_organization_event(
        organization_id,
        "courier.messages_read",
        {
            "chat_id": f"courier:{courier_id}:manager:{contact_user_id or 'unassigned'}",
            "courier_id": str(courier_id),
            "contact_user_id": contact_user_id,
            "message_ids": [str(message_id) for message_id in message_ids],
            "read_at": timezone.now().isoformat(),
        },
    )


def _manager_contact_queryset(organization: Organization, branch: Branch | None = None):
    qs = OrganizationUser.objects.select_related("role", "branch").filter(
        organization=organization,
        status=OrganizationUser.Status.ACTIVE,
        role__permissions__code="manage_fleet",
    ).distinct()
    if branch:
        qs = qs.filter(Q(branch=branch) | Q(branch__isnull=True) | Q(role__key="owner"))
    return qs.order_by("name")


def _serialize_courier_chat_contact(user: OrganizationUser, courier: Courier, actor=None) -> dict:
    last_message = (
        CourierMessage.objects.filter(
            organization=courier.organization,
            courier=courier,
            contact_user=user,
        )
        .order_by("-created_at")
        .first()
    )
    return {
        "id": str(user.id),
        "name": user.name,
        "initials": user.initials,
        "email": user.email,
        "branch_name": user.branch.name if user.branch else None,
        "is_self": bool(actor and str(getattr(actor, "id", "")) == str(user.id)),
        "last_message": last_message.body if last_message else "",
        "last_message_at": last_message.created_at if last_message else None,
    }


def _find_courier_chat_contact(organization: Organization, contact_user_id, branch: Branch | None = None):
    if not contact_user_id:
        return None
    return _manager_contact_queryset(organization, branch).filter(id=contact_user_id).first()


def resolve_public_organization(*, host: str = "", tenant: str = ""):
    lookup = tenant or ""
    host = (host or "").lower().strip(".")
    if not lookup and host:
        custom_domain = CustomDomain.objects.select_related("organization", "organization__public_site").filter(
            domain=host,
            status=CustomDomain.Status.VERIFIED,
            organization__status=Organization.Status.ACTIVE,
        ).first()
        if custom_domain:
            return custom_domain.organization
        labels = host.split(".")
        if len(labels) > 1:
            lookup = labels[0]
    if not lookup:
        return None
    return Organization.objects.select_related("public_site").filter(
        Q(subdomain=lookup) | Q(slug=lookup),
        status=Organization.Status.ACTIVE,
    ).first()


def public_site_payload(organization: Organization, site: PublicSite) -> dict:
    brand = organization.branding or {}
    return {
        "organization": {
            "id": str(organization.id),
            "name": organization.name,
            "subdomain": organization.subdomain,
            "brand_color": brand.get("brand_color") or "#16a34a",
            "initials": brand.get("initials") or "".join(part[0] for part in organization.name.split() if part)[:2].upper(),
        },
        "site": PublicSiteSerializer(site).data,
    }


def normalize_path(value: str) -> str:
    path = "/" + str(value or "/").split("?", 1)[0].strip().strip("/")
    return "/" if path == "/" else path.rstrip("/")


class PublicSiteDirectoryView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        sites = (
            PublicSite.objects.select_related("organization")
            .prefetch_related("service_areas")
            .filter(enabled=True, organization__status=Organization.Status.ACTIVE)
            .order_by("organization__name")
        )
        return Response([public_site_payload(site.organization, site) for site in sites])


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def health(request):
    return Response({"status": "ok", "time": timezone.now()})


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def readiness(request):
    data = readiness_checks()
    status_code = status.HTTP_200_OK if data["status"] == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE
    return Response(data, status=status_code)


class PlatformMetricsView(APIView):
    permission_classes = [PlatformPermission]
    required_permission = "view_platform_metrics"

    def get(self, request):
        return Response(operational_metrics())


class OrganizationLoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = OrganizationLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization_ref = serializer.validated_data["organization"]
        lookup = Q(slug=organization_ref) | Q(subdomain=organization_ref)
        try:
            lookup |= Q(id=uuid.UUID(organization_ref))
        except ValueError:
            pass
        organization = Organization.objects.filter(lookup, status=Organization.Status.ACTIVE).first()
        email = serializer.validated_data["email"]
        if is_login_locked(subject_type="organization", email=email, organization=organization, request=request):
            write_audit(action="auth.login_locked", organization=organization, request=request, metadata={"email": email})
            return Response({"detail": "Too many failed login attempts. Try again later."}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        user = None
        if organization:
            user = OrganizationUser.objects.select_related("organization", "role", "branch").filter(
                organization=organization,
                email=email,
                status=OrganizationUser.Status.ACTIVE,
            ).first()
        if not user or not verify_password(serializer.validated_data["password"], user.password_hash):
            record_login_attempt(subject_type="organization", email=email, organization=organization, request=request, success=False, failure_reason="invalid_credentials")
            write_audit(action="auth.login_failed", organization=organization, request=request, metadata={"email": email})
            return Response({"detail": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED)
        if mfa_required_for_user(user):
            code = serializer.validated_data.get("mfa_code", "")
            if not code:
                record_login_attempt(subject_type="organization", email=email, organization=organization, request=request, success=False, mfa_required=True, failure_reason="mfa_required", count_for_lockout=False)
                return Response({"mfa_required": True, "detail": "MFA code required."}, status=status.HTTP_202_ACCEPTED)
            if not verify_user_totp(user, code):
                record_login_attempt(subject_type="organization", email=email, organization=organization, request=request, success=False, mfa_required=True, failure_reason="invalid_mfa")
                write_audit(action="auth.mfa_failed", organization=organization, actor=user, request=request)
                return Response({"detail": "Invalid MFA code."}, status=status.HTTP_401_UNAUTHORIZED)
        user.last_login = timezone.now()
        user.last_active_at = user.last_login
        user.save(update_fields=["last_login", "last_active_at", "updated_at"])
        record_login_attempt(subject_type="organization", email=email, organization=organization, request=request, success=True, mfa_required=user.mfa_enabled)
        pair = issue_org_user_pair(user, request=request)
        write_audit(action="auth.login_success", organization=organization, actor=user, request=request)
        return Response(
            {
                "access": pair.access,
                "refresh": pair.refresh,
                "organization": OrganizationSerializer(organization).data,
                "user": OrganizationUserSerializer(user).data,
            }
        )


class BusinessSignupView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = BusinessSignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with transaction.atomic():
            organization = Organization.objects.create(
                name=data["company_name"],
                slug=data["subdomain"],
                subdomain=data["subdomain"],
                subscription_plan="Starter",
                branding={
                    "brand_color": data["brand_color"],
                    "initials": _initials(data["company_name"]),
                },
                metadata={
                    "settings": {
                        "currency": data["currency"],
                    },
                    "onboarding": {
                        "company_size": data.get("company_size", ""),
                        "branch_count": data.get("branch_count", 1),
                        "country": data.get("country", ""),
                        "currency": data["currency"],
                        "location": data.get("location", ""),
                    },
                },
            )
            permissions = [
                OrganizationPermission.objects.create(
                    organization=organization,
                    code=code,
                    description=code.replace("_", " ").title(),
                )
                for code in DEFAULT_ORGANIZATION_PERMISSIONS
            ]
            owner_role = OrganizationRole.objects.create(
                organization=organization,
                key="owner",
                label="Owner",
                description="Full access to the business workspace.",
            )
            owner_role.permissions.set(permissions)
            owner = OrganizationUser.objects.create(
                organization=organization,
                name=data["owner_name"],
                email=data["owner_email"],
                initials=_initials(data["owner_name"]),
                role=owner_role,
                status=OrganizationUser.Status.ACTIVE,
                password_hash=hash_password(data["owner_password"]),
                last_login=timezone.now(),
                last_active_at=timezone.now(),
            )
            site = get_or_create_public_site(organization)
            site.enabled = data.get("enable_public_site", False)
            site.headline = data.get("site_headline") or f"Delivery requests for {organization.name}"
            site.description = data.get("site_description") or "Request a pickup, track active deliveries, and stay connected with the dispatch team."
            site.contact_email = data["owner_email"]
            site.save(update_fields=["enabled", "headline", "description", "contact_email", "updated_at"])

        pair = issue_org_user_pair(owner, request=request)
        write_audit(action="auth.business_signup", organization=organization, actor=owner, request=request)
        return Response(
            {
                "access": pair.access,
                "refresh": pair.refresh,
                "organization": OrganizationSerializer(organization).data,
                "user": OrganizationUserSerializer(owner).data,
            },
            status=status.HTTP_201_CREATED,
        )


class CourierLoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CourierLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization_ref = serializer.validated_data["organization"]
        lookup = Q(slug=organization_ref) | Q(subdomain=organization_ref)
        try:
            lookup |= Q(id=uuid.UUID(organization_ref))
        except ValueError:
            pass
        organization = Organization.objects.filter(lookup, status=Organization.Status.ACTIVE).first()
        identifier = serializer.validated_data["identifier"]
        normalized_email = _normalize_email(identifier)
        normalized_phone = _normalize_phone(identifier)
        courier = None
        if organization:
            courier = Courier.objects.select_related("organization", "branch").filter(
                organization=organization,
            ).filter(
                Q(email__iexact=normalized_email) | Q(phone=normalized_phone),
            ).exclude(status=Courier.Status.INACTIVE).first()
        if not courier or not courier.password_hash or not verify_password(serializer.validated_data["password"], courier.password_hash):
            write_audit(
                action="courier_auth.login_failed",
                organization=organization,
                request=request,
                metadata={"identifier": identifier},
            )
            return Response({"detail": "Invalid courier credentials."}, status=status.HTTP_401_UNAUTHORIZED)

        courier.last_login = timezone.now()
        if courier.status == Courier.Status.OFFLINE:
            courier.status = Courier.Status.AVAILABLE
            courier.save(update_fields=["last_login", "status", "updated_at"])
        else:
            courier.save(update_fields=["last_login", "updated_at"])
        pair = issue_courier_pair(courier, request=request)
        write_audit(action="courier_auth.login_success", organization=organization, request=request, metadata={"courier_id": str(courier.id)})
        return Response(
            {
                "access": pair.access,
                "refresh": pair.refresh,
                "organization": OrganizationSerializer(organization).data,
                "courier": CourierSerializer(courier).data,
            }
        )


class PlatformLoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PlatformLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        if is_login_locked(subject_type="platform", email=email, request=request):
            write_audit(action="platform_auth.login_locked", request=request, metadata={"email": email})
            return Response({"detail": "Too many failed login attempts. Try again later."}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        user = get_user_model().objects.select_related("platform_profile__role").filter(
            email__iexact=email,
            is_active=True,
            is_staff=True,
        ).first()
        profile = None
        if user:
            try:
                profile = platform_profile_for_user(user)
            except ValueError:
                profile = None
        if not user or not profile or not user.check_password(serializer.validated_data["password"]):
            record_login_attempt(subject_type="platform", email=email, request=request, success=False, failure_reason="invalid_credentials")
            write_audit(action="platform_auth.login_failed", request=request, metadata={"email": email})
            return Response({"detail": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED)
        if mfa_required_for_user(user):
            code = serializer.validated_data.get("mfa_code", "")
            if not code:
                record_login_attempt(subject_type="platform", email=email, request=request, success=False, mfa_required=True, failure_reason="mfa_required", count_for_lockout=False)
                return Response({"mfa_required": True, "detail": "MFA code required."}, status=status.HTTP_202_ACCEPTED)
            if not verify_user_totp(user, code):
                record_login_attempt(subject_type="platform", email=email, request=request, success=False, mfa_required=True, failure_reason="invalid_mfa")
                write_audit(action="platform_auth.mfa_failed", actor=user, request=request)
                return Response({"detail": "Invalid MFA code."}, status=status.HTTP_401_UNAUTHORIZED)
        user.last_login = timezone.now()
        user.save(update_fields=["last_login"])
        record_login_attempt(subject_type="platform", email=email, request=request, success=True, mfa_required=profile.mfa_enabled)
        pair = issue_platform_user_pair(user, request=request)
        write_audit(action="platform_auth.login_success", actor=user, request=request)
        return Response(
            {
                "access": pair.access,
                "refresh": pair.refresh,
                "user": {"id": str(user.id), "email": user.email, "name": profile.display_name, "role": profile.role.key},
            }
        )


class RefreshTokenView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            pair = rotate_refresh_token(serializer.validated_data["refresh"])
        except Exception:
            return Response({"detail": "Invalid refresh token."}, status=status.HTTP_401_UNAUTHORIZED)
        return Response({"access": pair.access, "refresh": pair.refresh})


class AccountProfileView(APIView):
    def get(self, request):
        actor = getattr(request, "actor", None)
        if not isinstance(actor, OrganizationUser):
            return Response({"detail": "Account settings are only available for organization users."}, status=status.HTTP_403_FORBIDDEN)
        return Response(OrganizationUserSerializer(actor).data)

    def patch(self, request):
        actor = getattr(request, "actor", None)
        if not isinstance(actor, OrganizationUser):
            return Response({"detail": "Account settings are only available for organization users."}, status=status.HTTP_403_FORBIDDEN)

        name = str(request.data.get("name", actor.name)).strip()
        email = str(request.data.get("email", actor.email)).strip().lower()
        if not name:
            return Response({"name": "Name is required."}, status=status.HTTP_400_BAD_REQUEST)
        if not email:
            return Response({"email": "Email is required."}, status=status.HTTP_400_BAD_REQUEST)
        if OrganizationUser.objects.filter(organization=actor.organization, email=email).exclude(id=actor.id).exists():
            return Response({"email": "Another user in this organization already uses this email."}, status=status.HTTP_400_BAD_REQUEST)

        actor.name = name
        actor.email = email
        actor.initials = "".join(part[0] for part in name.split() if part)[:2].upper()
        actor.save(update_fields=["name", "email", "initials", "updated_at"])
        write_audit(action="account.profile_updated", organization=actor.organization, actor=actor, request=request)
        return Response(OrganizationUserSerializer(actor).data)


class AccountPasswordView(APIView):
    def post(self, request):
        actor = getattr(request, "actor", None)
        if not isinstance(actor, OrganizationUser):
            return Response({"detail": "Password changes are only available for organization users."}, status=status.HTTP_403_FORBIDDEN)

        current_password = request.data.get("current_password", "")
        new_password = request.data.get("new_password", "")
        if not verify_password(current_password, actor.password_hash):
            return Response({"current_password": "Current password is incorrect."}, status=status.HTTP_400_BAD_REQUEST)
        if len(str(new_password)) < 8:
            return Response({"new_password": "Password must be at least 8 characters."}, status=status.HTTP_400_BAD_REQUEST)

        actor.password_hash = hash_password(new_password)
        actor.save(update_fields=["password_hash", "updated_at"])
        write_audit(action="account.password_changed", organization=actor.organization, actor=actor, request=request)
        return Response({"ok": True})


class MFASetupView(APIView):
    def post(self, request):
        actor = getattr(request, "actor", None)
        if not _is_user_account(actor):
            return Response({"detail": "MFA is only available for user accounts."}, status=status.HTTP_403_FORBIDDEN)
        setup = create_mfa_setup(actor)
        write_audit(action="auth.mfa_setup_started", request=request, actor=actor)
        return Response(setup, status=status.HTTP_201_CREATED)


class MFAVerifyView(APIView):
    def post(self, request):
        actor = getattr(request, "actor", None)
        if not _is_user_account(actor):
            return Response({"detail": "MFA is only available for user accounts."}, status=status.HTTP_403_FORBIDDEN)
        serializer = MFAVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not confirm_mfa(actor, serializer.validated_data["code"]):
            write_audit(action="auth.mfa_setup_failed", request=request, actor=actor)
            return Response({"detail": "Invalid MFA code."}, status=status.HTTP_400_BAD_REQUEST)
        write_audit(action="auth.mfa_enabled", request=request, actor=actor)
        return Response({"mfa_enabled": True})


class MFADisableView(APIView):
    def post(self, request):
        actor = getattr(request, "actor", None)
        if not _is_user_account(actor):
            return Response({"detail": "MFA is only available for user accounts."}, status=status.HTTP_403_FORBIDDEN)
        serializer = MFAVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if mfa_required_for_user(actor) and not verify_user_totp(actor, serializer.validated_data["code"]):
            write_audit(action="auth.mfa_disable_failed", request=request, actor=actor)
            return Response({"detail": "Invalid MFA code."}, status=status.HTTP_400_BAD_REQUEST)
        disable_mfa(actor)
        write_audit(action="auth.mfa_disabled", request=request, actor=actor)
        return Response({"mfa_enabled": False})


class ImpersonationStartView(APIView):
    permission_classes = [PlatformPermission]
    required_permission = "impersonate_tenant"

    def post(self, request):
        actor = getattr(request, "actor", None)
        if not _is_platform_actor(actor):
            return Response({"detail": "Platform user required."}, status=status.HTTP_403_FORBIDDEN)
        serializer = ImpersonationStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = Organization.objects.filter(
            id=serializer.validated_data["organization_id"],
            status=Organization.Status.ACTIVE,
        ).first()
        if not organization:
            return Response({"detail": "Active organization not found."}, status=status.HTTP_404_NOT_FOUND)
        requested = serializer.validated_data.get("allowed_permissions") or [
            "view_overview",
            "view_orders",
            "view_fleet",
            "view_customers",
            "view_analytics",
            "view_settings",
        ]
        allowed_permissions = [permission for permission in requested if permission.startswith("view_")]
        profile = platform_profile_for_user(actor)
        session = ImpersonationSession.objects.create(
            platform_user=profile,
            organization=organization,
            reason=serializer.validated_data["reason"],
            allowed_permissions=allowed_permissions,
            expires_at=timezone.now() + timedelta(minutes=serializer.validated_data.get("duration_minutes", 30)),
        )
        token = create_impersonation_access_token(session)
        write_audit(
            action="impersonation.started",
            organization=organization,
            actor=actor,
            request=request,
            metadata={"impersonation_session_id": str(session.id), "allowed_permissions": allowed_permissions},
        )
        return Response(
            {
                "access": token,
                "session": {
                    "id": str(session.id),
                    "organization_id": str(organization.id),
                    "expires_at": session.expires_at,
                    "allowed_permissions": allowed_permissions,
                },
            },
            status=status.HTTP_201_CREATED,
        )


class ImpersonationEndView(APIView):
    permission_classes = [PlatformPermission]
    required_permission = "impersonate_tenant"

    def post(self, request, session_id):
        actor = getattr(request, "actor", None)
        profile = platform_profile_for_user(actor) if _is_platform_actor(actor) else None
        session = ImpersonationSession.objects.select_related("organization").filter(id=session_id, platform_user=profile).first()
        if not session:
            return Response({"detail": "Impersonation session not found."}, status=status.HTTP_404_NOT_FOUND)
        session.status = ImpersonationSession.Status.ENDED
        session.ended_at = timezone.now()
        session.save(update_fields=["status", "ended_at", "updated_at"])
        write_audit(
            action="impersonation.ended",
            organization=session.organization,
            actor=actor,
            request=request,
            metadata={"impersonation_session_id": str(session.id)},
        )
        return Response({"status": session.status, "ended_at": session.ended_at})


class TenantViewSet(viewsets.ModelViewSet):
    permission_classes = [OrganizationScopedPermission]
    required_permission = None

    def get_organization(self):
        return require_tenant(self.request)

    def get_queryset(self):
        qs = self.queryset.for_organization(self.get_organization())
        branch = require_branch(self.request)
        if branch and hasattr(qs.model, "branch_id"):
            qs = qs.filter(branch=branch)
        return qs

    def perform_create(self, serializer):
        kwargs = {"organization": self.get_organization()}
        branch = require_branch(self.request)
        if branch and hasattr(serializer.Meta.model, "branch_id"):
            kwargs["branch"] = branch
        serializer.save(**kwargs)


class OrganizationViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Organization.objects.filter(status=Organization.Status.ACTIVE).order_by("name")
    serializer_class = OrganizationSerializer
    permission_classes = [AllowAny]
    required_permission = "manage_settings"

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [AllowAny()]
        return [OrganizationScopedPermission()]

    def get_queryset(self):
        if self.action in {"update", "partial_update"}:
            organization = getattr(self.request, "organization", None)
            if organization:
                return Organization.objects.filter(id=organization.id, status=Organization.Status.ACTIVE)
            return Organization.objects.none()
        return super().get_queryset()

    def partial_update(self, request, *args, **kwargs):
        organization = self.get_object()
        allowed = {
            "name": request.data.get("name", organization.name),
            "custom_domain": request.data.get("custom_domain", organization.custom_domain),
            "branding": {
                **(organization.branding or {}),
                **(request.data.get("branding") or {}),
            },
            "metadata": {
                **(organization.metadata or {}),
                **(request.data.get("metadata") or {}),
            },
        }
        serializer = self.get_serializer(organization, data=allowed, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        write_audit(action="organization.settings_updated", organization=organization, actor=getattr(request, "actor", None), request=request)
        return Response(serializer.data)


class PlatformOrganizationViewSet(viewsets.ModelViewSet):
    queryset = Organization.objects.all().order_by("name")
    serializer_class = OrganizationSerializer
    permission_classes = [PlatformPermission]
    required_permission = "manage_organizations"

    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        if not _platform_has_permission(request, "suspend_organization"):
            return Response({"detail": "Missing suspend_organization permission."}, status=status.HTTP_403_FORBIDDEN)
        organization = self.get_object()
        organization.status = Organization.Status.SUSPENDED
        organization.save(update_fields=["status", "updated_at"])
        write_audit(action="organization.suspended", organization=organization, actor=getattr(request, "actor", None), request=request)
        return Response(OrganizationSerializer(organization).data)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        organization = self.get_object()
        organization.status = Organization.Status.ACTIVE
        organization.save(update_fields=["status", "updated_at"])
        write_audit(action="organization.activated", organization=organization, actor=getattr(request, "actor", None), request=request)
        return Response(OrganizationSerializer(organization).data)


class RoleViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = OrganizationRoleSerializer
    permission_classes = [OrganizationScopedPermission]
    required_permission = "manage_staff"

    def get_queryset(self):
        return require_tenant(self.request).organizationrole_set.prefetch_related("permissions").all()


class GoogleMapsConfigView(APIView):
    permission_classes = [OrganizationScopedPermission]
    required_permission = "view_fleet"

    def get(self, request):
        return Response(
            {
                "api_key": settings.GOOGLE_MAPS_API_KEY,
                "map_id": settings.GOOGLE_MAPS_MAP_ID,
                "configured": bool(settings.GOOGLE_MAPS_API_KEY),
            }
        )


class PublicSiteView(APIView):
    permission_classes = [OrganizationScopedPermission]
    required_permission = "manage_settings"

    def get(self, request):
        organization = require_tenant(request)
        site = get_or_create_public_site(organization)
        return Response(PublicSiteSerializer(site, context={"request": request}).data)

    def patch(self, request):
        organization = require_tenant(request)
        serializer = PublicSiteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()
        service_area_names = data.pop("service_area_names", None)
        site = update_public_site(
            organization=organization,
            service_area_names=service_area_names,
            **data,
        )
        write_audit(action="public_site.updated", organization=organization, actor=getattr(request, "actor", None), request=request)
        return Response(PublicSiteSerializer(site, context={"request": request}).data)


class PublicSitePageViewSet(TenantViewSet):
    serializer_class = PublicSitePageSerializer
    queryset = PublicSitePage.objects.select_related("public_site").prefetch_related("blocks")
    required_permission = "manage_settings"

    def get_queryset(self):
        site = get_or_create_public_site(self.get_organization())
        return super().get_queryset().filter(public_site=site)

    def perform_create(self, serializer):
        organization = self.get_organization()
        site = get_or_create_public_site(organization)
        serializer.save(organization=organization, public_site=site)

    def perform_update(self, serializer):
        page = serializer.save()
        if page.status == PublicSitePage.Status.PUBLISHED and page.published_at is None:
            page.published_at = timezone.now()
            page.save(update_fields=["published_at", "updated_at"])


class PublicSiteBlockViewSet(TenantViewSet):
    serializer_class = PublicSiteBlockSerializer
    queryset = PublicSiteBlock.objects.select_related("public_site", "page")
    required_permission = "manage_settings"

    def get_queryset(self):
        site = get_or_create_public_site(self.get_organization())
        qs = super().get_queryset().filter(public_site=site)
        page_id = self.request.query_params.get("page_id")
        if page_id:
            qs = qs.filter(page_id=page_id)
        return qs

    def perform_create(self, serializer):
        organization = self.get_organization()
        site = get_or_create_public_site(organization)
        page = serializer.validated_data["page"]
        if page.organization_id != organization.id or page.public_site_id != site.id:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"page": "Page does not belong to this public site."})
        serializer.save(organization=organization, public_site=site)

    def perform_update(self, serializer):
        page = serializer.validated_data.get("page", serializer.instance.page)
        if page.organization_id != self.get_organization().id:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"page": "Page does not belong to this organization."})
        serializer.save()


class PublicSiteResolveView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        host = str(request.query_params.get("host") or "").split(":")[0].lower()
        tenant = str(request.query_params.get("tenant") or "").strip().lower()
        organization = resolve_public_organization(host=host, tenant=tenant)
        if not organization:
            return Response({"detail": "Public site not found."}, status=status.HTTP_404_NOT_FOUND)
        site = getattr(organization, "public_site", None)
        if not site or not site.enabled:
            return Response({"detail": "Public site is not enabled."}, status=status.HTTP_404_NOT_FOUND)
        return Response(public_site_payload(organization, site))


class PublicSitePageResolveView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        host = str(request.query_params.get("host") or "").split(":")[0].lower()
        tenant = str(request.query_params.get("tenant") or "").strip().lower()
        path = str(request.query_params.get("path") or "/")
        organization = resolve_public_organization(host=host, tenant=tenant)
        if not organization:
            return Response({"detail": "Public site not found."}, status=status.HTTP_404_NOT_FOUND)
        site = getattr(organization, "public_site", None)
        if not site or not site.enabled:
            return Response({"detail": "Public site is not enabled."}, status=status.HTTP_404_NOT_FOUND)
        slug = normalize_path(path)
        page = PublicSitePage.objects.prefetch_related("blocks").filter(
            organization=organization,
            public_site=site,
            slug=slug,
            status=PublicSitePage.Status.PUBLISHED,
        ).first()
        if not page:
            return Response({"detail": "Page not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({**public_site_payload(organization, site), "page": PublicSitePageSerializer(page).data})


class PublicDeliveryRequestView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        host = str(request.data.get("host") or request.headers.get("host") or "").split(":")[0].lower()
        tenant = str(request.data.get("tenant") or "").strip().lower()
        organization = resolve_public_organization(host=host, tenant=tenant)
        if not organization:
            return Response({"detail": "Public site not found."}, status=status.HTTP_404_NOT_FOUND)
        site = getattr(organization, "public_site", None)
        if not site or not site.enabled or not site.request_form_enabled:
            return Response({"detail": "Delivery requests are not enabled for this business."}, status=status.HTTP_403_FORBIDDEN)
        serializer = PublicDeliveryRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()
        coordinates = {
            "pickup": {
                "latitude": str(data.pop("pickup_latitude", "")) or None,
                "longitude": str(data.pop("pickup_longitude", "")) or None,
            },
            "delivery": {
                "latitude": str(data.pop("delivery_latitude", "")) or None,
                "longitude": str(data.pop("delivery_longitude", "")) or None,
            },
        }
        delivery = create_delivery(
            organization=organization,
            customer=get_or_create_public_customer(organization, data),
            status=Delivery.Status.REQUESTED,
            source="public_site",
            source_label="Public website",
            delivery_fee=0,
            **data,
            metadata={"customer_submitted": True, "coordinates": coordinates},
        )
        return Response(DeliverySerializer(delivery).data, status=status.HTTP_201_CREATED)


def get_or_create_public_customer(organization: Organization, data: dict) -> Customer:
    name = str(data.get("customer_name") or "").strip()
    phone = str(data.get("customer_phone") or "").strip()
    zone = str(data.get("zone") or "").strip()
    customer = None
    if phone:
        customer = Customer.objects.filter(organization=organization, phone=phone).first()
    if customer:
        update_fields = ["updated_at"]
        if name and customer.name != name:
            customer.name = name
            customer.initials = "".join(part[0] for part in name.split() if part)[:2].upper()
            update_fields.extend(["name", "initials"])
        if zone and customer.zone != zone:
            customer.zone = zone
            update_fields.append("zone")
        if len(update_fields) > 1:
            customer.save(update_fields=update_fields)
        return customer
    return Customer.objects.create(
        organization=organization,
        name=name,
        phone=phone,
        initials="".join(part[0] for part in name.split() if part)[:2].upper(),
        zone=zone,
        status=Customer.Status.NEW,
    )


class PublicDeliveryTrackView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        host = str(request.query_params.get("host") or request.headers.get("host") or "").split(":")[0].lower()
        tenant = str(request.query_params.get("tenant") or "").strip().lower()
        reference = str(request.query_params.get("reference") or "").strip()
        phone = str(request.query_params.get("phone") or "").strip()
        organization = resolve_public_organization(host=host, tenant=tenant)
        if not organization:
            return Response({"detail": "Public site not found."}, status=status.HTTP_404_NOT_FOUND)
        site = getattr(organization, "public_site", None)
        if not site or not site.enabled or not site.tracking_enabled:
            return Response({"detail": "Tracking is not enabled for this business."}, status=status.HTTP_403_FORBIDDEN)
        if not reference:
            return Response({"reference": "Reference is required."}, status=status.HTTP_400_BAD_REQUEST)
        delivery = Delivery.objects.select_related("courier").prefetch_related("events").filter(
            organization=organization,
            reference__iexact=reference,
        ).first()
        if not delivery or (phone and delivery.customer_phone and delivery.customer_phone != phone):
            return Response({"detail": "Delivery not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(DeliverySerializer(delivery).data)


class BranchViewSet(viewsets.ModelViewSet):
    serializer_class = BranchSerializer
    permission_classes = [OrganizationScopedPermission]
    required_permission = None

    def get_queryset(self):
        organization = require_tenant(self.request)
        qs = Branch.objects.for_organization(organization).filter(status=Branch.Status.ACTIVE)
        branch = require_branch(self.request)
        if branch and not self._is_owner():
            qs = qs.filter(id=branch.id)
        return qs.order_by("name")

    def _is_owner(self):
        return actor_is_owner(getattr(self.request, "actor", None))

    def _require_owner(self):
        if not self._is_owner():
            return Response({"detail": "Only organization owners can manage branches."}, status=status.HTTP_403_FORBIDDEN)
        return None

    def create(self, request, *args, **kwargs):
        denied = self._require_owner()
        if denied:
            return denied
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        denied = self._require_owner()
        if denied:
            return denied
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        denied = self._require_owner()
        if denied:
            return denied
        return super().partial_update(request, *args, **kwargs)

    def perform_create(self, serializer):
        organization = require_tenant(self.request)
        with transaction.atomic():
            branch = serializer.save(organization=organization)
            if branch.is_default:
                Branch.objects.filter(organization=organization).exclude(id=branch.id).update(is_default=False)
        write_audit(action="branch.created", request=self.request, metadata={"branch_id": str(branch.id)})

    def perform_update(self, serializer):
        if serializer.instance.is_default and serializer.validated_data.get("status") == Branch.Status.INACTIVE:
            from rest_framework.exceptions import ValidationError

            raise ValidationError({"status": "The default branch cannot be deactivated."})
        with transaction.atomic():
            branch = serializer.save()
            if branch.is_default:
                Branch.objects.filter(organization=branch.organization).exclude(id=branch.id).update(is_default=False)
        write_audit(action="branch.updated", request=self.request, metadata={"branch_id": str(branch.id)})

    def destroy(self, request, *args, **kwargs):
        denied = self._require_owner()
        if denied:
            return denied
        branch = self.get_object()
        if branch.is_default:
            return Response({"detail": "The default branch cannot be deactivated."}, status=status.HTTP_400_BAD_REQUEST)
        branch.status = Branch.Status.INACTIVE
        branch.save(update_fields=["status", "updated_at"])
        write_audit(action="branch.deactivated", request=request, metadata={"branch_id": str(branch.id)})
        return Response(status=status.HTTP_204_NO_CONTENT)


class StaffViewSet(TenantViewSet):
    serializer_class = OrganizationUserSerializer
    queryset = OrganizationUser.objects.select_related("role", "branch")
    required_permission = "manage_staff"

    def partial_update(self, request, *args, **kwargs):
        member = self.get_object()
        actor = getattr(request, "actor", None)
        if actor and str(member.id) == str(actor.id) and request.data.get("status") == OrganizationUser.Status.SUSPENDED:
            return Response({"detail": "You cannot suspend your own account."}, status=status.HTTP_400_BAD_REQUEST)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        member = self.get_object()
        actor = getattr(request, "actor", None)
        if actor and str(member.id) == str(actor.id):
            return Response({"detail": "You cannot remove your own account."}, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)


class CustomerViewSet(TenantViewSet):
    serializer_class = CustomerSerializer
    queryset = Customer.objects.all()
    required_permission = "view_customers"


class CourierViewSet(TenantViewSet):
    serializer_class = CourierSerializer
    queryset = Courier.objects.all()
    required_permission = "view_fleet"

    @action(detail=True, methods=["post"])
    def status(self, request, pk=None):
        courier = self.get_object()
        new_status = request.data.get("status")
        if new_status not in Courier.Status.values:
            return Response({"detail": "Invalid courier status."}, status=status.HTTP_400_BAD_REQUEST)
        active_delivery_exists = Delivery.objects.filter(
            organization=courier.organization,
            courier=courier,
            status__in=[
                Delivery.Status.ACCEPTED,
                Delivery.Status.PICKED_UP,
                Delivery.Status.IN_TRANSIT,
            ],
        ).exists()
        if courier.status == Courier.Status.DELIVERING and active_delivery_exists and new_status != Courier.Status.DELIVERING:
            return Response(
                {"detail": "Courier status is locked while an active delivery is in progress."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        courier.status = new_status
        courier.save(update_fields=["status", "updated_at"])
        write_audit(action="courier.status_changed", request=request, metadata={"courier_id": str(courier.id), "status": new_status})
        return Response(CourierSerializer(courier).data)

    @action(detail=False, methods=["get"])
    def nearest(self, request):
        serializer = NearestCouriersQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        matches = nearest_couriers(self.get_organization(), branch=require_branch(request), **serializer.validated_data)
        return Response(
            [
                {
                    "courier": CourierSerializer(match["courier"]).data,
                    "distance_km": match["distance_km"],
                    "latitude": match["latitude"],
                    "longitude": match["longitude"],
                    "location_updated_at": match["location_updated_at"],
                }
                for match in matches
            ]
        )


class DeliveryViewSet(TenantViewSet):
    serializer_class = DeliverySerializer
    queryset = Delivery.objects.select_related("customer", "courier").prefetch_related("events")
    required_permission = "view_orders"

    def get_queryset(self):
        qs = super().get_queryset()
        status_filter = self.request.query_params.get("status")
        search = self.request.query_params.get("search")
        if status_filter:
            qs = qs.filter(status=status_filter)
        if search:
            qs = qs.filter(Q(reference__icontains=search) | Q(customer_name__icontains=search))
        return qs.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        organization = self.get_organization()
        data = request.data.copy()
        branch = require_branch(request)
        if branch and not data.get("branch"):
            data["branch"] = str(branch.id)
        serializer = DeliveryCreateSerializer(data=data, context={"organization": organization})
        serializer.is_valid(raise_exception=True)
        delivery = create_delivery(organization=organization, actor=getattr(request, "actor", None), request=request, **serializer.validated_data)
        return Response(DeliverySerializer(delivery).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def assign(self, request, pk=None):
        organization = self.get_organization()
        serializer = AssignCourierSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            courier_qs = Courier.objects.filter(id=serializer.validated_data["courier_id"], organization=organization)
            branch = require_branch(request)
            if branch:
                courier_qs = courier_qs.filter(branch=branch)
            delivery = assign_courier(
                organization=organization,
                delivery=self.get_object(),
                courier=courier_qs.get(),
                actor=getattr(request, "actor", None),
                request=request,
            )
        except (Courier.DoesNotExist, DeliveryStateError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(DeliverySerializer(delivery).data)

    @action(detail=True, methods=["post"])
    def transition(self, request, pk=None):
        organization = self.get_organization()
        serializer = TransitionDeliverySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            delivery = transition_delivery(
                organization=organization,
                delivery=self.get_object(),
                status=serializer.validated_data["status"],
                actor=getattr(request, "actor", None),
                request=request,
            )
        except DeliveryStateError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(DeliverySerializer(delivery).data)


class TrackingViewSet(TenantViewSet):
    serializer_class = TrackingLogSerializer
    queryset = TrackingLog.objects.select_related("courier", "delivery")
    permission_classes = [CourierTrackingPermission]
    required_permission = "view_fleet"

    def create(self, request, *args, **kwargs):
        organization = self.get_organization()
        actor = getattr(request, "actor", None)
        data = request.data.copy()
        if isinstance(actor, Courier):
            data["courier_id"] = str(actor.id)
        serializer = TrackingCreateSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        branch = require_branch(request)
        if isinstance(actor, Courier):
            courier = actor
        else:
            courier_qs = Courier.objects.filter(id=serializer.validated_data["courier_id"], organization=organization)
            if branch:
                courier_qs = courier_qs.filter(branch=branch)
            courier = courier_qs.first()
        if not courier:
            return Response({"detail": "Courier not found."}, status=status.HTTP_404_NOT_FOUND)
        delivery = None
        if serializer.validated_data.get("delivery_id"):
            delivery_qs = Delivery.objects.filter(id=serializer.validated_data["delivery_id"], organization=organization)
            if branch:
                delivery_qs = delivery_qs.filter(branch=branch)
            delivery = delivery_qs.first()
            if not delivery:
                return Response({"detail": "Delivery not found."}, status=status.HTTP_404_NOT_FOUND)
        log = record_tracking(organization=organization, courier=courier, delivery=delivery, **{k: v for k, v in serializer.validated_data.items() if k not in {"courier_id", "delivery_id"}})
        if log is None:
            return Response({"detail": "Tracking update throttled."}, status=status.HTTP_202_ACCEPTED)
        return Response(TrackingLogSerializer(log).data, status=status.HTTP_201_CREATED)


class CourierMessageViewSet(TenantViewSet):
    serializer_class = CourierMessageSerializer
    queryset = CourierMessage.objects.select_related("courier", "branch", "sender_user", "contact_user")
    required_permission = "view_fleet"

    def get_queryset(self):
        qs = super().get_queryset().order_by("-created_at")
        courier_id = self.request.query_params.get("courier_id")
        if courier_id:
            qs = qs.filter(courier_id=courier_id)
        contact_user_id = self.request.query_params.get("contact_user_id") or self.request.query_params.get("contact_id")
        actor = getattr(self.request, "actor", None)
        if contact_user_id:
            qs = qs.filter(contact_user_id=contact_user_id)
        elif isinstance(actor, OrganizationUser):
            qs = qs.filter(contact_user=actor)
        return qs

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        actor = getattr(request, "actor", None)
        courier_id = request.query_params.get("courier_id")
        contact_user_id = request.query_params.get("contact_user_id") or request.query_params.get("contact_id")
        if isinstance(actor, OrganizationUser) and courier_id and contact_user_id and str(actor.id) == str(contact_user_id):
            read_at = timezone.now()
            unread_ids = list(
                CourierMessage.objects.filter(
                    organization=self.get_organization(),
                    courier_id=courier_id,
                    contact_user_id=contact_user_id,
                    sender_type=CourierMessage.SenderType.COURIER,
                    read_at__isnull=True,
                ).values_list("id", flat=True)
            )
            if unread_ids:
                CourierMessage.objects.filter(id__in=unread_ids).update(read_at=read_at, updated_at=read_at)
                _broadcast_courier_messages_read(self.get_organization().id, courier_id, contact_user_id, unread_ids)
        return response

    @action(detail=False, methods=["get"])
    def contacts(self, request):
        actor = getattr(request, "actor", None)
        if not isinstance(actor, OrganizationUser):
            return Response({"detail": "Organization account required."}, status=status.HTTP_403_FORBIDDEN)

        organization = self.get_organization()
        courier_id = request.query_params.get("courier_id")
        courier_qs = Courier.objects.filter(id=courier_id, organization=organization).exclude(status=Courier.Status.INACTIVE)
        branch = require_branch(request)
        if branch:
            courier_qs = courier_qs.filter(branch=branch)
        courier = courier_qs.first()
        if not courier:
            return Response({"detail": "Courier not found."}, status=status.HTTP_404_NOT_FOUND)

        contacts = _manager_contact_queryset(organization, courier.branch)
        if not actor_is_owner(actor):
            contacts = contacts.filter(Q(id=actor.id) | Q(id__in=CourierMessage.objects.filter(
                organization=organization,
                courier=courier,
                contact_user__isnull=False,
            ).values("contact_user_id")))
        rows = [_serialize_courier_chat_contact(contact, courier, actor=actor) for contact in contacts]
        rows.sort(key=lambda item: (not item["is_self"], item["name"].lower()))
        return Response(rows)

    def create(self, request, *args, **kwargs):
        actor = getattr(request, "actor", None)
        if not isinstance(actor, OrganizationUser) or not actor.role.permissions.filter(code="manage_fleet").exists():
            return Response({"detail": "Missing manage_fleet permission."}, status=status.HTTP_403_FORBIDDEN)

        organization = self.get_organization()
        serializer = CourierMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        courier_qs = Courier.objects.filter(id=serializer.validated_data.get("courier_id"), organization=organization).exclude(status=Courier.Status.INACTIVE)
        branch = require_branch(request)
        if branch:
            courier_qs = courier_qs.filter(branch=branch)
        courier = courier_qs.first()
        if not courier:
            return Response({"detail": "Courier not found."}, status=status.HTTP_404_NOT_FOUND)

        message = CourierMessage.objects.create(
            organization=organization,
            branch=courier.branch,
            courier=courier,
            sender_type=CourierMessage.SenderType.DISPATCH,
            sender_user=actor,
            contact_user=actor,
            body=serializer.validated_data["body"],
        )
        _broadcast_courier_message(message)
        write_audit(action="courier.message_sent", organization=organization, actor=actor, request=request, metadata={"courier_id": str(courier.id)})
        return Response(CourierMessageSerializer(message).data, status=status.HTTP_201_CREATED)


class CourierOwnMessagesView(APIView):
    def get(self, request):
        actor = getattr(request, "actor", None)
        if not isinstance(actor, Courier):
            return Response({"detail": "Courier account required."}, status=status.HTTP_403_FORBIDDEN)
        contact = _find_courier_chat_contact(
            actor.organization,
            request.query_params.get("contact_user_id") or request.query_params.get("contact_id"),
            actor.branch,
        )
        if not contact:
            return Response([])
        messages = CourierMessage.objects.select_related("courier", "sender_user", "contact_user", "branch").filter(
            organization=actor.organization,
            courier=actor,
            contact_user=contact,
        ).order_by("-created_at")[:30]
        read_at = timezone.now()
        unread_ids = [
            message.id
            for message in messages
            if message.sender_type == CourierMessage.SenderType.DISPATCH and message.read_at is None
        ]
        if unread_ids:
            CourierMessage.objects.filter(id__in=unread_ids).update(read_at=read_at, updated_at=read_at)
            _broadcast_courier_messages_read(actor.organization_id, actor.id, contact.id, unread_ids)
            for message in messages:
                if message.id in unread_ids:
                    message.read_at = read_at
        return Response(CourierMessageSerializer(list(reversed(list(messages))), many=True).data)

    def post(self, request):
        actor = getattr(request, "actor", None)
        if not isinstance(actor, Courier):
            return Response({"detail": "Courier account required."}, status=status.HTTP_403_FORBIDDEN)
        serializer = CourierMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contact = _find_courier_chat_contact(
            actor.organization,
            serializer.validated_data.get("contact_user_id"),
            actor.branch,
        )
        if not contact:
            return Response({"detail": "Select a dispatch contact before sending a message."}, status=status.HTTP_400_BAD_REQUEST)
        message = CourierMessage.objects.create(
            organization=actor.organization,
            branch=actor.branch,
            courier=actor,
            contact_user=contact,
            sender_type=CourierMessage.SenderType.COURIER,
            body=serializer.validated_data["body"],
        )
        _broadcast_courier_message(message)
        write_audit(action="courier.message_reply_sent", organization=actor.organization, request=request, metadata={"courier_id": str(actor.id)})
        return Response(CourierMessageSerializer(message).data, status=status.HTTP_201_CREATED)


class CourierOwnMessageContactsView(APIView):
    def get(self, request):
        actor = getattr(request, "actor", None)
        if not isinstance(actor, Courier):
            return Response({"detail": "Courier account required."}, status=status.HTTP_403_FORBIDDEN)
        contacts = _manager_contact_queryset(actor.organization, actor.branch)
        rows = [_serialize_courier_chat_contact(contact, actor) for contact in contacts]
        return Response(rows)


class CourierOwnProfileView(APIView):
    def get(self, request):
        actor = getattr(request, "actor", None)
        if not isinstance(actor, Courier):
            return Response({"detail": "Courier account required."}, status=status.HTTP_403_FORBIDDEN)
        return Response(CourierSerializer(actor).data)

    def patch(self, request):
        actor = getattr(request, "actor", None)
        if not isinstance(actor, Courier):
            return Response({"detail": "Courier account required."}, status=status.HTTP_403_FORBIDDEN)
        serializer = CourierProfileUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        update_fields = ["updated_at"]
        if "phone" in serializer.validated_data:
            actor.phone = serializer.validated_data["phone"].strip()
            update_fields.append("phone")
        if "status" in serializer.validated_data:
            actor.status = serializer.validated_data["status"]
            update_fields.append("status")
        if "preferences" in serializer.validated_data:
            actor.metadata = {
                **(actor.metadata or {}),
                "preferences": serializer.validated_data["preferences"] or {},
            }
            update_fields.append("metadata")
        actor.save(update_fields=update_fields)
        write_audit(action="courier.profile_updated", organization=actor.organization, request=request, metadata={"courier_id": str(actor.id)})
        return Response(CourierSerializer(actor).data)


class CourierOwnDeliveriesView(APIView):
    def get(self, request):
        actor = getattr(request, "actor", None)
        if not isinstance(actor, Courier):
            return Response({"detail": "Courier account required."}, status=status.HTTP_403_FORBIDDEN)
        deliveries = Delivery.objects.select_related("customer", "courier").prefetch_related("events").filter(
            organization=actor.organization,
            courier=actor,
        ).order_by("-created_at")
        return Response(DeliverySerializer(deliveries, many=True).data)


class CourierOwnDeliveryTransitionView(APIView):
    def post(self, request, delivery_id):
        actor = getattr(request, "actor", None)
        if not isinstance(actor, Courier):
            return Response({"detail": "Courier account required."}, status=status.HTTP_403_FORBIDDEN)
        delivery = Delivery.objects.filter(
            id=delivery_id,
            organization=actor.organization,
            courier=actor,
        ).first()
        if not delivery:
            return Response({"detail": "Assigned task not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = TransitionDeliverySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            delivery = transition_delivery(
                organization=actor.organization,
                delivery=delivery,
                status=serializer.validated_data["status"],
                actor=actor,
                request=request,
            )
        except DeliveryStateError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(DeliverySerializer(delivery).data)


class NotificationViewSet(TenantViewSet):
    serializer_class = NotificationSerializer
    queryset = Notification.objects.all()
    required_permission = "view_orders"


class AnalyticsViewSet(TenantViewSet):
    serializer_class = AnalyticsEventSerializer
    queryset = AnalyticsEvent.objects.all()
    required_permission = "view_analytics"

    @action(detail=False, methods=["get"])
    def overview(self, request):
        return Response(overview_metrics(self.get_organization(), branch=require_branch(request)))

    @action(detail=False, methods=["get"])
    def snapshots(self, request):
        if require_branch(request):
            return Response([])
        try:
            days = int(request.query_params.get("days", "7"))
        except ValueError:
            return Response({"detail": "days must be an integer."}, status=status.HTTP_400_BAD_REQUEST)
        snapshots = analytics_snapshot_series(self.get_organization(), days=days)
        return Response(AnalyticsSnapshotSerializer(snapshots, many=True).data)

    @action(detail=False, methods=["post"])
    def aggregate(self, request):
        raw_date = request.data.get("period_start")
        period_start = parse_date(raw_date) if raw_date else timezone.localdate()
        if raw_date and not period_start:
            return Response({"detail": "period_start must be an ISO date."}, status=status.HTTP_400_BAD_REQUEST)
        snapshot = aggregate_analytics_snapshot(self.get_organization(), period_start=period_start)
        return Response(AnalyticsSnapshotSerializer(snapshot).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def heatmap(self, request):
        serializer = DeliveryHeatmapQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return Response(delivery_zone_heatmap(self.get_organization(), days=serializer.validated_data["days"], branch=require_branch(request)))


class DomainViewSet(TenantViewSet):
    serializer_class = CustomDomainSerializer
    queryset = CustomDomain.objects.all()
    required_permission = "manage_settings"

    def create(self, request, *args, **kwargs):
        domain = request.data.get("domain")
        if not domain:
            return Response({"detail": "domain is required."}, status=status.HTTP_400_BAD_REQUEST)
        record = create_domain_verification(self.get_organization(), domain)
        write_audit(action="domain.verification_created", request=request, metadata={"domain": domain})
        return Response(CustomDomainSerializer(record).data, status=status.HTTP_201_CREATED)


class APIKeyViewSet(TenantViewSet):
    serializer_class = APIKeySerializer
    queryset = APIKey.objects.all()
    required_permission = "manage_settings"

    def create(self, request, *args, **kwargs):
        serializer = APIKeyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw, prefix, key_hash = make_api_key()
        api_key = APIKey.objects.create(
            organization=self.get_organization(),
            name=serializer.validated_data["name"],
            scopes=serializer.validated_data.get("scopes") or [],
            prefix=prefix,
            key_hash=key_hash,
        )
        write_audit(action="api_key.created", request=request, metadata={"api_key_id": str(api_key.id), "prefix": prefix})
        data = APIKeySerializer(api_key).data
        data["key"] = raw
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        api_key = self.get_object()
        api_key.revoked_at = timezone.now()
        api_key.save(update_fields=["revoked_at", "updated_at"])
        write_audit(action="api_key.revoked", request=request, metadata={"api_key_id": str(api_key.id)})
        return Response(APIKeySerializer(api_key).data)


class UploadIntentView(APIView):
    permission_classes = [OrganizationScopedPermission]
    required_permission = "manage_orders"

    def post(self, request):
        organization = require_tenant(request)
        serializer = UploadIntentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            intent = create_upload_intent(
                organization=organization,
                upload_type=serializer.validated_data["type"],
                original_name=serializer.validated_data["original_name"],
                mime_type=serializer.validated_data["mime_type"],
                size_bytes=serializer.validated_data["size_bytes"],
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        write_audit(action="upload.intent_created", request=request, metadata={"upload_id": intent["upload_id"]})
        return Response(intent, status=status.HTTP_201_CREATED)


class UploadCompleteView(APIView):
    permission_classes = [OrganizationScopedPermission]
    required_permission = "manage_orders"

    def post(self, request, upload_id):
        organization = require_tenant(request)
        serializer = UploadCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            upload = complete_upload(
                organization=organization,
                upload_id=upload_id,
                checksum=serializer.validated_data.get("checksum", ""),
                size_bytes=serializer.validated_data.get("size_bytes"),
            )
        except Upload.DoesNotExist:
            return Response({"detail": "Upload not found."}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        write_audit(action="upload.completed", request=request, metadata={"upload_id": str(upload.id)})
        return Response(UploadSerializer(upload).data)


def _platform_has_permission(request, code: str) -> bool:
    actor = getattr(request, "actor", None)
    if not _is_platform_actor(actor):
        return False
    return platform_profile_for_user(actor).role.permissions.filter(code=code).exists()


def _is_platform_actor(actor) -> bool:
    if not isinstance(actor, get_user_model()):
        return False
    try:
        platform_profile_for_user(actor)
    except ValueError:
        return False
    return True


def _is_user_account(actor) -> bool:
    return isinstance(actor, OrganizationUser) or _is_platform_actor(actor)
