"""
Delegation Chain Tracker — Mitigation for AFT-030 and AFT-031.

Tracks agent delegation chains per request, detects circular delegation,
and provides structured response envelopes for error propagation between
agents.

Thread-safe. No framework dependencies.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class CircularDelegationError(Exception):
    """Raised when a delegation cycle is detected."""

    def __init__(self, message: str, cycle_path: list[str], full_chain: list[str]):
        super().__init__(message)
        self.cycle_path = cycle_path
        self.full_chain = full_chain


class DelegationDepthError(Exception):
    """Raised when delegation chain exceeds maximum depth."""

    def __init__(self, message: str, depth: int, max_depth: int, chain: list[str]):
        super().__init__(message)
        self.depth = depth
        self.max_depth = max_depth
        self.chain = chain


class AgentResponseStatus(Enum):
    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"
    TIMEOUT = "timeout"


@dataclass
class AgentResponse:
    """Structured response envelope for inter-agent communication.

    Prevents AFT-031 (subagent silent failure) by making success/failure
    explicit and distinguishable from empty-but-valid results.
    """
    status: AgentResponseStatus
    data: Any = None
    error_message: str | None = None
    error_code: str | None = None
    agent_id: str | None = None
    duration_ms: float | None = None

    def is_success(self) -> bool:
        return self.status == AgentResponseStatus.SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "data": self.data,
            "error_message": self.error_message,
            "error_code": self.error_code,
            "agent_id": self.agent_id,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def success(cls, data: Any, agent_id: str | None = None) -> AgentResponse:
        return cls(status=AgentResponseStatus.SUCCESS, data=data, agent_id=agent_id)

    @classmethod
    def error(cls, message: str, code: str, agent_id: str | None = None) -> AgentResponse:
        return cls(
            status=AgentResponseStatus.ERROR,
            error_message=message,
            error_code=code,
            agent_id=agent_id,
        )


@dataclass
class DelegationRecord:
    """Record of a single delegation in a chain."""
    agent_id: str
    timestamp: float
    request_id: str


class DelegationChainTracker:
    """Tracks delegation chains per request and detects cycles.

    Usage:
        tracker = DelegationChainTracker(max_depth=10)

        # Before each delegation:
        tracker.delegate(request_id="req-123", target_agent="agent_a")
        tracker.delegate(request_id="req-123", target_agent="agent_b")
        tracker.delegate(request_id="req-123", target_agent="agent_a")  # Raises CircularDelegationError

    Thread-safe. Designed for use in concurrent request handling.

    Args:
        max_depth: Maximum allowed delegation chain depth (independent of cycle detection).
    """

    def __init__(self, max_depth: int = 15):
        self.max_depth = max_depth
        self._chains: dict[str, list[DelegationRecord]] = {}
        self._lock = threading.Lock()

    def delegate(self, request_id: str, target_agent: str) -> list[str]:
        """Record a delegation and check for cycles.

        Args:
            request_id: Unique identifier for the current request.
            target_agent: Agent ID being delegated to.

        Returns:
            The current delegation chain (list of agent IDs).

        Raises:
            CircularDelegationError: If delegating to target_agent would create a cycle.
            DelegationDepthError: If the chain exceeds max_depth.
        """
        with self._lock:
            chain = self._chains.setdefault(request_id, [])
            agent_ids = [r.agent_id for r in chain]

            # Check for cycle
            if target_agent in agent_ids:
                cycle_start = agent_ids.index(target_agent)
                cycle_path = agent_ids[cycle_start:] + [target_agent]
                raise CircularDelegationError(
                    f"Circular delegation detected: {' -> '.join(cycle_path)}",
                    cycle_path=cycle_path,
                    full_chain=agent_ids + [target_agent],
                )

            # Check depth
            if len(chain) >= self.max_depth:
                raise DelegationDepthError(
                    f"Delegation chain depth {len(chain) + 1} exceeds max {self.max_depth}",
                    depth=len(chain) + 1,
                    max_depth=self.max_depth,
                    chain=agent_ids + [target_agent],
                )

            chain.append(DelegationRecord(
                agent_id=target_agent,
                timestamp=time.time(),
                request_id=request_id,
            ))
            return agent_ids + [target_agent]

    def get_chain(self, request_id: str) -> list[str]:
        """Get the current delegation chain for a request."""
        with self._lock:
            chain = self._chains.get(request_id, [])
            return [r.agent_id for r in chain]

    def detect_cycle(self, request_id: str, target_agent: str) -> list[str] | None:
        """Check if delegating to target would create a cycle, without recording.

        Returns:
            The cycle path if a cycle would be created, None otherwise.
        """
        with self._lock:
            chain = self._chains.get(request_id, [])
            agent_ids = [r.agent_id for r in chain]
            if target_agent in agent_ids:
                cycle_start = agent_ids.index(target_agent)
                return agent_ids[cycle_start:] + [target_agent]
            return None

    def complete(self, request_id: str) -> list[str]:
        """Mark a request as complete and return the final chain.

        Call this when the request is fully handled to free memory.
        """
        with self._lock:
            chain = self._chains.pop(request_id, [])
            return [r.agent_id for r in chain]

    @property
    def active_requests(self) -> int:
        with self._lock:
            return len(self._chains)


if __name__ == "__main__":
    print("Delegation Chain Tracker — Demo")
    print("=" * 50)

    tracker = DelegationChainTracker(max_depth=10)

    # Demo 1: Normal delegation chain
    print("\n1. Normal delegation chain:")
    req1 = "req-001"
    for agent in ["router", "customer_agent", "pricing_agent", "fulfillment"]:
        chain = tracker.delegate(req1, agent)
        print(f"   Delegated to {agent}: chain = {chain}")
    tracker.complete(req1)

    # Demo 2: Circular delegation detected
    print("\n2. Circular delegation detection:")
    req2 = "req-002"
    try:
        tracker.delegate(req2, "agent_a")
        print(f"   Delegated to agent_a")
        tracker.delegate(req2, "agent_b")
        print(f"   Delegated to agent_b")
        tracker.delegate(req2, "agent_a")  # Cycle!
    except CircularDelegationError as e:
        print(f"   CYCLE: {e}")
        print(f"   Cycle path: {e.cycle_path}")
    tracker.complete(req2)

    # Demo 3: Structured response envelope
    print("\n3. Structured response envelope (AFT-031 prevention):")

    # Subagent error — clear failure signal
    error_resp = AgentResponse.error(
        message="CRM API returned 500",
        code="UPSTREAM_API_FAILURE",
        agent_id="customer_agent",
    )
    print(f"   Error response: {error_resp.to_dict()}")
    print(f"   is_success: {error_resp.is_success()}")

    # Subagent success with empty data — distinct from error
    empty_resp = AgentResponse.success(data={"customers": []}, agent_id="customer_agent")
    print(f"   Empty success:  status={empty_resp.status.value}, data={empty_resp.data}")
    print(f"   is_success: {empty_resp.is_success()}")
    print(f"   These are distinguishable — empty success != error")

    # Demo 4: Pre-check without recording
    print("\n4. Pre-check (detect_cycle without recording):")
    req3 = "req-003"
    tracker.delegate(req3, "x")
    tracker.delegate(req3, "y")
    cycle = tracker.detect_cycle(req3, "x")
    print(f"   Would delegating to 'x' cycle? {cycle}")
    cycle2 = tracker.detect_cycle(req3, "z")
    print(f"   Would delegating to 'z' cycle? {cycle2}")
    tracker.complete(req3)
