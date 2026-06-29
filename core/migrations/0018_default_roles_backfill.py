from django.db import migrations

ALL_PERMISSIONS = [
    "view_overview", "view_orders", "manage_orders", "view_fleet", "manage_fleet",
    "view_staff", "manage_staff", "view_customers", "manage_customers",
    "view_analytics", "view_settings", "manage_settings",
]

ROLE_BLUEPRINTS = [
    ("owner", "Organization Owner", "Full access to the business workspace.", list(ALL_PERMISSIONS)),
    ("branch_manager", "Branch Manager", "Runs day-to-day operations for a single branch.",
     ["view_overview", "view_orders", "manage_orders", "view_fleet", "manage_fleet",
      "view_customers", "manage_customers", "view_analytics", "view_staff"]),
    ("dispatcher", "Dispatcher", "Operational access to orders and fleet.",
     ["view_overview", "view_orders", "manage_orders", "view_fleet", "manage_fleet", "view_customers", "view_analytics"]),
    ("fleet_manager", "Fleet Manager", "Fleet-focused operational access.",
     ["view_overview", "view_orders", "view_fleet", "manage_fleet", "view_analytics"]),
    ("customer_support", "Customer Support", "Customer operations only.",
     ["view_overview", "view_orders", "view_customers", "manage_customers"]),
    ("analyst", "Analyst", "Read-only analytics access.", ["view_overview", "view_analytics"]),
]


def backfill_roles(apps, schema_editor):
    Organization = apps.get_model("core", "Organization")
    OrganizationPermission = apps.get_model("core", "OrganizationPermission")
    OrganizationRole = apps.get_model("core", "OrganizationRole")

    for organization in Organization.objects.all():
        permissions = {}
        for code in ALL_PERMISSIONS:
            permissions[code], _ = OrganizationPermission.objects.get_or_create(
                organization=organization,
                code=code,
                defaults={"description": code.replace("_", " ").title()},
            )
        for key, label, description, codes in ROLE_BLUEPRINTS:
            role, created = OrganizationRole.objects.get_or_create(
                organization=organization,
                key=key,
                defaults={"label": label, "description": description},
            )
            if created:
                role.permissions.set([permissions[code] for code in codes])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0017_delivery_delivery_type"),
    ]

    operations = [
        migrations.RunPython(backfill_roles, migrations.RunPython.noop),
    ]
