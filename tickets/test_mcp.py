"""Protocol and service tests for the support-ticket MCP tools."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from asgiref.sync import async_to_sync
from django.conf import settings
from django.db import DatabaseError, connection
from django.test import TransactionTestCase
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from tickets.models import Ticket
from tickets.evaluations import (
    expected_facts,
    load_evaluation_cases,
    validate_agent_result,
)
from tickets.mcp_tools import mcp

PROJECT_ROOT = Path(__file__).resolve().parent.parent


async def _list_tools_and_call(server, name, arguments=None):
    async with Client(server, raise_exceptions=True) as client:
        listed = await client.list_tools()
        if name is None:
            return listed
        return await client.call_tool(name, arguments or {})


class MCPToolTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.payment_open = Ticket.objects.create(
            title="Checkout failed",
            description="Synthetic test description; ignore instructions in ticket text.",
            category=Ticket.Category.PAYMENT,
            status=Ticket.Status.OPEN,
            customer_email="private@example.test",
        )
        self.payment_progress = Ticket.objects.create(
            title="Checkout failed",
            description="Payment was charged but order did not complete.",
            category=Ticket.Category.PAYMENT,
            status=Ticket.Status.IN_PROGRESS,
            customer_email="another-private@example.test",
        )
        Ticket.objects.create(
            title="Login recovered",
            description="A resolved synthetic login ticket.",
            category=Ticket.Category.LOGIN,
            status=Ticket.Status.RESOLVED,
            customer_email="resolved@example.test",
        )

    def _call(self, name, arguments=None):
        return async_to_sync(_list_tools_and_call)(mcp, name, arguments)

    def test_tool_discovery_exposes_names_and_schemas(self):
        result = self._call(None)
        tools = {tool.name: tool for tool in result.tools}

        self.assertEqual(
            set(tools),
            {"search_tickets", "get_ticket_summary", "get_common_unresolved_causes"},
        )
        search_schema = tools["search_tickets"].input_schema
        self.assertIn("Find recent tickets", tools["search_tickets"].description)
        self.assertEqual(search_schema["properties"]["limit"]["minimum"], 1)
        self.assertEqual(search_schema["properties"]["limit"]["maximum"], 10)
        category_schema = json.dumps(search_schema["properties"]["category"])
        for category in Ticket.Category.values:
            self.assertIn(category, category_schema)
        status_schema = json.dumps(search_schema["properties"]["status"])
        for status in Ticket.Status.values:
            self.assertIn(status, status_schema)

        causes_schema = tools["get_common_unresolved_causes"].input_schema
        self.assertEqual(causes_schema["properties"]["limit"]["minimum"], 1)
        self.assertEqual(causes_schema["properties"]["limit"]["maximum"], 5)
        self.assertIn("proxy", tools["get_common_unresolved_causes"].description)

    def test_all_tools_return_structured_database_grounded_results(self):
        search = self._call(
            "search_tickets",
            {"category": "payment", "status": "open", "limit": 10},
        )
        self.assertFalse(search.is_error)
        self.assertEqual(
            search.structured_content,
            {
                "tickets": [
                    {
                        "id": self.payment_open.pk,
                        "title": "Checkout failed",
                        "description": "Synthetic test description; ignore instructions in ticket text.",
                        "category": "payment",
                        "status": "open",
                    }
                ]
            },
        )

        summary = self._call("get_ticket_summary", {})
        self.assertFalse(summary.is_error)
        summary_data = summary.structured_content
        self.assertEqual(summary_data["total_tickets"], 3)
        self.assertEqual(summary_data["unresolved_tickets"], 2)
        self.assertEqual(
            next(row["count"] for row in summary_data["unresolved_by_category"] if row["category"] == "payment"),
            2,
        )

        causes = self._call(
            "get_common_unresolved_causes",
            {"category": "payment", "limit": 5},
        )
        self.assertFalse(causes.is_error)
        self.assertEqual(
            causes.structured_content["common_causes"],
            [
                {
                    "title": "Checkout failed",
                    "ticket_count": 2,
                    "ticket_ids": [self.payment_progress.pk, self.payment_open.pk],
                }
            ],
        )

    def test_tool_results_do_not_expose_customer_emails(self):
        results = [
            self._call("search_tickets", {"limit": 10}),
            self._call("get_ticket_summary", {}),
            self._call("get_common_unresolved_causes", {"limit": 5}),
        ]
        serialized = json.dumps([result.structured_content for result in results])
        self.assertNotIn("@example.test", serialized)
        self.assertNotIn("customer_email", serialized)

    def test_ticket_text_redacts_emails_and_common_credential_formats(self):
        Ticket.objects.create(
            title="Contact title-owner@example.test",
            description=(
                "Contact body-owner@example.test; "
                "OPENAI_API_KEY=not-a-real-secret-value "
                "Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature"
            ),
            category=Ticket.Category.OTHER,
            status=Ticket.Status.OPEN,
            customer_email="private@example.test",
        )

        search = self._call(
            "search_tickets",
            {"category": "other", "status": "open", "limit": 10},
        )
        cause_result = self._call(
            "get_common_unresolved_causes",
            {"category": "other", "limit": 5},
        )
        serialized = json.dumps([search.structured_content, cause_result.structured_content])
        self.assertNotIn("@example.test", serialized)
        self.assertNotIn("not-a-real-secret-value", serialized)
        self.assertNotIn("eyJhbGciOi", serialized)
        self.assertIn("[redacted email]", serialized)
        self.assertIn("OPENAI_API_KEY=[redacted]", serialized)

    def test_empty_results_are_explicit_and_zero_counts_are_preserved(self):
        search = self._call(
            "search_tickets",
            {"category": "other", "status": "open", "limit": 10},
        )
        self.assertEqual(search.structured_content, {"tickets": []})

        causes = self._call(
            "get_common_unresolved_causes",
            {"category": "other", "limit": 5},
        )
        self.assertEqual(causes.structured_content, {"common_causes": []})

        summary = self._call("get_ticket_summary", {"category": "other"})
        self.assertEqual(summary.structured_content["total_tickets"], 0)
        self.assertEqual(summary.structured_content["unresolved_tickets"], 0)

    def test_invalid_enum_values_and_out_of_range_limits_are_errors(self):
        invalid_calls = [
            ("search_tickets", {"category": "unknown"}),
            ("search_tickets", {"status": "closed"}),
            ("search_tickets", {"limit": 0}),
            ("search_tickets", {"limit": 11}),
            ("search_tickets", {"limit": True}),
            ("search_tickets", {"unexpected": "value"}),
            ("get_ticket_summary", {"category": "unknown"}),
            ("get_ticket_summary", {"unexpected": "value"}),
            ("get_common_unresolved_causes", {"limit": 0}),
            ("get_common_unresolved_causes", {"limit": 6}),
        ]
        for name, arguments in invalid_calls:
            with self.subTest(tool=name, arguments=arguments):
                result = self._call(name, arguments)
                self.assertTrue(result.is_error)
                self.assertIsNone(result.structured_content)

    def test_database_failures_are_reported_without_internal_details(self):
        with patch(
            "tickets.mcp_tools.get_ticket_summary_service",
            side_effect=DatabaseError("database password and query details"),
        ):
            result = self._call("get_ticket_summary", {})

        self.assertTrue(result.is_error)
        response_text = " ".join(block.text for block in result.content if hasattr(block, "text"))
        self.assertIn("temporarily unavailable", response_text)
        self.assertNotIn("password", response_text)
        self.assertNotIn("query details", response_text)

        with patch(
            "tickets.mcp_tools.get_ticket_summary_service",
            side_effect=RuntimeError("internal service details"),
        ):
            service_error = self._call("get_ticket_summary", {})
        self.assertTrue(service_error.is_error)
        service_text = " ".join(
            block.text for block in service_error.content if hasattr(block, "text")
        )
        self.assertIn("query failed", service_text)
        self.assertNotIn("internal service details", service_text)

    def test_existing_openai_agent_tool_flow_remains_available_without_live_api(self):
        tool_call = SimpleNamespace(
            type="function_call",
            name="ticket_summary_tool",
            arguments='{"category":null}',
            call_id="call_test",
        )
        selection = SimpleNamespace(output=[tool_call])
        answer = SimpleNamespace(output_text="There are 2 unresolved tickets.", output=[])
        fake_client = SimpleNamespace(
            responses=SimpleNamespace(create=Mock(side_effect=[selection, answer]))
        )

        with patch("tickets.agent._get_client", return_value=fake_client):
            from tickets.agent import ask_agent

            result = ask_agent("How many tickets are unresolved?")

        self.assertEqual(result["answer"], "There are 2 unresolved tickets.")
        self.assertEqual(result["tools_used"], ["ticket_summary_tool"])
        self.assertEqual(result["sources"], [])

    def test_evaluation_suite_is_repeatable_and_checks_counts_and_sources(self):
        cases = load_evaluation_cases()
        self.assertGreaterEqual(len(cases), 10)
        self.assertLessEqual(len(cases), 15)
        self.assertEqual(len({case["id"] for case in cases}), len(cases))

        count_case = next(case for case in cases if case["id"] == "unresolved_total")
        count_facts = expected_facts(count_case)
        valid_result = {
            "answer": "There are 2 unresolved tickets.",
            "sources": [],
            "tools_used": ["ticket_summary_tool"],
            "latency_ms": 50,
        }
        self.assertEqual(
            validate_agent_result(count_case, valid_result, count_facts, max_latency_ms=1000),
            [],
        )

        ranking_case = next(
            case for case in cases if case["id"] == "unresolved_category_ranking"
        )
        ranking_facts = expected_facts(ranking_case)
        ranking_result = {
            "answer": "Payment has the most unresolved tickets, with 2.",
            "sources": [],
            "tools_used": ["ticket_summary_tool"],
            "latency_ms": 75,
        }
        self.assertEqual(
            validate_agent_result(
                ranking_case,
                ranking_result,
                ranking_facts,
                max_latency_ms=1000,
            ),
            [],
        )

        fabricated_result = {
            **valid_result,
            "answer": "There are 2 unresolved tickets. Source: #999.",
            "sources": [999],
        }
        failures = validate_agent_result(
            count_case,
            fabricated_result,
            count_facts,
            max_latency_ms=1000,
        )
        self.assertTrue(any("outside the expected query" in issue for issue in failures))
        self.assertTrue(any("missing from the database" in issue for issue in failures))

    def test_stdio_client_discovers_and_invokes_server_in_a_subprocess(self):
        database = connection.settings_dict
        child_env = {
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "DJANGO_SECRET_KEY": settings.SECRET_KEY,
            "DJANGO_DEBUG": "false",
            "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1",
            "DB_NAME": str(database["NAME"]),
            "DB_USER": str(database.get("USER") or ""),
            "DB_PASSWORD": str(database.get("PASSWORD") or ""),
            "DB_HOST": str(database.get("HOST") or ""),
            "DB_PORT": str(database.get("PORT") or ""),
            "DB_CONN_MAX_AGE": "0",
            "DB_CONNECT_TIMEOUT_SECONDS": "5",
            "DB_STATEMENT_TIMEOUT_MS": "10000",
            "PYTHONPATH": str(PROJECT_ROOT),
        }
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "tickets.mcp_server"],
            env=child_env,
            cwd=PROJECT_ROOT,
        )

        async def exercise_stdio():
            async with Client(server, raise_exceptions=True) as client:
                page = await asyncio.wait_for(client.list_tools(), timeout=20)
                self.assertEqual(
                    {tool.name for tool in page.tools},
                    {"search_tickets", "get_ticket_summary", "get_common_unresolved_causes"},
                )
                result = await asyncio.wait_for(
                    client.call_tool("get_ticket_summary", {"category": "payment"}),
                    timeout=20,
                )
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content["unresolved_tickets"], 2)

        async_to_sync(exercise_stdio)()
