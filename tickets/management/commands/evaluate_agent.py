import json
import math
from statistics import median

from django.core.management.base import BaseCommand, CommandError

from tickets.evaluations import (
    expected_facts,
    load_evaluation_cases,
    validate_agent_result,
)


class Command(BaseCommand):
    help = "Run the optional live, database-grounded evaluation of the OpenAI agent."

    def add_arguments(self, parser):
        parser.add_argument(
            "--live",
            action="store_true",
            help="Make live Responses API calls (billable) for the evaluation cases.",
        )
        parser.add_argument(
            "--max-latency-ms",
            type=int,
            default=30000,
            help="Maximum acceptable end-to-end latency for each case (default: 30000).",
        )

    def handle(self, *args, **options):
        if not options["live"]:
            raise CommandError(
                "This evaluation makes paid OpenAI calls. Re-run with --live to start it."
            )
        max_latency_ms = options["max_latency_ms"]
        if max_latency_ms < 1:
            raise CommandError("--max-latency-ms must be greater than zero.")

        from tickets.agent import ask_agent

        cases = load_evaluation_cases()
        report = []
        latencies = []
        request_failures = 0
        evaluation_data_failures = 0
        quality_failures = 0

        for case in cases:
            try:
                facts = expected_facts(case)
            except Exception as exc:
                evaluation_data_failures += 1
                report.append(
                    {
                        "id": case["id"],
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "phase": "expected_data",
                    }
                )
                continue
            if case["check"] == "empty_search" and facts["tickets"]:
                report.append({"id": case["id"], "status": "skipped", "issues": [
                    "evaluation database contains matching tickets for the empty-result case"
                ]})
                continue

            try:
                result = ask_agent(case["question"])
            except Exception as exc:
                request_failures += 1
                report.append(
                    {
                        "id": case["id"],
                        "status": "failed",
                        "error_type": type(exc).__name__,
                    }
                )
                continue

            latency = result.get("latency_ms")
            if isinstance(latency, int):
                latencies.append(latency)
            issues = validate_agent_result(
                case,
                result,
                facts,
                max_latency_ms=max_latency_ms,
            )
            if len(issues) == 1 and issues[0].startswith("skipped:"):
                report.append({"id": case["id"], "status": "skipped", "issues": issues})
                continue

            status = "passed" if not issues else "failed"
            if issues:
                quality_failures += 1
            report.append({"id": case["id"], "status": status, "issues": issues})

        attempted = sum(case["status"] != "skipped" for case in report)
        case_failures = sum(case["status"] == "failed" for case in report)
        summary = {
            "cases_defined": len(cases),
            "cases_attempted": attempted,
            "cases_skipped": sum(case["status"] == "skipped" for case in report),
            "case_failures": case_failures,
            "case_failure_rate": round(case_failures / attempted, 3) if attempted else 0.0,
            "request_failures": request_failures,
            "evaluation_data_failures": evaluation_data_failures,
            "request_failure_rate": round(request_failures / attempted, 3) if attempted else 0.0,
            "quality_failures": quality_failures,
            "quality_failure_rate": round(quality_failures / attempted, 3) if attempted else 0.0,
            "latency_ms": {
                "median": round(median(latencies)) if latencies else None,
                "p95": sorted(latencies)[max(0, math.ceil(0.95 * len(latencies)) - 1)] if latencies else None,
                "max": max(latencies) if latencies else None,
                "budget": max_latency_ms,
            },
            "results": report,
        }
        self.stdout.write(json.dumps(summary, indent=2))
        if quality_failures:
            raise CommandError("Agent evaluation failed; inspect the JSON report above.")
