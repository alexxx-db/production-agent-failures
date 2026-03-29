"""
AFT-030 Repro: Circular Delegation

Demonstrates how a multi-agent supervisor enters a delegation cycle when
two agents redirect to each other, and how cycle detection resolves it.

Run: python aft030_repro.py
"""
from __future__ import annotations

from dataclasses import dataclass, field


class CircularDelegationError(Exception):
    pass


class MaxDepthExceededError(Exception):
    pass


@dataclass
class Agent:
    name: str
    domain: str
    redirect_to: str | None = None  # If set, agent claims this other agent should handle it

    def handle(self, query: str, context: dict | None = None) -> dict:
        if self.redirect_to:
            return {
                "status": "redirect",
                "message": f"This requires {self.redirect_to}'s domain expertise",
                "target": self.redirect_to,
            }
        return {
            "status": "success",
            "data": f"{self.name} handled: {query}",
        }


class NaiveSupervisor:
    """WRONG: Uses depth limit only. Cannot detect cycles."""

    def __init__(self, agents: dict[str, Agent], max_depth: int = 5):
        self.agents = agents
        self.max_depth = max_depth

    def delegate(self, query: str, target: str, depth: int = 0) -> dict:
        if depth >= self.max_depth:
            raise MaxDepthExceededError(
                f"Max delegation depth {self.max_depth} exceeded. "
                f"Last target: {target}"
            )
        agent = self.agents[target]
        result = agent.handle(query)

        if result["status"] == "redirect":
            print(f"    [{depth}] {agent.name} → redirect to {result['target']}")
            return self.delegate(query, result["target"], depth + 1)

        print(f"    [{depth}] {agent.name} → success")
        return result


class CycleAwareSupervisor:
    """CORRECT: Tracks delegation chain, detects cycles."""

    def __init__(self, agents: dict[str, Agent]):
        self.agents = agents

    def delegate(self, query: str, target: str, chain: list[str] | None = None) -> dict:
        if chain is None:
            chain = []

        if target in chain:
            cycle = chain[chain.index(target):] + [target]
            raise CircularDelegationError(
                f"Cycle detected: {' → '.join(cycle)}\n"
                f"Full chain: {' → '.join(chain + [target])}"
            )

        chain.append(target)
        agent = self.agents[target]
        result = agent.handle(query)

        if result["status"] == "redirect":
            redirect_target = result["target"]
            print(f"    {agent.name} → redirect to {redirect_target} (chain: {chain})")
            return self.delegate(query, redirect_target, chain)

        print(f"    {agent.name} → success (chain: {chain})")
        return result


if __name__ == "__main__":
    # Setup: Agent A and Agent B redirect to each other
    agents = {
        "customer_agent": Agent(
            name="customer_agent",
            domain="customer data",
            redirect_to="pricing_agent",
        ),
        "pricing_agent": Agent(
            name="pricing_agent",
            domain="pricing",
            redirect_to="customer_agent",
        ),
        "fulfillment_agent": Agent(
            name="fulfillment_agent",
            domain="fulfillment",
            redirect_to=None,  # Actually handles requests
        ),
    }

    print("=" * 60)
    print("  AFT-030: Circular Delegation")
    print("=" * 60)

    # Scenario 1: Naive supervisor with depth limit
    print("\n  Scenario 1: Naive depth-limit supervisor (BROKEN)")
    naive = NaiveSupervisor(agents, max_depth=5)
    try:
        naive.delegate("What's the renewal price for Acme Corp?", "customer_agent")
    except MaxDepthExceededError as e:
        print(f"    ERROR: {e}")
        print("    Problem: Burned 5 delegations before stopping. No cycle info.")

    # Scenario 2: Cycle-aware supervisor
    print("\n  Scenario 2: Cycle-aware supervisor (CORRECT)")
    cycle_aware = CycleAwareSupervisor(agents)
    try:
        cycle_aware.delegate("What's the renewal price for Acme Corp?", "customer_agent")
    except CircularDelegationError as e:
        print(f"    Caught cycle: {e}")
        print("    Stopped after 2 delegations. Cycle path identified.")

    # Scenario 3: Legitimate chain (should work with both)
    print("\n  Scenario 3: Legitimate delegation chain (no cycle)")
    agents_no_cycle = {
        "routing_agent": Agent(name="routing_agent", domain="routing", redirect_to="customer_agent"),
        "customer_agent": Agent(name="customer_agent", domain="customer data", redirect_to="fulfillment_agent"),
        "fulfillment_agent": Agent(name="fulfillment_agent", domain="fulfillment", redirect_to=None),
    }
    legit_supervisor = CycleAwareSupervisor(agents_no_cycle)
    result = legit_supervisor.delegate("Process order for Acme", "routing_agent")
    print(f"    Result: {result}")
