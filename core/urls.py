from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AnalyticsViewSet,
    AccountPasswordView,
    AccountProfileView,
    APIKeyViewSet,
    BranchViewSet,
    BusinessSignupView,
    CourierLoginView,
    CourierMessageViewSet,
    CourierOwnDeliveriesView,
    CourierOwnDeliveryTransitionView,
    CourierOwnMessageContactsView,
    CourierOwnMessagesView,
    CourierOwnProfileView,
    CourierViewSet,
    CustomerViewSet,
    DeliveryViewSet,
    DomainViewSet,
    GoogleMapsConfigView,
    ImpersonationEndView,
    ImpersonationStartView,
    MFADisableView,
    MFASetupView,
    MFAVerifyView,
    NotificationViewSet,
    OrganizationLoginView,
    OrganizationViewSet,
    PlatformLoginView,
    PlatformMetricsView,
    PlatformOrganizationViewSet,
    PublicDeliveryRequestView,
    PublicDeliveryTrackView,
    PublicSiteBlockViewSet,
    PublicSiteDirectoryView,
    PublicSitePageResolveView,
    PublicSitePageViewSet,
    PublicSiteResolveView,
    PublicSiteView,
    RefreshTokenView,
    RoleViewSet,
    StaffViewSet,
    TrackingViewSet,
    UploadCompleteView,
    UploadIntentView,
    health,
    readiness,
)

router = DefaultRouter()
router.register("organizations", OrganizationViewSet, basename="organizations")
router.register("platform/organizations", PlatformOrganizationViewSet, basename="platform-organizations")
router.register("roles", RoleViewSet, basename="roles")
router.register("branches", BranchViewSet, basename="branches")
router.register("staff", StaffViewSet, basename="staff")
router.register("customers", CustomerViewSet, basename="customers")
router.register("couriers", CourierViewSet, basename="couriers")
router.register("courier-messages", CourierMessageViewSet, basename="courier-messages")
router.register("deliveries", DeliveryViewSet, basename="deliveries")
router.register("tracking", TrackingViewSet, basename="tracking")
router.register("notifications", NotificationViewSet, basename="notifications")
router.register("analytics", AnalyticsViewSet, basename="analytics")
router.register("domains", DomainViewSet, basename="domains")
router.register("api-keys", APIKeyViewSet, basename="api-keys")
router.register("public-site/pages", PublicSitePageViewSet, basename="public-site-pages")
router.register("public-site/blocks", PublicSiteBlockViewSet, basename="public-site-blocks")

urlpatterns = [
    path("health/", health, name="health"),
    path("health/ready/", readiness, name="health-ready"),
    path("platform/metrics/", PlatformMetricsView.as_view(), name="platform-metrics"),
    path("integrations/google-maps/config/", GoogleMapsConfigView.as_view(), name="google-maps-config"),
    path("public-site/", PublicSiteView.as_view(), name="public-site"),
    path("public/sites/", PublicSiteDirectoryView.as_view(), name="public-site-directory"),
    path("public/site/resolve/", PublicSiteResolveView.as_view(), name="public-site-resolve"),
    path("public/site/page/", PublicSitePageResolveView.as_view(), name="public-site-page-resolve"),
    path("public/deliveries/request/", PublicDeliveryRequestView.as_view(), name="public-delivery-request"),
    path("public/deliveries/track/", PublicDeliveryTrackView.as_view(), name="public-delivery-track"),
    path("account/me/", AccountProfileView.as_view(), name="account-profile"),
    path("account/password/", AccountPasswordView.as_view(), name="account-password"),
    path("auth/courier/login/", CourierLoginView.as_view(), name="courier-login"),
    path("courier/me/", CourierOwnProfileView.as_view(), name="courier-own-profile"),
    path("courier/tasks/", CourierOwnDeliveriesView.as_view(), name="courier-own-tasks"),
    path("courier/tasks/<uuid:delivery_id>/transition/", CourierOwnDeliveryTransitionView.as_view(), name="courier-own-task-transition"),
    path("courier/message-contacts/", CourierOwnMessageContactsView.as_view(), name="courier-own-message-contacts"),
    path("courier/messages/", CourierOwnMessagesView.as_view(), name="courier-own-messages"),
    path("auth/business/signup/", BusinessSignupView.as_view(), name="business-signup"),
    path("auth/organization/login/", OrganizationLoginView.as_view(), name="organization-login"),
    path("auth/platform/login/", PlatformLoginView.as_view(), name="platform-login"),
    path("auth/refresh/", RefreshTokenView.as_view(), name="token-refresh"),
    path("auth/mfa/setup/", MFASetupView.as_view(), name="mfa-setup"),
    path("auth/mfa/verify/", MFAVerifyView.as_view(), name="mfa-verify"),
    path("auth/mfa/disable/", MFADisableView.as_view(), name="mfa-disable"),
    path("platform/impersonations/", ImpersonationStartView.as_view(), name="impersonation-start"),
    path("platform/impersonations/<uuid:session_id>/end/", ImpersonationEndView.as_view(), name="impersonation-end"),
    path("uploads/intent/", UploadIntentView.as_view(), name="upload-intent"),
    path("uploads/<uuid:upload_id>/complete/", UploadCompleteView.as_view(), name="upload-complete"),
    path("", include(router.urls)),
]
