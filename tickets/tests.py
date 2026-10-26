from django.test import TestCase
from rest_framework.test import APIClient

from tickets.models import Ticket


class TicketAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        Ticket.objects.create(
            title="Payment failed at checkout",
            description="Synthetic test ticket.",
            category=Ticket.Category.PAYMENT,
            status=Ticket.Status.OPEN,
            customer_email="test@example.com",
        )

    def test_ticket_list_returns_ticket(self):
        response = self.client.get("/api/tickets/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)

    def test_summary_counts_unresolved_tickets(self):
        response = self.client.get("/api/tickets/summary/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_tickets"], 1)
        self.assertEqual(response.data["unresolved_tickets"], 1)

    def test_invalid_category_returns_400(self):
        response = self.client.get("/api/tickets/?category=unknown")

        self.assertEqual(response.status_code, 400)