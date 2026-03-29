"""
Loop Circuit Breaker — Mitigation for AFT-001 and AFT-002.

Detects and interrupts agent loops by tracking tool call signatures within
a sliding window. Catches both exact-match loops (identical calls) and
oscillation patterns (alternating tool names).

This module has no framework dependencies. Integrate it by calling
`record()` before each tool execution and handling `LoopDetectedError`.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any


class LoopDetectedError(Exception):
    """Raised when a tool call loop is detected."""

    def __init__(self, message: str, tool_name: str, call_count: int, window_size: int):
        super().__init__(message)
        self.tool_name = tool_name
        self.call_count = call_count
        self.window_size = window_size


@dataclass
class ToolCallSignature:
    """Immutable signature of a tool call for deduplication."""
    tool_name: str
    input_hash: str
    raw_args: dict[str, Any]

    @classmethod
    def from_call(cls, tool_name: str, args: dict[str, Any], hash_fields: list[str] | None = None) -> ToolCallSignature:
        """Create a signature from a tool call.

        Args:
            tool_name: Name of the tool being called.
            args: Arguments passed to the tool.
            hash_fields: If provided, only these fields are included in the hash.
                         Use this when some arguments are non-deterministic (e.g., timestamps).
        """
        if hash_fields:
            hashable = {k: args[k] for k in hash_fields if k in args}
        else:
            hashable = args

        raw = json.dumps(hashable, sort_keys=True, default=str)
        input_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return cls(tool_name=tool_name, input_hash=input_hash, raw_args=args)

    @property
    def exact_key(self) -> str:
        return f"{self.tool_name}:{self.input_hash}"


class LoopCircuitBreaker:
    """Detects tool call loops within a sliding window.

    Two detection modes:
    1. **Exact match**: Same tool + same arguments repeated N times.
    2. **Oscillation**: Same tool name appears N times in a window, regardless of arguments.

    Thread-safe. One instance per request/session.

    Args:
        window_size: Number of recent calls to consider.
        max_identical_calls: Threshold for exact-match loop detection.
        max_name_frequency: Threshold for oscillation detection (same tool name count in window).
        hash_fields: Optional list of argument fields to include in the hash.
    """

    def __init__(
        self,
        window_size: int = 10,
        max_identical_calls: int = 3,
        max_name_frequency: int = 4,
        hash_fields: list[str] | None = None,
    ):
        if window_size < max_identical_calls:
            raise ValueError(f"window_size ({window_size}) must be >= max_identical_calls ({max_identical_calls})")
        if window_size < max_name_frequency:
            raise ValueError(f"window_size ({window_size}) must be >= max_name_frequency ({max_name_frequency})")

        self.window_size = window_size
        self.max_identical_calls = max_identical_calls
        self.max_name_frequency = max_name_frequency
        self.hash_fields = hash_fields
        self._history: deque[ToolCallSignature] = deque(maxlen=window_size)
        self._lock = threading.Lock()

    def record(self, tool_name: str, args: dict[str, Any]) -> ToolCallSignature:
        """Record a tool call and check for loops.

        Call this before executing each tool. If a loop is detected,
        raises LoopDetectedError. Otherwise, returns the call signature.

        Args:
            tool_name: Name of the tool being called.
            args: Arguments passed to the tool.

        Returns:
            The ToolCallSignature for this call.

        Raises:
            LoopDetectedError: If a loop pattern is detected.
        """
        sig = ToolCallSignature.from_call(tool_name, args, self.hash_fields)

        with self._lock:
            # Check exact-match loop
            exact_count = sum(1 for s in self._history if s.exact_key == sig.exact_key)
            if exact_count >= self.max_identical_calls - 1:  # -1 because current call isn't in history yet
                raise LoopDetectedError(
                    f"Exact loop detected: {tool_name}({sig.input_hash}) called "
                    f"{exact_count + 1} times in last {len(self._history)} calls",
                    tool_name=tool_name,
                    call_count=exact_count + 1,
                    window_size=self.window_size,
                )

            # Check oscillation (name frequency)
            name_count = sum(1 for s in self._history if s.tool_name == tool_name)
            if name_count >= self.max_name_frequency - 1:
                raise LoopDetectedError(
                    f"Oscillation detected: {tool_name} called {name_count + 1} times "
                    f"in last {len(self._history)} calls (different args each time)",
                    tool_name=tool_name,
                    call_count=name_count + 1,
                    window_size=self.window_size,
                )

            self._history.append(sig)

        return sig

    def reset(self) -> None:
        """Clear the call history. Use when starting a new logical request."""
        with self._lock:
            self._history.clear()

    @property
    def history(self) -> list[ToolCallSignature]:
        """Return a copy of the current call history."""
        with self._lock:
            return list(self._history)


if __name__ == "__main__":
    print("Loop Circuit Breaker — Demo")
    print("=" * 50)

    breaker = LoopCircuitBreaker(window_size=8, max_identical_calls=3, max_name_frequency=4)

    # Demo 1: Exact match loop
    print("\n1. Exact match detection:")
    breaker.reset()
    calls = [
        ("search_db", {"query": "enterprise customers"}),
        ("search_db", {"query": "enterprise customers"}),
        ("search_db", {"query": "enterprise customers"}),  # Should trigger
    ]
    for tool, args in calls:
        try:
            sig = breaker.record(tool, args)
            print(f"   OK: {tool}({args})")
        except LoopDetectedError as e:
            print(f"   BLOCKED: {e}")

    # Demo 2: Oscillation detection
    print("\n2. Oscillation detection:")
    breaker.reset()
    calls = [
        ("search_docs", {"query": "retention policy"}),
        ("query_metrics", {"metric": "churn"}),
        ("search_docs", {"query": "retention Q3"}),
        ("query_metrics", {"metric": "churn Q3"}),
        ("search_docs", {"query": "retention enterprise"}),
        ("query_metrics", {"metric": "churn enterprise"}),
        ("search_docs", {"query": "retention root cause"}),  # 4th call — triggers oscillation
    ]
    for tool, args in calls:
        try:
            sig = breaker.record(tool, args)
            print(f"   OK: {tool}({list(args.values())[0]})")
        except LoopDetectedError as e:
            print(f"   BLOCKED: {e}")

    # Demo 3: Legitimate varied calls — should not trigger
    print("\n3. Legitimate varied calls (no trigger):")
    breaker.reset()
    tools = ["search_db", "format_result", "send_email", "log_event", "search_db", "validate"]
    for tool in tools:
        try:
            sig = breaker.record(tool, {"unique_arg": tool})
            print(f"   OK: {tool}")
        except LoopDetectedError as e:
            print(f"   BLOCKED: {e}")
