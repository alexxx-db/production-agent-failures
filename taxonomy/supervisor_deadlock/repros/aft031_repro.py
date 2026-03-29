"""
AFT-031 Repro: Subagent Silent Failure

Demonstrates how a subagent that catches exceptions and returns empty results
causes the supervisor to misinterpret failures as valid empty responses.

Run: python aft031_repro.py
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class CRMAPIError(Exception):
    pass


@dataclass
class AgentResponse:
    """Structured response envelope for subagent-to-supervisor communication."""
    status: str  # "success" | "error"
    data: Any = None
    error_message: str | None = None
    error_code: str | None = None


class CRMTool:
    """Simulates a CRM API that is currently down."""

    def __init__(self, is_healthy: bool = True):
        self.is_healthy = is_healthy

    def lookup_customer(self, query: str) -> list[dict]:
        if not self.is_healthy:
            raise CRMAPIError("HTTP 500: Internal Server Error from api.crm.com")
        return [{"id": 1, "name": "Acme Corp", "tier": "enterprise"}]


class NaiveSubagent:
    """WRONG: catches exceptions and returns empty result."""

    def __init__(self, crm: CRMTool):
        self.crm = crm

    def handle(self, query: str) -> dict:
        try:
            results = self.crm.lookup_customer(query)
            return {"customers": results}
        except CRMAPIError:
            # "Don't let infrastructure errors crash the agent"
            return {}  # BUG: indistinguishable from "no results found"


class StructuredSubagent:
    """CORRECT: returns a structured envelope with explicit status."""

    def __init__(self, crm: CRMTool):
        self.crm = crm

    def handle(self, query: str) -> AgentResponse:
        try:
            results = self.crm.lookup_customer(query)
            return AgentResponse(status="success", data={"customers": results})
        except CRMAPIError as e:
            return AgentResponse(
                status="error",
                error_message=str(e),
                error_code="UPSTREAM_API_FAILURE",
            )


class Supervisor:
    """Interprets subagent results and generates user-facing responses."""

    @staticmethod
    def interpret_naive(result: dict) -> str:
        if not result or not result.get("customers"):
            return "I was unable to find any customer records matching your query."
        return f"Found {len(result['customers'])} customer(s): {result['customers']}"

    @staticmethod
    def interpret_structured(result: AgentResponse) -> str:
        if result.status == "error":
            return (
                f"I'm sorry, I couldn't complete the customer lookup due to a "
                f"system error ({result.error_code}). Please try again in a few minutes."
            )
        if not result.data or not result.data.get("customers"):
            return "The customer lookup completed successfully but found no matching records."
        return f"Found {len(result.data['customers'])} customer(s): {result.data['customers']}"


if __name__ == "__main__":
    print("=" * 60)
    print("  AFT-031: Subagent Silent Failure")
    print("=" * 60)

    # CRM API is down
    broken_crm = CRMTool(is_healthy=False)
    healthy_crm = CRMTool(is_healthy=True)
    supervisor = Supervisor()

    # --- Naive subagent with broken CRM ---
    print("\n  Scenario 1: Naive subagent + broken CRM")
    naive = NaiveSubagent(broken_crm)
    result = naive.handle("Acme Corp")
    response = supervisor.interpret_naive(result)
    print(f"    Subagent returned: {result}")
    print(f"    Supervisor says:   {response}")
    print(f"    WRONG: User told 'no records found' when API was actually down")

    # --- Naive subagent with healthy CRM, no results ---
    print("\n  Scenario 2: Naive subagent + healthy CRM, no match")
    healthy_naive = NaiveSubagent(CRMTool(is_healthy=True))
    # Monkey-patch to return empty for demo
    healthy_naive.crm.lookup_customer = lambda q: []
    result2 = healthy_naive.handle("NonexistentCorp")
    response2 = supervisor.interpret_naive(result2)
    print(f"    Subagent returned: {result2}")
    print(f"    Supervisor says:   {response2}")
    print(f"    Note: Same response as Scenario 1 — indistinguishable")

    # --- Structured subagent with broken CRM ---
    print("\n  Scenario 3: Structured subagent + broken CRM (CORRECT)")
    structured = StructuredSubagent(broken_crm)
    result3 = structured.handle("Acme Corp")
    response3 = supervisor.interpret_structured(result3)
    print(f"    Subagent returned: status={result3.status}, error={result3.error_code}")
    print(f"    Supervisor says:   {response3}")
    print(f"    CORRECT: User informed of system error, not told data doesn't exist")
