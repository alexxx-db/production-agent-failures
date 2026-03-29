"""
AFT-002 Repro: Tool Call Oscillation

Demonstrates how an agent oscillates between two tools that return partial
results, creating an A-B-A-B pattern that evades exact-match loop detection.

Run: python aft002_repro.py
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolCall:
    name: str
    args: dict
    result: str


class OscillationDetector:
    """Detects tool call oscillation patterns in a sliding window."""

    def __init__(self, window: int = 6, threshold: int = 3):
        self.window = window
        self.threshold = threshold
        self.history: list[str] = []

    def record(self, tool_name: str) -> str | None:
        """Record a tool call. Returns the oscillating tool name if detected."""
        self.history.append(tool_name)
        recent = self.history[-self.window:]
        for name in set(recent):
            if recent.count(name) >= self.threshold:
                return name
        return None


class ExactMatchLoopDetector:
    """Standard loop detector — only catches identical calls."""

    def __init__(self, max_identical: int = 3):
        self.max_identical = max_identical
        self.history: list[tuple[str, str]] = []

    def record(self, tool_name: str, args_str: str) -> bool:
        sig = (tool_name, args_str)
        self.history.append(sig)
        count = sum(1 for s in self.history if s == sig)
        return count >= self.max_identical


def simulate_oscillation(use_detector: bool = False) -> list[ToolCall]:
    """Simulate an agent that oscillates between search_docs and query_metrics."""
    queries = [
        ("search_docs", {"query": "customer retention policy"}),
        ("query_metrics", {"metric": "churn_rate", "period": "Q3"}),
        ("search_docs", {"query": "customer retention policy Q3 churn"}),
        ("query_metrics", {"metric": "churn_rate", "period": "Q3", "segment": "enterprise"}),
        ("search_docs", {"query": "enterprise customer retention Q3"}),
        ("query_metrics", {"metric": "churn_rate", "period": "Q3", "segment": "enterprise", "compare": "Q2"}),
        ("search_docs", {"query": "enterprise retention Q3 vs Q2 policy changes"}),
        ("query_metrics", {"metric": "churn_rate", "period": "Q2-Q3", "segment": "enterprise"}),
        ("search_docs", {"query": "retention root cause enterprise churn increase"}),
        ("query_metrics", {"metric": "churn_rate", "period": "Q2-Q3", "segment": "all"}),
    ]

    exact_detector = ExactMatchLoopDetector()
    oscillation_detector = OscillationDetector(window=6, threshold=3) if use_detector else None
    calls: list[ToolCall] = []

    for tool_name, args in queries:
        # Exact match detector never fires — every call has different args
        exact_match = exact_detector.record(tool_name, str(args))

        # Oscillation detector fires when same tool name appears 3+ times in 6 calls
        oscillating = None
        if oscillation_detector:
            oscillating = oscillation_detector.record(tool_name)

        if oscillating:
            print(f"  OSCILLATION DETECTED: {tool_name} called {3}+ times in last 6 calls. Stopping.")
            break

        calls.append(ToolCall(name=tool_name, args=args, result=f"partial result for {tool_name}"))
        exact_flag = " (exact detector: no match)" if not exact_match else " (exact detector: MATCH)"
        print(f"  Step {len(calls)}: {tool_name}({list(args.values())[0]}){exact_flag}")

    return calls


if __name__ == "__main__":
    print("=" * 60)
    print("  WITHOUT oscillation detection — runs all 10 steps")
    print("=" * 60)
    calls_no_detect = simulate_oscillation(use_detector=False)
    print(f"  Total calls: {len(calls_no_detect)}")

    print()
    print("=" * 60)
    print("  WITH oscillation detection — stops early")
    print("=" * 60)
    calls_with_detect = simulate_oscillation(use_detector=True)
    print(f"  Total calls: {len(calls_with_detect)}")
