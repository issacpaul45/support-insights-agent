from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0001_initial"),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name="ticket",
            name="ticket_category_status_idx",
        ),
        migrations.AddIndex(
            model_name="ticket",
            index=models.Index(
                fields=["category", "status", "-created_at"],
                name="ticket_filter_recent_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="ticket",
            index=models.Index(
                fields=["status", "-created_at"],
                name="ticket_status_recent_idx",
            ),
        ),
    ]
