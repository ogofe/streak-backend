from django.db import migrations

# fleet_manager now owns courier management + order assignment (absorbs dispatcher).
FLEET_MANAGER_PERMS = [
    "view_overview", "view_orders", "manage_orders",
    "view_fleet", "manage_fleet", "view_customers", "view_analytics",
]


def merge_dispatcher(apps, schema_editor):
    Organization = apps.get_model("core", "Organization")
    OrganizationRole = apps.get_model("core", "OrganizationRole")
    OrganizationPermission = apps.get_model("core", "OrganizationPermission")
    OrganizationUser = apps.get_model("core", "OrganizationUser")

    for organization in Organization.objects.all():
        perms = {p.code: p for p in OrganizationPermission.objects.filter(organization=organization)}
        for code in FLEET_MANAGER_PERMS:
            if code not in perms:
                perms[code] = OrganizationPermission.objects.create(
                    organization=organization, code=code, description=code.replace("_", " ").title()
                )

        fleet = OrganizationRole.objects.filter(organization=organization, key="fleet_manager").first()
        if fleet is None:
            fleet = OrganizationRole.objects.create(
                organization=organization,
                key="fleet_manager",
                label="Fleet Manager",
                description="Manages couriers and assigns deliveries to them.",
            )
        # fleet_manager gains order management + customer visibility.
        fleet.permissions.set([perms[code] for code in FLEET_MANAGER_PERMS])

        dispatcher = OrganizationRole.objects.filter(organization=organization, key="dispatcher").first()
        if dispatcher is not None:
            # role FK is PROTECT — move staff off dispatcher before deleting it.
            OrganizationUser.objects.filter(organization=organization, role=dispatcher).update(role=fleet)
            dispatcher.permissions.clear()
            dispatcher.delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0018_default_roles_backfill"),
    ]

    operations = [
        migrations.RunPython(merge_dispatcher, migrations.RunPython.noop),
    ]
