"""
AFT-001 Repro: Naive Retry Amplification

Demonstrates how retry logic that appends (instead of replaces) tool results
in the LLM conversation history causes token count blowup and reasoning
corruption.

Run: python aft001_repro.py

Requires: langchain-core
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    call_id: str
    tool_name: str
    status: str  # "success" | "error"
    data: Any = None
    error: str | None = None

    def to_message(self) -> dict[str, Any]:
        return {
            "role": "tool",
            "call_id": self.call_id,
            "name": self.tool_name,
            "content": json.dumps({"status": self.status, "data": self.data, "error": self.error}),
        }


@dataclass
class MessageHistory:
    """Simulates an LLM message history."""
    messages: list[dict[str, Any]] = field(default_factory=list)

    def token_estimate(self) -> int:
        return sum(len(json.dumps(m)) // 4 for m in self.messages)


class NaiveRetryExecutor:
    """WRONG: appends every retry result to history."""

    def __init__(self, history: MessageHistory, max_retries: int = 3):
        self.history = history
        self.max_retries = max_retries

    def execute(self, tool_name: str, args: dict[str, Any], fail_rate: float = 0.7) -> ToolResult:
        for attempt in range(self.max_retries):
            call_id = f"{tool_name}_{attempt}_{hashlib.md5(json.dumps(args).encode()).hexdigest()[:8]}"
            if random.random() < fail_rate and attempt < self.max_retries - 1:
                result = ToolResult(call_id=call_id, tool_name=tool_name, status="error",
                                    error=f"TimeoutError after 10s (attempt {attempt + 1})")
            else:
                result = ToolResult(call_id=call_id, tool_name=tool_name, status="success",
                                    data={"customers": [{"id": 1, "name": "Acme Corp"}]})

            # BUG: appends every attempt
            self.history.messages.append(result.to_message())
            if result.status == "success":
                return result
        return result


class IdempotentRetryExecutor:
    """CORRECT: replaces previous attempts with same logical call."""

    def __init__(self, history: MessageHistory, max_retries: int = 3):
        self.history = history
        self.max_retries = max_retries

    def execute(self, tool_name: str, args: dict[str, Any], fail_rate: float = 0.7) -> ToolResult:
        # Single call_id for all retries of this logical invocation
        call_id = f"{tool_name}_{hashlib.md5(json.dumps(args).encode()).hexdigest()[:8]}"

        for attempt in range(self.max_retries):
            if random.random() < fail_rate and attempt < self.max_retries - 1:
                result = ToolResult(call_id=call_id, tool_name=tool_name, status="error",
                                    error=f"TimeoutError after 10s (attempt {attempt + 1})")
            else:
                result = ToolResult(call_id=call_id, tool_name=tool_name, status="success",
                                    data={"customers": [{"id": 1, "name": "Acme Corp"}]})

            # Replace, don't append
            replaced = False
            for i, msg in enumerate(self.history.messages):
                if msg.get("call_id") == call_id:
                    self.history.messages[i] = result.to_message()
                    replaced = True
                    break
            if not replaced:
                self.history.messages.append(result.to_message())

            if result.status == "success":
                return result
        return result


def run_scenario(executor_cls: type, label: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")

    random.seed(42)
    history = MessageHistory()
    history.messages.append({"role": "user", "content": "Find all enterprise customers."})
    history.messages.append({"role": "assistant", "content": "I'll search the customer database."})

    executor = executor_cls(history, max_retries=5)

    # Simulate 3 tool calls, each with potential retries
    for query in ["enterprise", "enterprise tier:gold", "enterprise region:NA"]:
        executor.execute("search_customer_db", {"query": query}, fail_rate=0.7)

    print(f"  Messages in history: {len(history.messages)}")
    print(f"  Estimated tokens:    {history.token_estimate()}")
    print(f"  Tool results:        {sum(1 for m in history.messages if m.get('role') == 'tool')}")


if __name__ == "__main__":
    run_scenario(NaiveRetryExecutor, "NAIVE (append every retry) — AFT-001 failure")
    run_scenario(IdempotentRetryExecutor, "IDEMPOTENT (replace on retry) — correct behavior")
