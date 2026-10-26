
from django.db import models


class Ticket(models.Model):
    class Category(models.TextChoices):
        PAYMENT = "payment", "Payment"
        LOGIN = "login", "Login"
        BILLING = "billing", "Billing"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        RESOLVED = "resolved", "Resolved"

    title = models.CharField(max_length=200)
    description = models.TextField()
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.OTHER,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    customer_email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["category", "status", "-created_at"],
                name="ticket_filter_recent_idx",
            ),
            models.Index(
                fields=["status", "-created_at"],
                name="ticket_status_recent_idx",
            ),
        ]

    def __str__(self):
        return f"#{self.pk} {self.title}"
