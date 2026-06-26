import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


RESERVED_PUBLIC_SITE_SLUGS = {
    "admin",
    "api",
    "assets",
    "dashboard",
    "login",
    "account",
    "request",
    "track",
    "static",
    "_next",
}


def normalize_public_site_slug(value: str) -> str:
    slug = "/" + str(value or "/").strip().strip("/")
    return "/" if slug == "/" else slug.rstrip("/")


def validate_public_site_slug(value: str) -> None:
    slug = normalize_public_site_slug(value)
    first_segment = slug.strip("/").split("/", 1)[0]
    if first_segment in RESERVED_PUBLIC_SITE_SLUGS:
        raise ValidationError(f'"/{first_segment}" is reserved for Streak system routes.')


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Organization(TimestampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80, unique=True)
    subdomain = models.SlugField(max_length=63, unique=True)
    custom_domain = models.CharField(max_length=160, unique=True, blank=True, null=True)
    subscription_plan = models.CharField(max_length=40, default="Starter")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    domain_settings = models.JSONField(default=dict, blank=True)
    branding = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "organizations"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["subdomain"]),
        ]

    def __str__(self) -> str:
        return self.name


class TenantQuerySet(models.QuerySet):
    def for_organization(self, organization):
        organization_id = getattr(organization, "id", organization)
        return self.filter(organization_id=organization_id)


class TenantScopedModel(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)

    objects = TenantQuerySet.as_manager()

    class Meta:
        abstract = True
        indexes = [models.Index(fields=["organization"])]


class Branch(TenantScopedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    name = models.CharField(max_length=120)
    code = models.SlugField(max_length=40)
    state = models.CharField(max_length=80)
    city = models.CharField(max_length=80, blank=True)
    address = models.CharField(max_length=240, blank=True)
    is_default = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "branches"
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="uniq_branch_code_per_org"),
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["organization", "is_default"]),
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.organization_id}:{self.name}"


class PlatformPermission(models.Model):
    id = models.BigAutoField(primary_key=True)
    code = models.CharField(max_length=80, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "platform_permissions"
        ordering = ["code"]

    def __str__(self) -> str:
        return self.code


class PlatformRole(models.Model):
    id = models.BigAutoField(primary_key=True)
    key = models.SlugField(max_length=40, unique=True)
    label = models.CharField(max_length=80)
    permissions = models.ManyToManyField(PlatformPermission, blank=True)

    class Meta:
        db_table = "platform_roles"
        ordering = ["key"]

    def __str__(self) -> str:
        return self.label


class PlatformUser(TimestampedModel):
    """Platform profile for a Django auth user."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="platform_profile",
        blank=True,
        null=True,
    )
    email = models.EmailField(unique=True, blank=True)
    name = models.CharField(max_length=120, blank=True)
    password_hash = models.CharField(max_length=256, blank=True)
    role = models.ForeignKey(PlatformRole, on_delete=models.PROTECT, related_name="users")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    mfa_enabled = models.BooleanField(default=False)
    mfa_secret = models.TextField(blank=True)
    mfa_confirmed_at = models.DateTimeField(blank=True, null=True)
    last_login = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "platform_users"
        indexes = [models.Index(fields=["email", "status"])]

    def __str__(self) -> str:
        return self.display_email

    @property
    def is_authenticated(self) -> bool:
        return self.is_active

    @property
    def is_active(self) -> bool:
        if self.user_id:
            return self.status == self.Status.ACTIVE and self.user.is_active
        return self.status == self.Status.ACTIVE

    @property
    def display_email(self) -> str:
        return self.user.email if self.user_id else self.email

    @property
    def display_name(self) -> str:
        if self.user_id:
            full_name = self.user.get_full_name()
            return full_name or self.user.get_username()
        return self.name


class OrganizationPermission(TenantScopedModel):
    code = models.CharField(max_length=80)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "organization_permissions"
        constraints = [
            models.UniqueConstraint(fields=["organization", "code"], name="uniq_org_permission_code"),
        ]
        indexes = [models.Index(fields=["organization", "code"])]

    def __str__(self) -> str:
        return f"{self.organization_id}:{self.code}"


class OrganizationRole(TenantScopedModel):
    key = models.SlugField(max_length=40)
    label = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    permissions = models.ManyToManyField(OrganizationPermission, blank=True)

    class Meta:
        db_table = "organization_roles"
        constraints = [
            models.UniqueConstraint(fields=["organization", "key"], name="uniq_org_role_key"),
        ]
        indexes = [models.Index(fields=["organization", "key"])]

    def __str__(self) -> str:
        return f"{self.organization_id}:{self.key}"


class OrganizationUser(TenantScopedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INVITED = "invited", "Invited"
        SUSPENDED = "suspended", "Suspended"

    name = models.CharField(max_length=120)
    email = models.EmailField()
    initials = models.CharField(max_length=4)
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, related_name="users", blank=True, null=True)
    password_hash = models.CharField(max_length=256, blank=True)
    role = models.ForeignKey(OrganizationRole, on_delete=models.PROTECT, related_name="users")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INVITED)
    mfa_enabled = models.BooleanField(default=False)
    mfa_secret = models.TextField(blank=True)
    mfa_confirmed_at = models.DateTimeField(blank=True, null=True)
    last_login = models.DateTimeField(blank=True, null=True)
    last_active_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "organization_users"
        constraints = [
            models.UniqueConstraint(fields=["organization", "email"], name="uniq_org_user_email"),
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["organization", "branch"]),
            models.Index(fields=["email"]),
        ]

    @property
    def is_authenticated(self) -> bool:
        return True

    def __str__(self) -> str:
        return self.email


class Customer(TenantScopedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        NEW = "new", "New"
        VIP = "vip", "VIP"

    name = models.CharField(max_length=120)
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, related_name="customers", blank=True, null=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    initials = models.CharField(max_length=4, blank=True)
    delivery_stats = models.JSONField(default=dict, blank=True)
    total_orders = models.PositiveIntegerField(default=0)
    total_spent = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    last_order_at = models.DateTimeField(blank=True, null=True)
    zone = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)

    class Meta:
        db_table = "customers"
        constraints = [
            models.UniqueConstraint(fields=["organization", "email"], name="uniq_customer_email_per_org"),
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["organization", "branch"]),
            models.Index(fields=["organization", "created_at"]),
        ]

    def __str__(self) -> str:
        return self.name


class Courier(TenantScopedModel):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        DELIVERING = "delivering", "Delivering"
        OFFLINE = "offline", "Offline"
        INACTIVE = "inactive", "Inactive"

    name = models.CharField(max_length=120)
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, related_name="couriers", blank=True, null=True)
    initials = models.CharField(max_length=4)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OFFLINE)
    current_latitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    current_longitude = models.DecimalField(max_digits=9, decimal_places=6, blank=True, null=True)
    current_location = models.CharField(max_length=120, blank=True)
    location_updated_at = models.DateTimeField(blank=True, null=True)
    battery_level = models.PositiveSmallIntegerField(default=100)
    active_delivery_count = models.PositiveSmallIntegerField(default=0)
    completion_rate = models.PositiveSmallIntegerField(default=100)
    zone = models.CharField(max_length=60, blank=True)
    vehicle = models.CharField(max_length=40, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    password_hash = models.CharField(max_length=256, blank=True)
    last_login = models.DateTimeField(blank=True, null=True)
    rating = models.DecimalField(max_digits=2, decimal_places=1, default=5.0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "couriers"
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["organization", "branch"]),
            models.Index(fields=["organization", "zone"]),
            models.Index(fields=["organization", "email"]),
            models.Index(fields=["organization", "location_updated_at"]),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def is_authenticated(self) -> bool:
        return self.status != self.Status.INACTIVE


class Delivery(TenantScopedModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        PENDING = "pending", "Pending"
        ASSIGNED = "assigned", "Assigned"
        ACCEPTED = "accepted", "Accepted"
        PICKED_UP = "picked_up", "Picked up"
        IN_TRANSIT = "in_transit", "In transit"
        DELIVERED = "delivered", "Delivered"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="deliveries", blank=True, null=True)
    courier = models.ForeignKey(Courier, on_delete=models.SET_NULL, related_name="deliveries", blank=True, null=True)
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, related_name="deliveries", blank=True, null=True)
    reference = models.CharField(max_length=40)
    customer_name = models.CharField(max_length=120)
    customer_phone = models.CharField(max_length=40, blank=True)
    pickup_address = models.CharField(max_length=240)
    delivery_address = models.CharField(max_length=240)
    zone = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    source = models.CharField(max_length=80, default="dashboard")
    source_label = models.CharField(max_length=120, blank=True)
    external_reference = models.CharField(max_length=120, blank=True)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    scheduled_time = models.DateTimeField(blank=True, null=True, db_index=True)
    accepted_at = models.DateTimeField(blank=True, null=True)
    picked_up_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    failed_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "deliveries"
        constraints = [
            models.UniqueConstraint(fields=["organization", "reference"], name="uniq_delivery_reference_per_org"),
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["organization", "source"]),
            models.Index(fields=["organization", "branch"]),
            models.Index(fields=["organization", "courier"]),
            models.Index(fields=["organization", "scheduled_time"]),
            models.Index(fields=["organization", "created_at"]),
        ]

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            self.Status.DELIVERED,
            self.Status.FAILED,
            self.Status.CANCELLED,
        }

    def __str__(self) -> str:
        return self.reference


class DeliveryEvent(TenantScopedModel):
    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="events")
    label = models.CharField(max_length=80)
    status = models.CharField(max_length=20, blank=True)
    event_at = models.DateTimeField(default=timezone.now)
    done = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "delivery_events"
        indexes = [
            models.Index(fields=["organization", "delivery"]),
            models.Index(fields=["delivery", "sort_order"]),
        ]
        ordering = ["sort_order", "event_at"]


class PublicSite(TenantScopedModel):
    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name="public_site")
    enabled = models.BooleanField(default=False)
    headline = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    contact_phone = models.CharField(max_length=40, blank=True)
    contact_email = models.EmailField(blank=True)
    opening_hours = models.TextField(blank=True)
    request_form_enabled = models.BooleanField(default=True)
    tracking_enabled = models.BooleanField(default=True)
    logo_url = models.CharField(max_length=600, blank=True)
    hero_image_url = models.CharField(max_length=600, blank=True)

    class Meta:
        db_table = "public_sites"
        constraints = [
            models.UniqueConstraint(fields=["organization"], name="uniq_public_site_per_org"),
        ]
        indexes = [
            models.Index(fields=["organization", "enabled"]),
        ]

    def __str__(self) -> str:
        return f"{self.organization_id}:public-site"


class PublicSiteServiceArea(TenantScopedModel):
    public_site = models.ForeignKey(PublicSite, on_delete=models.CASCADE, related_name="service_areas")
    name = models.CharField(max_length=120)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "public_site_service_areas"
        constraints = [
            models.UniqueConstraint(fields=["public_site", "name"], name="uniq_public_site_service_area_name"),
        ]
        indexes = [
            models.Index(fields=["organization", "public_site", "sort_order"]),
        ]
        ordering = ["sort_order", "name"]


class PublicSitePage(TenantScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    public_site = models.ForeignKey(PublicSite, on_delete=models.CASCADE, related_name="pages")
    title = models.CharField(max_length=160)
    slug = models.CharField(max_length=180, default="/", validators=[validate_public_site_slug])
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    meta_title = models.CharField(max_length=180, blank=True)
    meta_description = models.CharField(max_length=240, blank=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    published_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "public_site_pages"
        constraints = [
            models.UniqueConstraint(fields=["public_site", "slug"], name="uniq_public_site_page_slug"),
        ]
        indexes = [
            models.Index(fields=["organization", "public_site", "status"]),
            models.Index(fields=["organization", "public_site", "slug"]),
        ]
        ordering = ["sort_order", "title"]

    def clean(self):
        self.slug = normalize_public_site_slug(self.slug)
        validate_public_site_slug(self.slug)

    def save(self, *args, **kwargs):
        self.slug = normalize_public_site_slug(self.slug)
        validate_public_site_slug(self.slug)
        if self.status == self.Status.PUBLISHED and self.published_at is None:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.public_site_id}:{self.slug}"


class PublicSiteBlock(TenantScopedModel):
    class Type(models.TextChoices):
        HERO = "hero", "Hero"
        RICH_TEXT = "rich_text", "Rich text"
        SERVICE_AREAS = "service_areas", "Service areas"
        REQUEST_CTA = "request_cta", "Request CTA"
        CONTACT_BAND = "contact_band", "Contact band"
        FAQ = "faq", "FAQ"

    public_site = models.ForeignKey(PublicSite, on_delete=models.CASCADE, related_name="blocks")
    page = models.ForeignKey(PublicSitePage, on_delete=models.CASCADE, related_name="blocks")
    type = models.CharField(max_length=40, choices=Type.choices)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_visible = models.BooleanField(default=True)
    eyebrow = models.CharField(max_length=80, blank=True)
    headline = models.CharField(max_length=180, blank=True)
    body = models.TextField(blank=True)
    button_label = models.CharField(max_length=80, blank=True)
    button_href = models.CharField(max_length=180, blank=True)
    image_url = models.CharField(max_length=600, blank=True)

    class Meta:
        db_table = "public_site_blocks"
        indexes = [
            models.Index(fields=["organization", "public_site", "page", "sort_order"]),
            models.Index(fields=["organization", "type"]),
        ]
        ordering = ["sort_order", "created_at"]

    def __str__(self) -> str:
        return f"{self.page_id}:{self.type}"


class TrackingLog(TenantScopedModel):
    courier = models.ForeignKey(Courier, on_delete=models.CASCADE, related_name="tracking_logs")
    delivery = models.ForeignKey(Delivery, on_delete=models.SET_NULL, related_name="tracking_logs", blank=True, null=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    accuracy = models.DecimalField(max_digits=7, decimal_places=2, blank=True, null=True)
    battery_level = models.PositiveSmallIntegerField(blank=True, null=True)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "tracking_logs"
        indexes = [
            models.Index(fields=["organization", "courier", "timestamp"]),
            models.Index(fields=["organization", "timestamp"]),
        ]


class CourierMessage(TenantScopedModel):
    class SenderType(models.TextChoices):
        DISPATCH = "dispatch", "Dispatch"
        COURIER = "courier", "Courier"

    courier = models.ForeignKey(Courier, on_delete=models.CASCADE, related_name="messages")
    branch = models.ForeignKey(Branch, on_delete=models.SET_NULL, related_name="courier_messages", blank=True, null=True)
    sender_type = models.CharField(max_length=20, choices=SenderType.choices)
    sender_user = models.ForeignKey(OrganizationUser, on_delete=models.SET_NULL, related_name="sent_courier_messages", blank=True, null=True)
    contact_user = models.ForeignKey(OrganizationUser, on_delete=models.SET_NULL, related_name="courier_chat_messages", blank=True, null=True)
    body = models.TextField()
    read_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "courier_messages"
        indexes = [
            models.Index(fields=["organization", "courier", "created_at"]),
            models.Index(fields=["organization", "courier", "contact_user", "created_at"]),
            models.Index(fields=["organization", "branch", "created_at"]),
        ]
        ordering = ["created_at"]


class Notification(TenantScopedModel):
    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"
        PUSH = "push", "Push"
        IN_APP = "in_app", "In-app"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    channel = models.CharField(max_length=20, choices=Channel.choices)
    event = models.CharField(max_length=80)
    recipient = models.CharField(max_length=160)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    sent_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "notifications"
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["organization", "event", "created_at"]),
        ]


class NotificationAttempt(TimestampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="delivery_attempts")
    attempt_number = models.PositiveSmallIntegerField()
    provider = models.CharField(max_length=80, blank=True)
    success = models.BooleanField(default=False)
    error = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "notification_attempts"
        indexes = [
            models.Index(fields=["notification", "attempt_number"]),
            models.Index(fields=["success", "created_at"]),
        ]


class AnalyticsEvent(TenantScopedModel):
    class Type(models.TextChoices):
        DELIVERY = "delivery", "Delivery"
        RIDER = "rider", "Rider"
        ORDER = "order", "Order"
        ALERT = "alert", "Alert"

    type = models.CharField(max_length=20, choices=Type.choices)
    message = models.CharField(max_length=200)
    detail = models.CharField(max_length=200, blank=True)
    metric_value = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "analytics_events"
        indexes = [
            models.Index(fields=["organization", "created_at"]),
            models.Index(fields=["organization", "type", "created_at"]),
        ]


class AnalyticsSnapshot(TenantScopedModel):
    class PeriodType(models.TextChoices):
        DAILY = "daily", "Daily"

    period_type = models.CharField(max_length=20, choices=PeriodType.choices, default=PeriodType.DAILY)
    period_start = models.DateField()
    delivery_volume = models.PositiveIntegerField(default=0)
    completed_deliveries = models.PositiveIntegerField(default=0)
    failed_deliveries = models.PositiveIntegerField(default=0)
    completion_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    average_delivery_seconds = models.PositiveIntegerField(default=0)
    revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    delivery_fees = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    active_courier_count = models.PositiveIntegerField(default=0)
    total_courier_count = models.PositiveIntegerField(default=0)
    rider_efficiency = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "analytics_snapshots"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "period_type", "period_start"],
                name="uniq_analytics_snapshot_period",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "period_type", "period_start"]),
            models.Index(fields=["organization", "created_at"]),
        ]


class Upload(TenantScopedModel):
    class Type(models.TextChoices):
        LOGO = "organization_logo", "Organization logo"
        PROOF = "proof_of_delivery", "Proof of delivery"
        RIDER_DOCUMENT = "rider_document", "Rider document"
        DELIVERY_IMAGE = "delivery_image", "Delivery image"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        UPLOADED = "uploaded", "Uploaded"
        REJECTED = "rejected", "Rejected"

    type = models.CharField(max_length=40, choices=Type.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    object_key = models.CharField(max_length=320)
    storage_url = models.CharField(max_length=600, blank=True)
    original_name = models.CharField(max_length=180)
    mime_type = models.CharField(max_length=120)
    size_bytes = models.PositiveIntegerField()
    checksum = models.CharField(max_length=128, blank=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "uploads"
        indexes = [
            models.Index(fields=["organization", "type", "created_at"]),
            models.Index(fields=["organization", "status", "created_at"]),
        ]


class CustomDomain(TenantScopedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        FAILED = "failed", "Failed"

    domain = models.CharField(max_length=160, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    txt_record_name = models.CharField(max_length=160)
    txt_record_value = models.CharField(max_length=160)
    verified_at = models.DateTimeField(blank=True, null=True)
    ssl_status = models.CharField(max_length=40, default="pending")

    class Meta:
        db_table = "custom_domains"
        indexes = [models.Index(fields=["organization", "status"])]


class APIKey(TenantScopedModel):
    name = models.CharField(max_length=120)
    prefix = models.CharField(max_length=16, db_index=True)
    key_hash = models.CharField(max_length=128, unique=True)
    scopes = models.JSONField(default=list, blank=True)
    last_used_at = models.DateTimeField(blank=True, null=True)
    revoked_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "api_keys"
        indexes = [models.Index(fields=["organization", "prefix", "revoked_at"])]

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    @property
    def is_authenticated(self) -> bool:
        return self.is_active


class RefreshToken(TimestampedModel):
    class SubjectType(models.TextChoices):
        PLATFORM = "platform", "Platform"
        ORGANIZATION = "organization", "Organization"
        COURIER = "courier", "Courier"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subject_type = models.CharField(max_length=20, choices=SubjectType.choices)
    platform_user = models.ForeignKey(PlatformUser, on_delete=models.CASCADE, blank=True, null=True)
    organization_user = models.ForeignKey(OrganizationUser, on_delete=models.CASCADE, blank=True, null=True)
    courier = models.ForeignKey(Courier, on_delete=models.CASCADE, blank=True, null=True)
    token_hash = models.CharField(max_length=128, unique=True)
    device_name = models.CharField(max_length=120, blank=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(blank=True, null=True)
    rotated_to = models.ForeignKey("self", on_delete=models.SET_NULL, blank=True, null=True)

    class Meta:
        db_table = "refresh_tokens"
        indexes = [
            models.Index(fields=["subject_type", "expires_at"]),
            models.Index(fields=["organization_user", "revoked_at"]),
            models.Index(fields=["platform_user", "revoked_at"]),
            models.Index(fields=["courier", "revoked_at"]),
        ]

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > timezone.now()


class LoginSecurityState(TimestampedModel):
    class SubjectType(models.TextChoices):
        PLATFORM = "platform", "Platform"
        ORGANIZATION = "organization", "Organization"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subject_type = models.CharField(max_length=20, choices=SubjectType.choices)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, blank=True, null=True)
    email = models.EmailField()
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    failure_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(blank=True, null=True)
    last_failure_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "login_security_states"
        constraints = [
            models.UniqueConstraint(
                fields=["subject_type", "organization", "email", "ip_address"],
                name="uniq_login_security_identity",
            ),
        ]
        indexes = [
            models.Index(fields=["subject_type", "email"]),
            models.Index(fields=["organization", "email"]),
            models.Index(fields=["locked_until"]),
        ]

    @property
    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > timezone.now())


class LoginAttempt(TimestampedModel):
    class SubjectType(models.TextChoices):
        PLATFORM = "platform", "Platform"
        ORGANIZATION = "organization", "Organization"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    subject_type = models.CharField(max_length=20, choices=SubjectType.choices)
    organization = models.ForeignKey(Organization, on_delete=models.SET_NULL, blank=True, null=True)
    email = models.EmailField()
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True)
    success = models.BooleanField(default=False)
    mfa_required = models.BooleanField(default=False)
    failure_reason = models.CharField(max_length=80, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "login_attempts"
        indexes = [
            models.Index(fields=["subject_type", "email", "created_at"]),
            models.Index(fields=["organization", "created_at"]),
            models.Index(fields=["ip_address", "created_at"]),
            models.Index(fields=["success", "created_at"]),
        ]


class AuditLog(TimestampedModel):
    class ActorType(models.TextChoices):
        PLATFORM = "platform", "Platform"
        ORGANIZATION = "organization", "Organization"
        API_KEY = "api_key", "API key"
        SYSTEM = "system", "System"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.SET_NULL, blank=True, null=True)
    actor_type = models.CharField(max_length=20, choices=ActorType.choices)
    actor_id = models.CharField(max_length=64, blank=True, null=True)
    action = models.CharField(max_length=100)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "audit_logs"
        indexes = [
            models.Index(fields=["organization", "created_at"]),
            models.Index(fields=["actor_type", "actor_id"]),
            models.Index(fields=["action", "created_at"]),
        ]


class ImpersonationSession(TimestampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ENDED = "ended", "Ended"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    platform_user = models.ForeignKey(PlatformUser, on_delete=models.CASCADE)
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    allowed_permissions = models.JSONField(default=list, blank=True)
    starts_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    ended_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "impersonation_sessions"
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["platform_user", "status"]),
        ]

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE and self.expires_at > timezone.now() and self.ended_at is None
