import logging

from django.db import DatabaseError
from openai import APIConnectionError, APITimeoutError, OpenAIError, RateLimitError
from rest_framework import status
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import Ticket
from .serializers import AgentQuestionSerializer, TicketSerializer
from .services import get_common_unresolved_causes, get_ticket_summary
from .agent import (
    AgentConfigurationError,
    AgentExecutionError,
    ask_agent,
)

logger = logging.getLogger(__name__)

class TicketPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 50


class TicketListView(generics.ListAPIView):
    serializer_class = TicketSerializer
    permission_classes = [AllowAny]
    pagination_class = TicketPagination

    def get_queryset(self):
        queryset = Ticket.objects.defer("customer_email")

        category = self.request.query_params.get("category")
        status = self.request.query_params.get("status")

        valid_categories = Ticket.Category.values
        valid_statuses = Ticket.Status.values

        if category:
            if category not in valid_categories:
                raise ValidationError({"category": "Invalid category."})
            queryset = queryset.filter(category=category)

        if status:
            if status not in valid_statuses:
                raise ValidationError({"status": "Invalid status."})
            queryset = queryset.filter(status=status)

        return queryset.order_by("-created_at", "-id")


class TicketSummaryView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        category = request.query_params.get("category")

        if category and category not in Ticket.Category.values:
            raise ValidationError({"category": "Invalid category."})

        return Response(get_ticket_summary(category=category))


class CommonCausesView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        category = request.query_params.get("category")
        if category and category not in Ticket.Category.values:
            raise ValidationError({"category": "Invalid category."})

        causes = get_common_unresolved_causes(category=category)
        return Response({"common_causes": causes})



class AgentView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "agent"

    def post(self, request):
        serializer = AgentQuestionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        question = serializer.validated_data["question"]

        try:
            result = ask_agent(question)
        except AgentConfigurationError:
            logger.exception("Support insights agent is not configured")
            return Response(
                {
                    "error": "The agent is not configured on the server.",
                    "code": "agent_not_configured",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except (APIConnectionError, APITimeoutError, RateLimitError, DatabaseError):
            logger.exception("Support insights agent dependency failed")
            return Response(
                {
                    "error": "The agent service is temporarily unavailable. Try again shortly.",
                    "code": "agent_unavailable",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except OpenAIError:
            logger.exception("OpenAI rejected the support insights request")
            return Response(
                {
                    "error": "The upstream AI service rejected the request.",
                    "code": "agent_upstream_error",
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except AgentExecutionError:
            logger.exception("Support insights agent returned an invalid tool flow")
            return Response(
                {
                    "error": "The agent could not safely complete this request. Try rephrasing it.",
                    "code": "agent_response_invalid",
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception:
            logger.exception("Support insights agent request failed")
            return Response(
                {
                    "error": "The agent could not complete this request.",
                    "code": "agent_request_failed",
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response({
            "question": question,
            "answer": result["answer"],
            "sources": result["sources"],
            "tools_used": result["tools_used"],
            "model": result["model"],
            "latency_ms": result["latency_ms"],
        })
