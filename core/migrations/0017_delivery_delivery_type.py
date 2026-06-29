from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0016_customer_email_nullable"),
    ]

    operations = [
        migrations.AddField(
            model_name="delivery",
            name="delivery_type",
            field=models.CharField(
                choices=[("pickup", "Pickup"), ("dropoff", "Drop-off")],
                default="dropoff",
                max_length=20,
            ),
        ),
    ]
