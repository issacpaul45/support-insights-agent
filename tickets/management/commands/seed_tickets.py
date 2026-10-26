import random

from django.core.management.base import BaseCommand

from tickets.models import Ticket


SAMPLES = [
    (
        "Payment failed at checkout",
        "Customer reports checkout payment failure.",
        Ticket.Category.PAYMENT,
    ),
    (
        "Payment pending after charge",
        "Payment is charged but order remains pending.",
        Ticket.Category.PAYMENT,
    ),
    (
        "Unable to reset password",
        "Password reset email was not received.",
        Ticket.Category.LOGIN,
    ),
    (
        "Account login rejected",
        "Valid credentials are rejected during login.",
        Ticket.Category.LOGIN,
    ),
    (
        "Incorrect subscription charge",
        "Customer reports an unexpected subscription charge.",
        Ticket.Category.BILLING,
    ),
    (
        "Invoice not available",
        "Customer cannot download the latest invoice.",
        Ticket.Category.BILLING,
    ),
    (
        "General support request",
        "Customer requests help with account settings.",
        Ticket.Category.OTHER,
    ),
]


class Command(BaseCommand):
    help = "Create synthetic support tickets for local development."

    def handle(self, *args, **options):
        if Ticket.objects.exists():
            self.stdout.write(
                self.style.WARNING("Tickets already exist; no data added.")
            )
            return

        rng = random.Random(42)
        statuses = list(Ticket.Status.values)

        tickets = []
        for index in range(100):
            title, description, category = rng.choice(SAMPLES)

            tickets.append(
                Ticket(
                    title=title,
                    description=description,
                    category=category,
                    status=rng.choices(
                        statuses, weights=[4, 3, 3], k=1
                    )[0],
                    customer_email=f"customer{index + 1}@example.com",
                )
            )

        Ticket.objects.bulk_create(tickets)

        self.stdout.write(
            self.style.SUCCESS(f"Created {len(tickets)} synthetic tickets.")
        )
