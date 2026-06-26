from rest_framework import serializers
from django.utils.text import slugify

from .models import (
    APIKey,
    AnalyticsEvent,
    AnalyticsSnapshot,
    Branch,
    Courier,
    CourierMessage,
    CustomDomain,
    Customer,
    Delivery,
    DeliveryEvent,
    Notification,
    NotificationAttempt,
    Organization,
    OrganizationRole,
    OrganizationUser,
    PublicSite,
    PublicSiteBlock,
    PublicSitePage,
    PublicSiteServiceArea,
    TrackingLog,
    Upload,
    validate_public_site_slug,
)


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        exclude = ["organization", "updated_at"]
        read_only_fields = ["id", "created_at"]


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = [
            "id",
            "name",
            "slug",
            "subdomain",
            "custom_domain",
            "subscription_plan",
            "status",
            "domain_settings",
            "branding",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class BusinessSignupSerializer(serializers.Serializer):
    owner_name = serializers.CharField(max_length=120)
    owner_email = serializers.EmailField()
    owner_password = serializers.CharField(min_length=8, max_length=128, write_only=True, trim_whitespace=False)
    company_name = serializers.CharField(max_length=120)
    subdomain = serializers.SlugField(max_length=63, required=False, allow_blank=True)
    company_size = serializers.CharField(max_length=40, required=False, allow_blank=True)
    branch_count = serializers.IntegerField(min_value=1, max_value=10000, required=False, default=1)
    country = serializers.CharField(max_length=80, required=False, allow_blank=True)
    currency = serializers.CharField(max_length=3, required=False, allow_blank=True)
    location = serializers.CharField(max_length=240, required=False, allow_blank=True)
    brand_color = serializers.RegexField(
        regex=r"^#[0-9A-Fa-f]{6}$",
        required=False,
        allow_blank=True,
        error_messages={"invalid": "Enter a valid hex color, like #16a34a."},
    )
    enable_public_site = serializers.BooleanField(required=False, default=False)
    site_headline = serializers.CharField(max_length=160, required=False, allow_blank=True)
    site_description = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        owner_name = attrs["owner_name"].strip()
        company_name = attrs["company_name"].strip()
        subdomain = slugify((attrs.get("subdomain") or company_name).strip())[:63].strip("-")
        if not owner_name:
            raise serializers.ValidationError({"owner_name": "Owner name is required."})
        if not company_name:
            raise serializers.ValidationError({"company_name": "Company name is required."})
        if not subdomain:
            raise serializers.ValidationError({"subdomain": "Choose a valid business subdomain."})
        if Organization.objects.filter(slug=subdomain).exists() or Organization.objects.filter(subdomain=subdomain).exists():
            raise serializers.ValidationError({"subdomain": "That business URL is already taken."})
        attrs["owner_name"] = owner_name
        attrs["owner_email"] = attrs["owner_email"].strip().lower()
        attrs["company_name"] = company_name
        attrs["subdomain"] = subdomain
        attrs["brand_color"] = attrs.get("brand_color") or "#16a34a"
        attrs["currency"] = (attrs.get("currency") or "NGN").strip().upper()
        attrs["country"] = (attrs.get("country") or "").strip()
        attrs["company_size"] = (attrs.get("company_size") or "").strip()
        attrs["location"] = (attrs.get("location") or "").strip()
        return attrs


class OrganizationRoleSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = OrganizationRole
        fields = ["id", "key", "label", "description", "permissions"]

    def get_permissions(self, obj):
        return list(obj.permissions.values_list("code", flat=True))


class OrganizationUserSerializer(serializers.ModelSerializer):
    role_key = serializers.CharField(source="role.key", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)

    class Meta:
        model = OrganizationUser
        fields = [
            "id",
            "name",
            "email",
            "initials",
            "branch",
            "branch_name",
            "role",
            "role_key",
            "status",
            "mfa_enabled",
            "last_login",
            "last_active_at",
            "created_at",
        ]
        read_only_fields = ["id", "mfa_enabled", "last_login", "last_active_at", "created_at"]


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        exclude = ["organization", "updated_at"]
        read_only_fields = ["id", "created_at"]


class CourierSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    active_delivery = serializers.SerializerMethodField()

    class Meta:
        model = Courier
        exclude = ["organization", "updated_at", "password_hash"]
        read_only_fields = ["id", "created_at", "location_updated_at"]

    def get_active_delivery(self, obj):
        delivery = (
            Delivery.objects.filter(
                organization=obj.organization,
                courier=obj,
                status__in=[
                    Delivery.Status.ACCEPTED,
                    Delivery.Status.PICKED_UP,
                    Delivery.Status.IN_TRANSIT,
                ],
            )
            .order_by("-created_at")
            .first()
        )
        if not delivery:
            return None
        return {
            "id": str(delivery.id),
            "reference": delivery.reference,
            "destination": delivery.delivery_address,
        }


class DeliveryEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryEvent
        fields = ["id", "label", "status", "event_at", "done", "sort_order", "metadata"]
        read_only_fields = ["id"]


class DeliverySerializer(serializers.ModelSerializer):
    events = DeliveryEventSerializer(many=True, read_only=True)
    courier_name = serializers.CharField(source="courier.name", read_only=True)

    class Meta:
        model = Delivery
        exclude = ["organization", "updated_at"]
        read_only_fields = [
            "id",
            "created_at",
            "accepted_at",
            "picked_up_at",
            "completed_at",
            "failed_at",
            "cancelled_at",
            "events",
            "courier_name",
        ]


class DeliveryCreateSerializer(serializers.Serializer):
    branch = serializers.PrimaryKeyRelatedField(queryset=Branch.objects.none(), required=False, allow_null=True)
    customer = serializers.PrimaryKeyRelatedField(queryset=Customer.objects.none(), required=False, allow_null=True)
    reference = serializers.CharField(max_length=40, required=False)
    customer_name = serializers.CharField(max_length=120)
    customer_phone = serializers.CharField(max_length=40, required=False, allow_blank=True)
    pickup_address = serializers.CharField(max_length=240)
    delivery_address = serializers.CharField(max_length=240)
    zone = serializers.CharField(max_length=60, required=False, allow_blank=True)
    delivery_fee = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    scheduled_time = serializers.DateTimeField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    source = serializers.CharField(max_length=80, required=False, allow_blank=True)
    source_label = serializers.CharField(max_length=120, required=False, allow_blank=True)
    external_reference = serializers.CharField(max_length=120, required=False, allow_blank=True)
    metadata = serializers.JSONField(required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        organization = self.context.get("organization")
        if organization:
            self.fields["customer"].queryset = Customer.objects.for_organization(organization)
            self.fields["branch"].queryset = Branch.objects.for_organization(organization).filter(status=Branch.Status.ACTIVE)


class PublicDeliveryRequestSerializer(serializers.Serializer):
    customer_name = serializers.CharField(max_length=120)
    customer_phone = serializers.CharField(max_length=40)
    pickup_address = serializers.CharField(max_length=240)
    delivery_address = serializers.CharField(max_length=240)
    pickup_latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    pickup_longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    delivery_latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    delivery_longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    zone = serializers.CharField(max_length=60, required=False, allow_blank=True)
    scheduled_time = serializers.DateTimeField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)


class PublicSiteServiceAreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = PublicSiteServiceArea
        fields = ["id", "name", "sort_order"]
        read_only_fields = ["id"]


class PublicSiteSerializer(serializers.ModelSerializer):
    service_areas = PublicSiteServiceAreaSerializer(many=True, read_only=True)
    service_area_names = serializers.ListField(
        child=serializers.CharField(max_length=120),
        required=False,
        write_only=True,
    )
    public_url = serializers.SerializerMethodField()

    class Meta:
        model = PublicSite
        exclude = ["organization"]
        read_only_fields = ["id", "created_at", "updated_at", "public_url"]

    def get_public_url(self, obj):
        request = self.context.get("request")
        base_domain = self.context.get("base_domain") or "localhost:3000"
        scheme = "https"
        if request and request.get_host().startswith(("localhost", "127.0.0.1")):
            scheme = "http"
        return f"{scheme}://{obj.organization.subdomain}.{base_domain}"


class PublicSiteBlockSerializer(serializers.ModelSerializer):
    class Meta:
        model = PublicSiteBlock
        exclude = ["organization", "public_site"]
        read_only_fields = ["id", "created_at", "updated_at"]


class PublicSitePageSerializer(serializers.ModelSerializer):
    blocks = PublicSiteBlockSerializer(many=True, read_only=True)

    class Meta:
        model = PublicSitePage
        exclude = ["organization", "public_site"]
        read_only_fields = ["id", "created_at", "updated_at", "published_at", "blocks"]

    def validate_slug(self, value):
        validate_public_site_slug(value)
        return value


class AssignCourierSerializer(serializers.Serializer):
    courier_id = serializers.UUIDField()


class TransitionDeliverySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Delivery.Status.choices)


class TrackingLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrackingLog
        exclude = ["organization"]
        read_only_fields = ["id", "timestamp", "created_at", "updated_at"]


class TrackingCreateSerializer(serializers.Serializer):
    courier_id = serializers.UUIDField()
    delivery_id = serializers.UUIDField(required=False)
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    accuracy = serializers.DecimalField(max_digits=7, decimal_places=2, required=False)
    battery_level = serializers.IntegerField(min_value=0, max_value=100, required=False)


class CourierProfileUpdateSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=40, required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=Courier.Status.choices, required=False)
    preferences = serializers.JSONField(required=False)


class CourierMessageSerializer(serializers.ModelSerializer):
    courier_name = serializers.CharField(source="courier.name", read_only=True)
    sender_name = serializers.SerializerMethodField()
    contact_user_name = serializers.CharField(source="contact_user.name", read_only=True)
    chat_id = serializers.SerializerMethodField()
    recipient_active = serializers.SerializerMethodField()

    class Meta:
        model = CourierMessage
        exclude = ["organization", "updated_at"]
        read_only_fields = [
            "id",
            "branch",
            "sender_type",
            "sender_user",
            "contact_user",
            "read_at",
            "created_at",
            "courier_name",
            "sender_name",
            "contact_user_name",
            "chat_id",
            "recipient_active",
        ]

    def get_sender_name(self, obj):
        if obj.sender_type == CourierMessage.SenderType.COURIER:
            return obj.courier.name
        if obj.sender_user:
            return obj.sender_user.name
        return "Dispatch"

    def get_chat_id(self, obj):
        contact_id = obj.contact_user_id or "unassigned"
        return f"courier:{obj.courier_id}:manager:{contact_id}"

    def get_recipient_active(self, obj):
        if obj.sender_type == CourierMessage.SenderType.COURIER:
            return bool(obj.contact_user and obj.contact_user.status == OrganizationUser.Status.ACTIVE)
        return obj.courier.status not in {Courier.Status.OFFLINE, Courier.Status.INACTIVE}


class CourierMessageCreateSerializer(serializers.Serializer):
    courier_id = serializers.UUIDField(required=False)
    contact_user_id = serializers.UUIDField(required=False)
    body = serializers.CharField(max_length=2000, trim_whitespace=True)


class NearestCouriersQuerySerializer(serializers.Serializer):
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    radius_km = serializers.FloatField(min_value=0.1, max_value=500, required=False, default=10)
    limit = serializers.IntegerField(min_value=1, max_value=50, required=False, default=10)


class DeliveryHeatmapQuerySerializer(serializers.Serializer):
    days = serializers.IntegerField(min_value=1, max_value=365, required=False, default=30)


class NotificationSerializer(serializers.ModelSerializer):
    delivery_attempts = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        exclude = ["organization"]
        read_only_fields = ["id", "attempts", "last_error", "sent_at", "created_at", "updated_at"]

    def get_delivery_attempts(self, obj):
        return NotificationAttemptSerializer(obj.delivery_attempts.order_by("attempt_number"), many=True).data


class NotificationAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationAttempt
        fields = ["id", "attempt_number", "provider", "success", "error", "metadata", "created_at"]
        read_only_fields = fields


class AnalyticsEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnalyticsEvent
        exclude = ["organization", "updated_at"]
        read_only_fields = ["id", "created_at"]


class AnalyticsSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnalyticsSnapshot
        exclude = ["organization", "updated_at"]
        read_only_fields = [
            "id",
            "period_type",
            "period_start",
            "delivery_volume",
            "completed_deliveries",
            "failed_deliveries",
            "completion_rate",
            "average_delivery_seconds",
            "revenue",
            "delivery_fees",
            "active_courier_count",
            "total_courier_count",
            "rider_efficiency",
            "metadata",
            "created_at",
        ]


class CustomDomainSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomDomain
        exclude = ["organization", "updated_at"]
        read_only_fields = ["id", "status", "txt_record_name", "txt_record_value", "verified_at", "ssl_status", "created_at"]


class APIKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = APIKey
        fields = ["id", "name", "prefix", "scopes", "last_used_at", "revoked_at", "created_at"]
        read_only_fields = ["id", "prefix", "last_used_at", "revoked_at", "created_at"]


class APIKeyCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    scopes = serializers.ListField(child=serializers.CharField(max_length=80), required=False)


class UploadIntentSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=Upload.Type.choices)
    original_name = serializers.CharField(max_length=180)
    mime_type = serializers.CharField(max_length=120)
    size_bytes = serializers.IntegerField(min_value=1)


class UploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Upload
        exclude = ["organization", "updated_at"]
        read_only_fields = [
            "id",
            "type",
            "status",
            "object_key",
            "storage_url",
            "original_name",
            "mime_type",
            "size_bytes",
            "checksum",
            "completed_at",
            "metadata",
            "created_at",
        ]


class UploadCompleteSerializer(serializers.Serializer):
    checksum = serializers.CharField(max_length=128, required=False, allow_blank=True)
    size_bytes = serializers.IntegerField(min_value=1, required=False)


class OrganizationLoginSerializer(serializers.Serializer):
    organization = serializers.CharField()
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    mfa_code = serializers.CharField(write_only=True, required=False, allow_blank=True)


class CourierLoginSerializer(serializers.Serializer):
    organization = serializers.CharField()
    identifier = serializers.CharField(required=False, allow_blank=True)
    phone = serializers.CharField(required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        identifier = (attrs.get("identifier") or attrs.get("phone") or attrs.get("email") or "").strip()
        if not identifier:
            raise serializers.ValidationError({"identifier": "Enter a courier email or phone number."})
        attrs["identifier"] = identifier
        return attrs


class PlatformLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    mfa_code = serializers.CharField(write_only=True, required=False, allow_blank=True)


class RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField(write_only=True)


class MFAVerifySerializer(serializers.Serializer):
    code = serializers.CharField(write_only=True, min_length=6, max_length=12)


class ImpersonationStartSerializer(serializers.Serializer):
    organization_id = serializers.UUIDField()
    reason = serializers.CharField(min_length=10, max_length=1000)
    duration_minutes = serializers.IntegerField(min_value=1, max_value=60, default=30, required=False)
    allowed_permissions = serializers.ListField(
        child=serializers.CharField(max_length=80),
        required=False,
        allow_empty=True,
    )
