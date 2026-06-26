from django.contrib import admin

from .models import (
    APIKey,
    AnalyticsEvent,
    AnalyticsSnapshot,
    AuditLog,
    Courier,
    CustomDomain,
    Customer,
    Delivery,
    DeliveryEvent,
    ImpersonationSession,
    LoginAttempt,
    LoginSecurityState,
    Notification,
    NotificationAttempt,
    Organization,
    OrganizationPermission,
    OrganizationRole,
    OrganizationUser,
    PlatformPermission,
    PlatformRole,
    PlatformUser,
    PublicSite,
    PublicSiteBlock,
    PublicSitePage,
    PublicSiteServiceArea,
    RefreshToken,
    TrackingLog,
    Upload,
)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "subdomain", "subscription_plan", "status", "created_at")
    search_fields = ("name", "slug", "subdomain", "custom_domain")
    list_filter = ("status", "subscription_plan")


@admin.register(OrganizationUser)
class OrganizationUserAdmin(admin.ModelAdmin):
    list_display = ("email", "name", "organization", "role", "status", "last_active_at")
    search_fields = ("email", "name", "organization__name")
    list_filter = ("status", "role")


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("reference", "organization", "customer_name", "courier", "status", "delivery_fee", "scheduled_time")
    search_fields = ("reference", "customer_name", "customer_phone")
    list_filter = ("status", "organization")


@admin.register(Courier)
class CourierAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "status", "zone", "battery_level", "active_delivery_count")
    search_fields = ("name", "phone")
    list_filter = ("status", "organization", "zone")


for model in [
    APIKey,
    AnalyticsEvent,
    AnalyticsSnapshot,
    AuditLog,
    CustomDomain,
    Customer,
    DeliveryEvent,
    ImpersonationSession,
    LoginAttempt,
    LoginSecurityState,
    Notification,
    NotificationAttempt,
    OrganizationPermission,
    OrganizationRole,
    PlatformPermission,
    PlatformRole,
    PlatformUser,
    PublicSite,
    PublicSiteBlock,
    PublicSitePage,
    PublicSiteServiceArea,
    RefreshToken,
    TrackingLog,
    Upload,
]:
    admin.site.register(model)
