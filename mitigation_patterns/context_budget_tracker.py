"""
Context Budget Tracker — Mitigation for AFT-010 and AFT-011.

Tracks token consumption across a conversation and fires callbacks
at configurable thresholds. Callers implement the summarization
strategy; this module handles the when.

Uses tiktoken for token counting when available, with a fallback
word-count estimate.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)


class TokenCounter(Protocol):
    """Protocol for token counting implementations."""
    def count(self, text: str) -> int: ...


class TiktokenCounter:
    """Token counter using tiktoken (OpenAI's tokenizer)."""

    def __init__(self, model: str = "cl100k_base"):
        try:
            import tiktoken
            self._encoder = tiktoken.get_encoding(model)
        except ImportError:
            raise ImportError(
                "tiktoken is required for accurate token counting. "
                "Install it with: pip install tiktoken"
            )

    def count(self, text: str) -> int:
        return len(self._encoder.encode(text))


class WordEstimateCounter:
    """Fallback token counter: ~1.3 tokens per word, ~4 chars per token."""

    def count(self, text: str) -> int:
        return max(1, len(text) // 4)


class BudgetLevel(Enum):
    """Budget threshold levels, ordered by severity."""
    NORMAL = "normal"
    WARNING = "warning"       # 60% — time to summarize
    CRITICAL = "critical"     # 80% — summarize urgently if not already done
    EMERGENCY = "emergency"   # 95% — hard stop, no more tool calls


@dataclass
class BudgetThreshold:
    """A threshold that fires a callback when crossed."""
    level: BudgetLevel
    ratio: float  # 0.0 to 1.0
    callback: Callable[[int, int, float], None] | None = None  # (current_tokens, max_tokens, ratio)
    fired: bool = False


@dataclass
class BudgetSnapshot:
    """Point-in-time snapshot of budget state."""
    total_tokens: int
    max_tokens: int
    ratio: float
    level: BudgetLevel
    message_count: int
    tool_result_tokens: int
    tool_result_ratio: float


class ContextBudgetTracker:
    """Tracks token consumption and fires callbacks at thresholds.

    Usage:
        tracker = ContextBudgetTracker(max_tokens=128_000)
        tracker.on_threshold(BudgetLevel.WARNING, my_summarize_callback)

        # As messages are added:
        tracker.add_message({"role": "user", "content": "..."})
        tracker.add_tool_result("tool_name", large_result_dict)

    The tracker does not modify messages. It tells you when to act;
    you decide how.

    Args:
        max_tokens: Maximum context window size in tokens.
        counter: Token counting implementation. Falls back to word estimate
                 if tiktoken is not installed.
        warning_threshold: Ratio at which WARNING fires (default 0.6).
        critical_threshold: Ratio at which CRITICAL fires (default 0.8).
        emergency_threshold: Ratio at which EMERGENCY fires (default 0.95).
    """

    def __init__(
        self,
        max_tokens: int,
        counter: TokenCounter | None = None,
        warning_threshold: float = 0.6,
        critical_threshold: float = 0.8,
        emergency_threshold: float = 0.95,
    ):
        self.max_tokens = max_tokens

        if counter is not None:
            self._counter = counter
        else:
            try:
                self._counter = TiktokenCounter()
            except ImportError:
                logger.warning("tiktoken not available, using word-count estimate")
                self._counter = WordEstimateCounter()

        self._total_tokens = 0
        self._tool_result_tokens = 0
        self._message_count = 0
        self._thresholds = [
            BudgetThreshold(BudgetLevel.WARNING, warning_threshold),
            BudgetThreshold(BudgetLevel.CRITICAL, critical_threshold),
            BudgetThreshold(BudgetLevel.EMERGENCY, emergency_threshold),
        ]

    def on_threshold(self, level: BudgetLevel, callback: Callable[[int, int, float], None]) -> None:
        """Register a callback for a budget threshold.

        The callback receives (current_tokens, max_tokens, ratio).
        """
        for threshold in self._thresholds:
            if threshold.level == level:
                threshold.callback = callback
                return
        raise ValueError(f"Unknown threshold level: {level}")

    def add_message(self, message: dict[str, Any]) -> BudgetSnapshot:
        """Track a conversation message.

        Args:
            message: A message dict with at least a 'content' field.

        Returns:
            Current budget snapshot after adding this message.
        """
        content = message.get("content", "")
        if isinstance(content, dict):
            content = json.dumps(content)
        tokens = self._counter.count(str(content))
        self._total_tokens += tokens
        self._message_count += 1
        return self._check_thresholds()

    def add_tool_result(self, tool_name: str, result: Any) -> BudgetSnapshot:
        """Track a tool result being added to context.

        Separately tracks tool result tokens to detect AFT-011
        (tool result accumulation).

        Args:
            tool_name: Name of the tool that produced this result.
            result: The tool result (will be serialized to estimate tokens).

        Returns:
            Current budget snapshot after adding this result.
        """
        text = json.dumps(result, default=str) if not isinstance(result, str) else result
        tokens = self._counter.count(text)
        self._total_tokens += tokens
        self._tool_result_tokens += tokens
        return self._check_thresholds()

    def snapshot(self) -> BudgetSnapshot:
        """Get the current budget state without modifying it."""
        ratio = self._total_tokens / self.max_tokens if self.max_tokens > 0 else 0.0
        tool_ratio = self._tool_result_tokens / self._total_tokens if self._total_tokens > 0 else 0.0
        level = BudgetLevel.NORMAL
        for threshold in self._thresholds:
            if ratio >= threshold.ratio:
                level = threshold.level
        return BudgetSnapshot(
            total_tokens=self._total_tokens,
            max_tokens=self.max_tokens,
            ratio=ratio,
            level=level,
            message_count=self._message_count,
            tool_result_tokens=self._tool_result_tokens,
            tool_result_ratio=tool_ratio,
        )

    def reset(self, preserved_tokens: int = 0) -> None:
        """Reset the tracker after a summarization.

        Args:
            preserved_tokens: Token count of the summary + system prompt
                            that will carry forward.
        """
        self._total_tokens = preserved_tokens
        self._tool_result_tokens = 0
        self._message_count = 0
        for threshold in self._thresholds:
            threshold.fired = False

    def _check_thresholds(self) -> BudgetSnapshot:
        snap = self.snapshot()
        for threshold in self._thresholds:
            if not threshold.fired and snap.ratio >= threshold.ratio:
                threshold.fired = True
                if threshold.callback:
                    threshold.callback(snap.total_tokens, snap.max_tokens, snap.ratio)
                else:
                    logger.warning(
                        f"Context budget {threshold.level.value}: "
                        f"{snap.total_tokens}/{snap.max_tokens} tokens ({snap.ratio:.1%})"
                    )
        return snap


if __name__ == "__main__":
    print("Context Budget Tracker — Demo")
    print("=" * 50)

    def on_warning(current: int, max_tok: int, ratio: float) -> None:
        print(f"   WARNING: {current}/{max_tok} tokens ({ratio:.1%}) — time to summarize")

    def on_critical(current: int, max_tok: int, ratio: float) -> None:
        print(f"   CRITICAL: {current}/{max_tok} tokens ({ratio:.1%}) — summarize NOW")

    def on_emergency(current: int, max_tok: int, ratio: float) -> None:
        print(f"   EMERGENCY: {current}/{max_tok} tokens ({ratio:.1%}) — stop accepting input")

    # Small context window for demo purposes
    tracker = ContextBudgetTracker(max_tokens=1000, counter=WordEstimateCounter())
    tracker.on_threshold(BudgetLevel.WARNING, on_warning)
    tracker.on_threshold(BudgetLevel.CRITICAL, on_critical)
    tracker.on_threshold(BudgetLevel.EMERGENCY, on_emergency)

    # Simulate a conversation
    messages = [
        {"role": "system", "content": "You are a helpful assistant. " * 10},
        {"role": "user", "content": "Tell me about customer retention strategies. " * 5},
        {"role": "assistant", "content": "Here are the key strategies... " * 15},
    ]

    print("\n1. Adding conversation messages:")
    for msg in messages:
        snap = tracker.add_message(msg)
        print(f"   [{msg['role']}] tokens={snap.total_tokens} ({snap.ratio:.1%})")

    # Simulate large tool results
    print("\n2. Adding tool results:")
    for i in range(5):
        result = {"data": [{"customer": f"Customer_{j}", "revenue": j * 1000} for j in range(50)]}
        snap = tracker.add_tool_result("query_db", result)
        print(f"   Tool result {i + 1}: tokens={snap.total_tokens} ({snap.ratio:.1%}), "
              f"tool_ratio={snap.tool_result_ratio:.1%}")

    # Final snapshot
    print(f"\n3. Final state:")
    snap = tracker.snapshot()
    print(f"   Total tokens: {snap.total_tokens}/{snap.max_tokens} ({snap.ratio:.1%})")
    print(f"   Tool result tokens: {snap.tool_result_tokens} ({snap.tool_result_ratio:.1%})")
    print(f"   Level: {snap.level.value}")
    if snap.tool_result_ratio > 0.4:
        print(f"   WARNING: Tool results > 40% of context — AFT-011 risk")
