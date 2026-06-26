from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Branch, Organization, OrganizationPermission, OrganizationRole, OrganizationUser, PlatformPermission, PlatformRole, PlatformUser
from core.security import hash_password


ORG_PERMISSIONS = [
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


class Command(BaseCommand):
    help = "Seed baseline Streak platform and tenant RBAC data."

    @transaction.atomic
    def handle(self, *args, **options):
        platform_permissions = {}
        for code in ["manage_organizations", "suspend_organization", "manage_billing", "impersonate_tenant", "view_platform_metrics"]:
            platform_permissions[code], _ = PlatformPermission.objects.get_or_create(code=code)
        super_admin, _ = PlatformRole.objects.get_or_create(key="super_admin", defaults={"label": "Super Admin"})
        super_admin.permissions.set(platform_permissions.values())
        user_model = get_user_model()
        platform_auth_user, created = user_model.objects.get_or_create(
            username="admin@streak.local",
            defaults={
                "email": "admin@streak.local",
                "first_name": "Platform",
                "last_name": "Admin",
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )
        if created:
            platform_auth_user.set_password("ChangeMe123!")
            platform_auth_user.save(update_fields=["password"])
        elif not platform_auth_user.is_staff or not platform_auth_user.is_active:
            platform_auth_user.is_staff = True
            platform_auth_user.is_active = True
            platform_auth_user.save(update_fields=["is_staff", "is_active"])
        PlatformUser.objects.get_or_create(
            user=platform_auth_user,
            defaults={
                "email": platform_auth_user.email,
                "name": "Platform Admin",
                "role": super_admin,
                "status": PlatformUser.Status.ACTIVE,
            },
        )
        org, _ = Organization.objects.get_or_create(
            slug="swift-couriers",
            defaults={
                "name": "Swift Couriers",
                "subdomain": "swift",
                "subscription_plan": "Growth",
                "branding": {"brand_color": "#16a34a", "initials": "SC"},
            },
        )
        kaduna, _ = Branch.objects.get_or_create(
            organization=org,
            code="kaduna",
            defaults={
                "name": "Kaduna Branch",
                "state": "Kaduna",
                "city": "Kaduna",
                "address": "Ahmadu Bello Way, Kaduna",
                "is_default": True,
            },
        )
        abuja, _ = Branch.objects.get_or_create(
            organization=org,
            code="abuja",
            defaults={
                "name": "Abuja Branch",
                "state": "FCT",
                "city": "Abuja",
                "address": "Central Business District, Abuja",
                "is_default": False,
            },
        )
        if kaduna.is_default and abuja.is_default:
            abuja.is_default = False
            abuja.save(update_fields=["is_default", "updated_at"])
        permissions = {}
        for code in ORG_PERMISSIONS:
            permissions[code], _ = OrganizationPermission.objects.get_or_create(organization=org, code=code)
        owner, _ = OrganizationRole.objects.get_or_create(
            organization=org,
            key="owner",
            defaults={"label": "Organization Owner", "description": "Full access to every organization function."},
        )
        owner.permissions.set(permissions.values())
        dispatcher, _ = OrganizationRole.objects.get_or_create(
            organization=org,
            key="dispatcher",
            defaults={"label": "Dispatcher", "description": "Manage delivery operations."},
        )
        dispatcher.permissions.set([permissions["view_orders"], permissions["manage_orders"], permissions["view_fleet"]])
        fleet_manager, _ = OrganizationRole.objects.get_or_create(
            organization=org,
            key="fleet_manager",
            defaults={"label": "Fleet Manager", "description": "Manage riders and fleet availability."},
        )
        fleet_manager.permissions.set([permissions["view_overview"], permissions["view_fleet"], permissions["manage_fleet"], permissions["view_orders"]])
        customer_support, _ = OrganizationRole.objects.get_or_create(
            organization=org,
            key="customer_support",
            defaults={"label": "Customer Support", "description": "Support customers and inspect orders."},
        )
        customer_support.permissions.set([permissions["view_overview"], permissions["view_orders"], permissions["view_customers"], permissions["manage_customers"]])
        analyst, _ = OrganizationRole.objects.get_or_create(
            organization=org,
            key="analyst",
            defaults={"label": "Analyst", "description": "Read-only analytics access."},
        )
        analyst.permissions.set([permissions["view_overview"], permissions["view_orders"], permissions["view_fleet"], permissions["view_customers"], permissions["view_analytics"]])

        OrganizationUser.objects.get_or_create(
            organization=org,
            email="owner@swiftcouriers.com",
            defaults={
                "name": "Swift Owner",
                "initials": "SO",
                "role": owner,
                "status": OrganizationUser.Status.ACTIVE,
                "password_hash": hash_password("ChangeMe123!"),
            },
        )
        OrganizationUser.objects.get_or_create(
            organization=org,
            email="manager.kaduna@swiftcouriers.com",
            defaults={
                "name": "Kaduna Manager",
                "initials": "KM",
                "branch": kaduna,
                "role": dispatcher,
                "status": OrganizationUser.Status.ACTIVE,
                "password_hash": hash_password("ChangeMe123!"),
            },
        )
        OrganizationUser.objects.get_or_create(
            organization=org,
            email="manager.abuja@swiftcouriers.com",
            defaults={
                "name": "Abuja Manager",
                "initials": "AM",
                "branch": abuja,
                "role": dispatcher,
                "status": OrganizationUser.Status.ACTIVE,
                "password_hash": hash_password("ChangeMe123!"),
            },
        )
        OrganizationUser.objects.filter(organization=org, branch__isnull=True).exclude(role__key="owner").update(branch=kaduna)
        self.stdout.write(self.style.SUCCESS("Seeded Streak baseline data."))
