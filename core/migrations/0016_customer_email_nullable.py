from django.db import migrations, models


def blank_email_to_null(apps, schema_editor):
    Customer = apps.get_model("core", "Customer")
    Customer.objects.filter(email="").update(email=None)


def null_email_to_blank(apps, schema_editor):
    Customer = apps.get_model("core", "Customer")
    Customer.objects.filter(email__isnull=True).update(email="")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0015_publicsitepage_publicsiteblock_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="customer",
            name="email",
            field=models.EmailField(blank=True, null=True, max_length=254),
        ),
        migrations.RunPython(blank_email_to_null, null_email_to_blank),
    ]
