from django.db import migrations


TENANT_TABLES = [
    "organization_permissions",
    "organization_roles",
    "organization_users",
    "customers",
    "couriers",
    "deliveries",
    "delivery_events",
    "tracking_logs",
    "notifications",
    "analytics_events",
    "uploads",
    "custom_domains",
    "api_keys",
]


def enable_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in TENANT_TABLES:
            cursor.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            cursor.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
            cursor.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
            cursor.execute(
                f'''
                CREATE POLICY tenant_isolation ON "{table}"
                USING (organization_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
                WITH CHECK (organization_id = NULLIF(current_setting('app.current_org', true), '')::uuid)
                '''
            )


def disable_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for table in TENANT_TABLES:
            cursor.execute(f'DROP POLICY IF EXISTS tenant_isolation ON "{table}"')
            cursor.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(enable_rls, reverse_code=disable_rls),
    ]
