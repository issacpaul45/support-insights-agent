"""Database-grounded checks for the optional live OpenAI agent evaluation."""

import json
import re
from pathlib import Path
from typing import Any

from .models import Ticket
from .services import (
    get_common_unresolved_causes,
    get_ticket_summary,
    search_tickets,
)

EVALUATION_CASES_PATH = Path(__file__).with_name("evaluation_cases.json")
_CITATION_PATTERN = re.compile(r"(?<!\w)#(\d+)\b")
_NUMBER_WORDS = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
}
_TENS_WORDS = {
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
    60: "sixty",
    70: "seventy",
    80: "eighty",
    90: "ninety",
}


def load_evaluation_cases() -> list[dict[str, Any]]:
    """Load the version-controlled natural-language evaluation questions."""
    return json.loads(EVALUATION_CASES_PATH.read_text(encoding="utf-8"))


def expected_facts(case: dict[str, Any]) -> dict[str, Any]:
    """Read exact expected facts through the same service layer as the tools."""
    check = case["check"]
    category = case.get("category")

    if check in {
        "unresolved_count",
        "total_count",
        "category_unresolved_count",
        "category_total_count",
        "category_count_and_causes",
        "category_ranking",
    }:
        facts = {"summary": get_ticket_summary(category=category)}
        if check == "category_count_and_causes":
            causes = get_common_unresolved_causes(
                category=category,
                limit=case.get("limit", 5),
            )
            facts["causes"] = causes
            facts["allowed_ids"] = {
                ticket_id for cause in causes for ticket_id in cause["ticket_ids"]
            }
        return facts

    if check == "common_causes":
        causes = get_common_unresolved_causes(
            category=category,
            limit=case.get("limit", 5),
        )
        return {
            "causes": causes,
            "allowed_ids": {ticket_id for cause in causes for ticket_id in cause["ticket_ids"]},
        }

    if check in {"ticket_search", "empty_search"}:
        tickets = list(
            search_tickets(
                category=category,
                status=case.get("status"),
                limit=case.get("limit", 10),
            )
        )
        return {"tickets": tickets, "allowed_ids": {ticket["id"] for ticket in tickets}}

    raise ValueError(f"Unknown evaluation check: {check}")


def _number_spellings(value: int) -> set[str]:
    if value < 0:
        return set()
    if value in _NUMBER_WORDS:
        return {_NUMBER_WORDS[value]}
    if value < 100:
        tens, remainder = divmod(value, 10)
        prefix = _TENS_WORDS[tens * 10]
        if remainder == 0:
            return {prefix}
        unit = _NUMBER_WORDS[remainder]
        return {
            f"{prefix} {unit}",
            f"{prefix}-{unit}",
        }
    if value >= 1000:
        thousands, remainder = divmod(value, 1000)
        prefix_spellings = _number_spellings(thousands)
        if remainder == 0:
            return {f"{prefix} thousand" for prefix in prefix_spellings}
        remainder_spellings = _number_spellings(remainder)
        return {
            f"{prefix} thousand {remainder_word}"
            for prefix in prefix_spellings
            for remainder_word in remainder_spellings
        } | {
            f"{prefix} thousand and {remainder_word}"
            for prefix in prefix_spellings
            for remainder_word in remainder_spellings
        }
    hundreds, remainder = divmod(value, 100)
    prefix = f"{_NUMBER_WORDS[hundreds]} hundred"
    if remainder == 0:
        return {prefix}
    remainder_spellings = _number_spellings(remainder)
    return {
        f"{prefix} {remainder_word}" for remainder_word in remainder_spellings
    } | {
        f"{prefix} and {remainder_word}" for remainder_word in remainder_spellings
    }


def _mentions_number(text: str, value: int) -> bool:
    numeric_match = re.search(rf"(?<![#\w]){value}(?!\d)", text)
    if numeric_match:
        return True
    if value == 0 and re.search(r"\b(no|none|zero|not any)\b", text, re.IGNORECASE):
        return True
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return any(
        re.search(rf"(?<!\w){re.escape(spelling)}(?!\w)", normalized)
        for spelling in _number_spellings(value)
    )


def _summary_count(case: dict[str, Any], summary: dict[str, Any]) -> int:
    if case["check"] in {
        "unresolved_count",
        "category_unresolved_count",
        "category_count_and_causes",
    }:
        return summary["unresolved_tickets"]
    return summary["total_tickets"]


def _allowed_source_ids(case: dict[str, Any], facts: dict[str, Any]) -> set[int]:
    check = case["check"]
    if check in {"common_causes", "category_count_and_causes", "ticket_search", "empty_search"}:
        return facts["allowed_ids"]
    return set()


def validate_agent_result(
    case: dict[str, Any],
    result: dict[str, Any],
    facts: dict[str, Any],
    *,
    max_latency_ms: int,
) -> list[str]:
    """Return deterministic quality failures for one model answer."""
    failures: list[str] = []
    expected_tools = set(case["expected_tools"])
    actual_tools = list(result.get("tools_used", []))
    if sorted(actual_tools) != sorted(expected_tools):
        failures.append(
            f"tool selection expected {sorted(expected_tools)} but got {sorted(actual_tools)}"
        )

    answer = result.get("answer", "")
    citations = {int(value) for value in _CITATION_PATTERN.findall(answer)}
    sources = set(result.get("sources", []))
    allowed_ids = _allowed_source_ids(case, facts)
    if not citations.issubset(sources):
        failures.append("answer cites ticket IDs absent from the tool results")
    if not sources.issubset(allowed_ids):
        failures.append("tool results contain ticket IDs outside the expected query")
    elif case["check"] in {
        "common_causes",
        "category_count_and_causes",
        "ticket_search",
        "empty_search",
    } and sources != allowed_ids:
        failures.append("tool results did not include all expected ticket IDs for the query")
    if sources and Ticket.objects.filter(pk__in=sources).count() != len(sources):
        failures.append("tool results contain ticket IDs missing from the database")

    check = case["check"]
    if check in {
        "unresolved_count",
        "total_count",
        "category_unresolved_count",
        "category_total_count",
        "category_count_and_causes",
    }:
        count = _summary_count(case, facts["summary"])
        if not _mentions_number(answer, count):
            failures.append(f"answer did not include the expected count {count}")

    if check == "category_ranking":
        counts = facts["summary"]["unresolved_by_category"]
        highest = max((row["count"] for row in counts), default=0)
        winners = [row["category"] for row in counts if row["count"] == highest]
        answer_lower = answer.lower()
        labels = {category.value: category.label.lower() for category in Ticket.Category}
        if highest == 0 and not any(
            phrase in answer_lower for phrase in ("no unresolved", "none", "zero")
        ):
            failures.append("answer did not explain that no category has unresolved tickets")
        if highest > 0 and any(labels[winner] not in answer_lower for winner in winners):
            failures.append(f"answer omitted one or more leading categories: {winners}")
        if not _mentions_number(answer, highest):
            failures.append(f"answer did not include the leading count {highest}")
        if len(winners) > 1 and not any(word in answer_lower for word in ("tie", "tied", "equal")):
            failures.append("answer did not identify the tie for the leading count")

    if check in {"common_causes", "category_count_and_causes"}:
        causes = facts["causes"]
        if causes:
            for cause in causes:
                if cause["title"].lower() not in answer.lower():
                    failures.append(f"answer omitted repeated title {cause['title']!r}")
                if not _mentions_number(answer, cause["ticket_count"]):
                    failures.append(
                        f"answer omitted the count for repeated title {cause['title']!r}"
                    )
        elif not any(
            phrase in answer.lower()
            for phrase in ("no unresolved", "none", "no repeated")
        ):
            failures.append("answer did not explain that no repeated causes were found")

    if check == "ticket_search":
        tickets = facts["tickets"]
        if tickets and not sources:
            failures.append("answer returned no source ticket IDs for a ticket search")
        if not tickets and not any(
            word in answer.lower() for word in ("no tickets", "none", "no matching")
        ):
            failures.append("answer did not explain that the ticket search was empty")

    if check == "empty_search":
        tickets = facts["tickets"]
        if tickets:
            return ["skipped: evaluation database has matching tickets for the empty-result case"]
        if sources or citations:
            failures.append("empty search cited ticket IDs")
        if not any(
            word in answer.lower() for word in ("no tickets", "none", "no matching")
        ):
            failures.append("answer did not explain that the search was empty")

    latency = result.get("latency_ms")
    if not isinstance(latency, int):
        failures.append("response did not report latency_ms")
    elif latency > max_latency_ms:
        failures.append(f"latency {latency} ms exceeded the {max_latency_ms} ms limit")

    return failures
