from django.urls import path

from .views import AgentView,CommonCausesView, TicketListView, TicketSummaryView

urlpatterns = [
    path("tickets/", TicketListView.as_view(), name="ticket-list"),
    path("tickets/summary/", TicketSummaryView.as_view(), name="ticket-summary"),
    path("tickets/common-causes/", CommonCausesView.as_view(), name="ticket-common-causes"),
    path("agent/", AgentView.as_view(), name="agent"),
]