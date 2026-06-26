from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.models import Branch, Courier, Organization, TrackingLog
from core.security import hash_password


RIDERS = [
    {
        "name": "Amina Yusuf",
        "initials": "AY",
        "branch_code": "kaduna",
        "status": Courier.Status.DELIVERING,
        "current_latitude": Decimal("10.510464"),
        "current_longitude": Decimal("7.416505"),
        "current_location": "Ahmadu Bello Way, Kaduna",
        "battery_level": 84,
        "active_delivery_count": 2,
        "completion_rate": 96,
        "zone": "Kaduna Central",
        "vehicle": "Motorbike KD-41-TRK",
        "phone": "+2348010004101",
        "email": "amina.yusuf@swiftcouriers.com",
        "password": "ChangeMe123!",
        "rating": Decimal("4.8"),
    },
    {
        "name": "Musa Bello",
        "initials": "MB",
        "branch_code": "abuja",
        "status": Courier.Status.AVAILABLE,
        "current_latitude": Decimal("9.076479"),
        "current_longitude": Decimal("7.398574"),
        "current_location": "Wuse 2, Abuja",
        "battery_level": 91,
        "active_delivery_count": 0,
        "completion_rate": 94,
        "zone": "Abuja Metro",
        "vehicle": "Motorbike ABJ-22-RDR",
        "phone": "+2348010004102",
        "email": "musa.bello@swiftcouriers.com",
        "password": "ChangeMe123!",
        "rating": Decimal("4.7"),
    },
]


class Command(BaseCommand):
    help = "Seed Swift Couriers riders with live GPS data for fleet map testing."

    @transaction.atomic
    def handle(self, *args, **options):
        organization = Organization.objects.filter(slug="swift-couriers").first() or Organization.objects.filter(subdomain="swift").first()
        if not organization:
            raise CommandError("Swift Couriers was not found. Run `python manage.py seed_streak` first.")

        branches = {
            branch.code: branch
            for branch in Branch.objects.filter(organization=organization, status=Branch.Status.ACTIVE)
        }
        missing = sorted({row["branch_code"] for row in RIDERS} - set(branches))
        if missing:
            raise CommandError(f"Missing Swift branch(es): {', '.join(missing)}. Run `python manage.py seed_streak` first.")

        now = timezone.now()
        seeded = []

        for row in RIDERS:
            branch = branches[row["branch_code"]]
            courier = Courier.objects.filter(organization=organization, phone=row["phone"]).first()
            created = courier is None
            if created:
                courier = Courier(organization=organization, phone=row["phone"])

            for field in [
                "name",
                "initials",
                "status",
                "current_latitude",
                "current_longitude",
                "current_location",
                "battery_level",
                "active_delivery_count",
                "completion_rate",
                "zone",
                "vehicle",
                "email",
                "rating",
            ]:
                setattr(courier, field, row[field])
            courier.branch = branch
            courier.password_hash = hash_password(row["password"])
            courier.location_updated_at = now
            courier.metadata = {
                **(courier.metadata or {}),
                "seeded_for": "live_fleet_testing",
                "phone_test_note": "Use the dashboard Fleet page to view this rider on Google Maps.",
            }
            courier.save()

            TrackingLog.objects.create(
                organization=organization,
                courier=courier,
                latitude=row["current_latitude"],
                longitude=row["current_longitude"],
                accuracy=Decimal("8.50"),
                battery_level=row["battery_level"],
                timestamp=now,
            )
            seeded.append((courier, created))

        self.stdout.write(self.style.SUCCESS("Seeded Swift Couriers live fleet riders."))
        self.stdout.write("Dashboard login for phone testing:")
        self.stdout.write("  Organization: swift")
        self.stdout.write("  Owner email: owner@swiftcouriers.com")
        self.stdout.write("  Kaduna manager: manager.kaduna@swiftcouriers.com")
        self.stdout.write("  Abuja manager: manager.abuja@swiftcouriers.com")
        self.stdout.write("  Password: ChangeMe123!")
        self.stdout.write("Seeded riders:")
        for courier, created in seeded:
            action = "created" if created else "updated"
            branch_name = courier.branch.name if courier.branch else "No branch"
            self.stdout.write(
                f"  - {courier.name} ({action}) | {branch_name} | {courier.phone} | "
                f"{courier.current_latitude}, {courier.current_longitude}"
            )
        self.stdout.write("Courier login for phone testing:")
        self.stdout.write("  URL: /courier/login")
        self.stdout.write("  Organization: swift")
        self.stdout.write("  Amina: +2348010004101 or amina.yusuf@swiftcouriers.com")
        self.stdout.write("  Musa: +2348010004102 or musa.bello@swiftcouriers.com")
        self.stdout.write("  Password: ChangeMe123!")
