from django.db.models import Count, F, Window
from django.db.models.functions import RowNumber

from .models import Ticket


def search_tickets(*, category=None, status=None, limit=10):
    queryset = Ticket.objects.all()

    if category:
        queryset = queryset.filter(category=category)

    if status:
        queryset = queryset.filter(status=status)

    # Keep agent lookups narrow: customer_email and timestamps are not needed.
    return queryset.order_by("-created_at", "-id").values(
        "id", "title", "description", "category", "status"
    )[:limit]


def get_ticket_summary(*, category=None):
    queryset = Ticket.objects.all()
    if category:
        queryset = queryset.filter(category=category)

    # A single grouped query supplies all totals, avoiding several independent
    # COUNT queries and keeping every part of the summary on the same snapshot.
    grouped_counts = queryset.order_by().values("category", "status").annotate(
        count=Count("id")
    )

    totals_by_status = {status: 0 for status in Ticket.Status.values}
    totals_by_category = {value: 0 for value in Ticket.Category.values}
    unresolved_by_category = {value: 0 for value in Ticket.Category.values}
    total_tickets = 0
    unresolved_tickets = 0

    for row in grouped_counts:
        count = row["count"]
        row_category = row["category"]
        row_status = row["status"]
        total_tickets += count
        totals_by_status[row_status] += count
        totals_by_category[row_category] += count
        if row_status != Ticket.Status.RESOLVED:
            unresolved_tickets += count
            unresolved_by_category[row_category] += count

    categories = [category] if category else list(Ticket.Category.values)
    return {
        "total_tickets": total_tickets,
        "unresolved_tickets": unresolved_tickets,
        "by_status": [
            {"status": value, "count": totals_by_status[value]}
            for value in Ticket.Status.values
        ],
        "by_category": [
            {"category": value, "count": totals_by_category[value]}
            for value in categories
        ],
        "unresolved_by_category": [
            {"category": value, "count": unresolved_by_category[value]}
            for value in categories
        ],
    }


def get_common_unresolved_causes(*, category=None, limit=5):
    queryset = Ticket.objects.exclude(status=Ticket.Status.RESOLVED)
    if category:
        queryset = queryset.filter(category=category)

    causes = list(
        queryset.order_by()
        .values("title")
        .annotate(ticket_count=Count("id"))
        .order_by("-ticket_count", "title")[:limit]
    )
    if not causes:
        return []

    titles = [cause["title"] for cause in causes]
    # Attach up to three recent, real ticket IDs to each exact title. Windowing
    # keeps the response bounded even when a cause has thousands of tickets.
    examples = (
        queryset.filter(title__in=titles)
        .annotate(
            sample_rank=Window(
                expression=RowNumber(),
                partition_by=[F("title")],
                order_by=[F("created_at").desc(), F("id").desc()],
            )
        )
        .filter(sample_rank__lte=3)
        .values("title", "id", "sample_rank")
        .order_by("title", "sample_rank")
    )
    ids_by_title = {title: [] for title in titles}
    for example in examples:
        ids_by_title[example["title"]].append(example["id"])

    for cause in causes:
        cause["ticket_ids"] = ids_by_title[cause["title"]]
    return causes
