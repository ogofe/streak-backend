"""Seed Streak with two African organizations and full demo data.

Idempotent (safe to re-run) and works on both sqlite and Postgres. Creates, per
organization: branches, the default RBAC roles, 3 staff accounts, 3 customers,
4 couriers/riders and 8 deliveries spanning different statuses. The first
organization runs two branches.
"""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import (
    Branch,
    Courier,
    Customer,
    Delivery,
    Organization,
    OrganizationUser,
    PlatformPermission,
    PlatformRole,
    PlatformUser,
)
from core.security import hash_password
from core.services import ensure_default_roles


PASSWORD = "ChangeMe123!"

PLATFORM_PERMISSIONS = [
    "manage_organizations",
    "suspend_organization",
    "manage_billing",
    "impersonate_tenant",
    "view_platform_metrics",
]

# Eight deliveries per org, each with a distinct status.
# (status, delivery_type, courier_index | None)
STATUS_PLAN = [
    ("requested", "dropoff", None),
    ("pending", "pickup", None),
    ("assigned", "dropoff", 0),
    ("picked_up", "pickup", 2),
    ("in_transit", "dropoff", 0),
    ("delivered", "pickup", 1),
    ("failed", "dropoff", 3),
    ("cancelled", "pickup", None),
]

ORG_SPECS = [
    {
        # Nigeria — two branches.
        "slug": "swift-couriers",
        "name": "Swift Couriers",
        "subdomain": "swift",
        "plan": "Growth",
        "brand_color": "#16a34a",
        "initials": "SC",
        "country": "Nigeria",
        "currency": "NGN",
        "ref_prefix": "SWF",
        "base_fee": 1500,
        "fee_step": 250,
        "branches": [
            {"code": "kaduna", "name": "Kaduna Branch", "state": "Kaduna", "city": "Kaduna",
             "address": "Ahmadu Bello Way, Kaduna", "is_default": True},
            {"code": "abuja", "name": "Abuja Branch", "state": "FCT", "city": "Abuja",
             "address": "Central Business District, Abuja", "is_default": False},
        ],
        "staff": [
            {"email": "owner@swiftcouriers.com", "name": "Swift Owner", "initials": "SO",
             "role": "owner", "branch": None},
            {"email": "manager.kaduna@swiftcouriers.com", "name": "Kaduna Manager", "initials": "KM",
             "role": "branch_manager", "branch": "kaduna"},
            {"email": "manager.abuja@swiftcouriers.com", "name": "Abuja Manager", "initials": "AM",
             "role": "dispatcher", "branch": "abuja"},
        ],
        "customers": [
            {"name": "Bisi Adeyemi", "phone": "+2348030001001", "email": "bisi.adeyemi@example.com",
             "zone": "Kaduna Central", "status": "active", "branch": "kaduna"},
            {"name": "Chidi Okeke", "phone": "+2348030001002", "email": "chidi.okeke@example.com",
             "zone": "Abuja Metro", "status": "vip", "branch": "abuja"},
            {"name": "Halima Sani", "phone": "+2348030001003", "email": "halima.sani@example.com",
             "zone": "Kaduna Central", "status": "new", "branch": "kaduna"},
        ],
        "couriers": [
            {"name": "Amina Yusuf", "initials": "AY", "phone": "+2348010005101", "branch": "kaduna",
             "status": "delivering", "zone": "Kaduna Central", "vehicle": "Motorbike KD-41",
             "location": "Ahmadu Bello Way, Kaduna", "lat": "10.523000", "lng": "7.438000",
             "battery": 84, "completion": 96, "email": "amina.yusuf2@swiftcouriers.com"},
            {"name": "Musa Bello", "initials": "MB", "phone": "+2348010005102", "branch": "abuja",
             "status": "available", "zone": "Abuja Metro", "vehicle": "Motorbike ABJ-22",
             "location": "Wuse 2, Abuja", "lat": "9.082000", "lng": "7.401000",
             "battery": 91, "completion": 94, "email": "musa.bello2@swiftcouriers.com"},
            {"name": "Ngozi Eze", "initials": "NE", "phone": "+2348010005103", "branch": "kaduna",
             "status": "delivering", "zone": "Kaduna Central", "vehicle": "Van KD-09",
             "location": "Barnawa, Kaduna", "lat": "10.505000", "lng": "7.410000",
             "battery": 67, "completion": 92, "email": "ngozi.eze@swiftcouriers.com"},
            {"name": "Tunde Bakare", "initials": "TB", "phone": "+2348010005104", "branch": "abuja",
             "status": "offline", "zone": "Abuja Metro", "vehicle": "Motorbike ABJ-77",
             "location": "Garki, Abuja", "lat": "9.070000", "lng": "7.390000",
             "battery": 40, "completion": 88, "email": "tunde.bakare@swiftcouriers.com"},
        ],
        "addresses": [
            ("12 Ahmadu Bello Way, Kaduna", "40 Constitution Ave, Abuja"),
            ("Wuse Market, Abuja", "Barnawa, Kaduna"),
            ("Sabon Gari, Kaduna", "Maitama, Abuja"),
            ("Garki, Abuja", "Kawo, Kaduna"),
        ],
    },
    {
        # Kenya — single branch.
        "slug": "baobab-logistics",
        "name": "Baobab Logistics",
        "subdomain": "baobab",
        "plan": "Starter",
        "brand_color": "#f97316",
        "initials": "BL",
        "country": "Kenya",
        "currency": "KES",
        "ref_prefix": "BAO",
        "base_fee": 350,
        "fee_step": 60,
        "branches": [
            {"code": "nairobi", "name": "Nairobi Branch", "state": "Nairobi", "city": "Nairobi",
             "address": "Kenyatta Avenue, Nairobi", "is_default": True},
        ],
        "staff": [
            {"email": "owner@baobablogistics.co.ke", "name": "Baobab Owner", "initials": "BO",
             "role": "owner", "branch": None},
            {"email": "dispatch@baobablogistics.co.ke", "name": "Nairobi Dispatcher", "initials": "ND",
             "role": "dispatcher", "branch": "nairobi"},
            {"email": "support@baobablogistics.co.ke", "name": "Nairobi Support", "initials": "NS",
             "role": "customer_support", "branch": "nairobi"},
        ],
        "customers": [
            {"name": "Wanjiku Kamau", "phone": "+254700100201", "email": "wanjiku.kamau@example.com",
             "zone": "Westlands", "status": "active", "branch": "nairobi"},
            {"name": "Otieno Omondi", "phone": "+254700100202", "email": "otieno.omondi@example.com",
             "zone": "Kilimani", "status": "vip", "branch": "nairobi"},
            {"name": "Achieng Were", "phone": "+254700100203", "email": "achieng.were@example.com",
             "zone": "Karen", "status": "new", "branch": "nairobi"},
        ],
        "couriers": [
            {"name": "Brian Mwangi", "initials": "BM", "phone": "+254700200301", "branch": "nairobi",
             "status": "delivering", "zone": "Westlands", "vehicle": "Motorbike KBZ-21",
             "location": "Westlands, Nairobi", "lat": "-1.267000", "lng": "36.803000",
             "battery": 80, "completion": 95, "email": "brian.mwangi@baobablogistics.co.ke"},
            {"name": "Faith Njeri", "initials": "FN", "phone": "+254700200302", "branch": "nairobi",
             "status": "available", "zone": "Kilimani", "vehicle": "Motorbike KCA-88",
             "location": "Kilimani, Nairobi", "lat": "-1.290000", "lng": "36.785000",
             "battery": 88, "completion": 93, "email": "faith.njeri@baobablogistics.co.ke"},
            {"name": "Kevin Otieno", "initials": "KO", "phone": "+254700200303", "branch": "nairobi",
             "status": "delivering", "zone": "Karen", "vehicle": "Van KDG-12",
             "location": "Karen, Nairobi", "lat": "-1.319000", "lng": "36.706000",
             "battery": 72, "completion": 90, "email": "kevin.otieno@baobablogistics.co.ke"},
            {"name": "Mercy Wambui", "initials": "MW", "phone": "+254700200304", "branch": "nairobi",
             "status": "offline", "zone": "Parklands", "vehicle": "Motorbike KBL-45",
             "location": "Parklands, Nairobi", "lat": "-1.263000", "lng": "36.809000",
             "battery": 45, "completion": 87, "email": "mercy.wambui@baobablogistics.co.ke"},
        ],
        "addresses": [
            ("Kenyatta Ave, Nairobi", "Westlands, Nairobi"),
            ("Kilimani, Nairobi", "Karen, Nairobi"),
            ("CBD, Nairobi", "Lavington, Nairobi"),
            ("Upperhill, Nairobi", "Parklands, Nairobi"),
        ],
    },
]


class Command(BaseCommand):
    help = "Seed two African organizations with branches, staff, customers, couriers and deliveries."

    @transaction.atomic
    def handle(self, *args, **options):
        self._seed_platform_admin()
        now = timezone.now()
        for spec in ORG_SPECS:
            self._seed_organization(spec, now)
        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(ORG_SPECS)} organizations with branches, staff, customers, couriers and deliveries."
        ))
        self.stdout.write("Logins (password for everyone: ChangeMe123!):")
        self.stdout.write("  Platform admin: admin@streak.local")
        for spec in ORG_SPECS:
            self.stdout.write(f"  {spec['name']} ({spec['country']}) — subdomain '{spec['subdomain']}':")
            for member in spec["staff"]:
                self.stdout.write(f"    - {member['role']}: {member['email']}")

    # ------------------------------------------------------------------ platform
    def _seed_platform_admin(self):
        platform_permissions = {}
        for code in PLATFORM_PERMISSIONS:
            platform_permissions[code], _ = PlatformPermission.objects.get_or_create(code=code)
        super_admin, _ = PlatformRole.objects.get_or_create(key="super_admin", defaults={"label": "Super Admin"})
        super_admin.permissions.set(platform_permissions.values())

        user_model = get_user_model()
        auth_user, created = user_model.objects.get_or_create(
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
            auth_user.set_password(PASSWORD)
            auth_user.save(update_fields=["password"])
        elif not auth_user.is_staff or not auth_user.is_active:
            auth_user.is_staff = True
            auth_user.is_active = True
            auth_user.save(update_fields=["is_staff", "is_active"])

        PlatformUser.objects.get_or_create(
            user=auth_user,
            defaults={
                "email": auth_user.email,
                "name": "Platform Admin",
                "role": super_admin,
                "status": PlatformUser.Status.ACTIVE,
            },
        )

    # -------------------------------------------------------------- organization
    def _seed_organization(self, spec, now):
        org, _ = Organization.objects.get_or_create(
            slug=spec["slug"],
            defaults={
                "name": spec["name"],
                "subdomain": spec["subdomain"],
                "subscription_plan": spec["plan"],
                "branding": {"brand_color": spec["brand_color"], "initials": spec["initials"]},
                "metadata": {
                    "settings": {"currency": spec["currency"]},
                    "onboarding": {
                        "country": spec["country"],
                        "currency": spec["currency"],
                        "location": spec["branches"][0]["city"],
                    },
                },
            },
        )

        roles = ensure_default_roles(org)

        branches = {}
        for blueprint in spec["branches"]:
            branch, _ = Branch.objects.get_or_create(
                organization=org,
                code=blueprint["code"],
                defaults={
                    "name": blueprint["name"],
                    "state": blueprint["state"],
                    "city": blueprint["city"],
                    "address": blueprint["address"],
                    "is_default": blueprint["is_default"],
                },
            )
            if branch.is_default != blueprint["is_default"]:
                branch.is_default = blueprint["is_default"]
                branch.save(update_fields=["is_default", "updated_at"])
            branches[blueprint["code"]] = branch

        for member in spec["staff"]:
            OrganizationUser.objects.get_or_create(
                organization=org,
                email=member["email"],
                defaults={
                    "name": member["name"],
                    "initials": member["initials"],
                    "role": roles[member["role"]],
                    "branch": branches.get(member["branch"]) if member["branch"] else None,
                    "status": OrganizationUser.Status.ACTIVE,
                    "password_hash": hash_password(PASSWORD),
                },
            )

        customers = []
        for entry in spec["customers"]:
            customer, _ = Customer.objects.get_or_create(
                organization=org,
                email=entry["email"],
                defaults={
                    "name": entry["name"],
                    "phone": entry["phone"],
                    "zone": entry["zone"],
                    "initials": self._initials(entry["name"]),
                    "branch": branches.get(entry["branch"]),
                    "status": entry["status"],
                },
            )
            customers.append(customer)

        couriers = []
        for entry in spec["couriers"]:
            courier, _ = Courier.objects.get_or_create(
                organization=org,
                phone=entry["phone"],
                defaults={
                    "name": entry["name"],
                    "initials": entry["initials"],
                    "status": entry["status"],
                    "zone": entry["zone"],
                    "vehicle": entry["vehicle"],
                    "branch": branches.get(entry["branch"]),
                    "current_location": entry["location"],
                    "current_latitude": Decimal(entry["lat"]),
                    "current_longitude": Decimal(entry["lng"]),
                    "location_updated_at": now,
                    "battery_level": entry["battery"],
                    "completion_rate": entry["completion"],
                    "email": entry["email"],
                    "password_hash": hash_password(PASSWORD),
                },
            )
            couriers.append(courier)

        branch_list = list(branches.values())
        base_fee = Decimal(spec["base_fee"])
        fee_step = Decimal(spec["fee_step"])
        for index, (status, delivery_type, courier_index) in enumerate(STATUS_PLAN):
            customer = customers[index % len(customers)]
            branch = branch_list[index % len(branch_list)]
            courier = couriers[courier_index] if courier_index is not None else None
            pickup, dropoff = spec["addresses"][index % len(spec["addresses"])]
            Delivery.objects.get_or_create(
                organization=org,
                reference=f"{spec['ref_prefix']}-{1001 + index}",
                defaults={
                    "customer": customer,
                    "courier": courier,
                    "branch": branch,
                    "customer_name": customer.name,
                    "customer_phone": customer.phone,
                    "pickup_address": pickup,
                    "delivery_address": dropoff,
                    "zone": customer.zone,
                    "delivery_type": delivery_type,
                    "status": status,
                    "source": "dashboard",
                    "delivery_fee": base_fee + fee_step * index,
                    **self._status_timestamps(status, now),
                },
            )

    # --------------------------------------------------------------------- utils
    @staticmethod
    def _initials(name):
        return "".join(part[0] for part in name.split()[:2]).upper()

    @staticmethod
    def _status_timestamps(status, now):
        fields = {
            "accepted_at": None,
            "picked_up_at": None,
            "completed_at": None,
            "failed_at": None,
            "cancelled_at": None,
            "scheduled_time": None,
        }
        if status in {"accepted", "picked_up", "in_transit", "delivered", "failed"}:
            fields["accepted_at"] = now - timedelta(hours=2)
        if status in {"picked_up", "in_transit", "delivered"}:
            fields["picked_up_at"] = now - timedelta(hours=1, minutes=30)
        if status == "delivered":
            fields["completed_at"] = now - timedelta(minutes=30)
        if status == "failed":
            fields["failed_at"] = now - timedelta(minutes=45)
        if status == "cancelled":
            fields["cancelled_at"] = now - timedelta(hours=1)
        if status in {"requested", "pending", "assigned"}:
            fields["scheduled_time"] = now + timedelta(hours=2)
        return fields
