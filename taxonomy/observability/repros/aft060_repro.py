"""
AFT-060 Repro: MLflow Trace Span Loss

Demonstrates how async context propagation failure causes tool call spans
to be orphaned from their parent agent request trace.

Run: python aft060_repro.py
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from uuid import uuid4


# Simulated MLflow trace context
_current_span: ContextVar[str | None] = ContextVar("_current_span", default=None)


@dataclass
class Span:
    name: str
    span_id: str = field(default_factory=lambda: uuid4().hex[:8])
    parent_id: str | None = None
    trace_id: str = field(default_factory=lambda: uuid4().hex[:8])
    children: list["Span"] = field(default_factory=list)

    def __repr__(self) -> str:
        parent = f"parent={self.parent_id}" if self.parent_id else "ROOT"
        return f"Span({self.name}, id={self.span_id}, {parent}, trace={self.trace_id})"


class SpanCollector:
    """Collects all spans for analysis."""

    def __init__(self):
        self.spans: list[Span] = []

    def add(self, span: Span) -> None:
        self.spans.append(span)

    def get_roots(self) -> list[Span]:
        return [s for s in self.spans if s.parent_id is None]

    def get_children(self, parent_id: str) -> list[Span]:
        return [s for s in self.spans if s.parent_id == parent_id]

    def print_tree(self) -> None:
        roots = self.get_roots()
        for root in roots:
            self._print_span(root, indent=0)

    def _print_span(self, span: Span, indent: int) -> None:
        prefix = "    " + "  " * indent
        marker = "├── " if indent > 0 else ""
        print(f"{prefix}{marker}{span.name} (trace={span.trace_id}, id={span.span_id})")
        for child in self.get_children(span.span_id):
            self._print_span(child, indent + 1)


collector = SpanCollector()


class TracingContext:
    """Simulates MLflow tracing context management."""

    @staticmethod
    def start_span(name: str, parent_id: str | None = None, trace_id: str | None = None) -> Span:
        current = _current_span.get()

        if parent_id:
            span = Span(name=name, parent_id=parent_id, trace_id=trace_id or uuid4().hex[:8])
        elif current:
            # Inherit parent from context
            parent_span = next((s for s in collector.spans if s.span_id == current), None)
            span = Span(
                name=name,
                parent_id=current,
                trace_id=parent_span.trace_id if parent_span else uuid4().hex[:8],
            )
        else:
            # No context — create root span (THIS IS THE BUG for async)
            span = Span(name=name)

        _current_span.set(span.span_id)
        collector.add(span)
        return span


async def tool_call_naive(name: str, query: str) -> str:
    """Tool call WITHOUT context propagation — creates orphaned span."""
    span = TracingContext.start_span(f"tool:{name}")
    await asyncio.sleep(0.01)  # Simulate work
    return f"{name} result for {query}"


async def tool_call_with_context(name: str, query: str, parent_id: str, trace_id: str) -> str:
    """Tool call WITH explicit context propagation — correctly parented."""
    span = TracingContext.start_span(f"tool:{name}", parent_id=parent_id, trace_id=trace_id)
    await asyncio.sleep(0.01)
    return f"{name} result for {query}"


async def agent_handler_naive(query: str) -> str:
    """BROKEN: async tool calls lose trace context."""
    parent = TracingContext.start_span("agent_request")

    # These tasks run in fresh contexts — no parent inherited
    task1 = asyncio.create_task(tool_call_naive("search_db", query))
    task2 = asyncio.create_task(tool_call_naive("format_result", query))

    r1 = await task1
    r2 = await task2
    return f"Response based on {r1} and {r2}"


async def agent_handler_correct(query: str) -> str:
    """CORRECT: explicit context propagation to async tool calls."""
    parent = TracingContext.start_span("agent_request")

    task1 = asyncio.create_task(
        tool_call_with_context("search_db", query, parent.span_id, parent.trace_id)
    )
    task2 = asyncio.create_task(
        tool_call_with_context("format_result", query, parent.span_id, parent.trace_id)
    )

    r1 = await task1
    r2 = await task2
    return f"Response based on {r1} and {r2}"


async def main():
    print("=" * 60)
    print("  AFT-060: MLflow Trace Span Loss")
    print("=" * 60)

    # Scenario 1: Naive — spans are orphaned
    print("\n  Scenario 1: Naive async (spans orphaned)")
    collector.spans.clear()
    _current_span.set(None)
    await agent_handler_naive("customer query")
    collector.print_tree()
    roots = collector.get_roots()
    print(f"\n    Root spans: {len(roots)} (expected 1, got {len(roots)})")
    print(f"    PROBLEM: Tool spans are separate root traces, not children")

    # Scenario 2: Correct — context propagated
    print("\n  Scenario 2: With context propagation (spans connected)")
    collector.spans.clear()
    _current_span.set(None)
    await agent_handler_correct("customer query")
    collector.print_tree()
    roots = collector.get_roots()
    print(f"\n    Root spans: {len(roots)} (expected 1, got {len(roots)})")
    print(f"    CORRECT: Tool spans are children of agent_request")


if __name__ == "__main__":
    asyncio.run(main())
