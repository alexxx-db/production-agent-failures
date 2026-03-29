"""
AFT-061 Repro: Missing Tool Call Attribution

Demonstrates the difference between event-based tool logging (metadata on LLM span)
and span-based tool logging (first-class spans with full input/output capture).

Run: python aft061_repro.py
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class SpanEvent:
    name: str
    payload: str  # Truncated to max_event_size


@dataclass
class TraceSpan:
    name: str
    span_id: str = field(default_factory=lambda: uuid4().hex[:8])
    parent_id: str | None = None
    inputs: dict | None = None
    outputs: dict | None = None
    events: list[SpanEvent] = field(default_factory=list)
    status: str = "OK"

    def set_inputs(self, data: dict) -> None:
        self.inputs = data

    def set_outputs(self, data: dict) -> None:
        self.outputs = data


def truncate(text: str, max_len: int = 256) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


class EventBasedTracer:
    """WRONG: logs tool calls as events on the LLM span (truncated, no results)."""

    def __init__(self, max_event_size: int = 256):
        self.max_event_size = max_event_size
        self.spans: list[TraceSpan] = []

    def trace_agent_step(self, user_message: str, tool_calls: list[dict]) -> TraceSpan:
        span = TraceSpan(name="llm_call")
        span.set_inputs({"user_message": user_message})

        for tc in tool_calls:
            args_str = json.dumps(tc["args"])
            truncated_args = truncate(args_str, self.max_event_size)
            span.events.append(SpanEvent(
                name=f"tool_call:{tc['name']}",
                payload=truncated_args,
                # NOTE: tool result is NOT captured
            ))

        span.set_outputs({"response": "Total revenue for Q3 is $2.4M"})
        self.spans.append(span)
        return span


class SpanBasedTracer:
    """CORRECT: logs tool calls as first-class child spans with full I/O."""

    def __init__(self):
        self.spans: list[TraceSpan] = []

    def trace_agent_step(self, user_message: str, tool_calls: list[dict]) -> TraceSpan:
        parent = TraceSpan(name="agent_step")
        parent.set_inputs({"user_message": user_message})
        self.spans.append(parent)

        for tc in tool_calls:
            child = TraceSpan(
                name=f"tool:{tc['name']}",
                parent_id=parent.span_id,
            )
            child.set_inputs(tc["args"])  # Full args, no truncation
            child.set_outputs(tc["result"])  # Full result captured
            self.spans.append(child)

        parent.set_outputs({"response": "Total revenue for Q3 is $2.4M"})
        return parent


def print_trace(spans: list[TraceSpan], label: str) -> None:
    print(f"\n  {label}:")
    for span in spans:
        indent = "      " if span.parent_id else "    "
        print(f"{indent}[{span.name}] (id={span.span_id})")
        if span.inputs:
            inputs_str = json.dumps(span.inputs)
            if len(inputs_str) > 120:
                inputs_str = inputs_str[:120] + "..."
            print(f"{indent}  inputs: {inputs_str}")
        if span.outputs:
            outputs_str = json.dumps(span.outputs)
            if len(outputs_str) > 120:
                outputs_str = outputs_str[:120] + "..."
            print(f"{indent}  outputs: {outputs_str}")
        for event in span.events:
            print(f"{indent}  event: {event.name} → {event.payload[:80]}...")
        if not span.inputs and not span.outputs and not span.events:
            print(f"{indent}  (empty)")


if __name__ == "__main__":
    print("=" * 60)
    print("  AFT-061: Missing Tool Call Attribution")
    print("=" * 60)

    # Simulate tool calls with large args and results
    tool_calls = [
        {
            "name": "calculate_revenue",
            "args": {
                "customer_ids": list(range(1, 501)),  # 500 customer IDs
                "period": "Q3",
                "include_projections": True,
            },
            "result": {
                "total_revenue": 3100000,
                "breakdown": {f"customer_{i}": 6200 + i * 10 for i in range(1, 501)},
                "period": "Q3",
            },
        },
        {
            "name": "get_comparison",
            "args": {"period_a": "Q2", "period_b": "Q3"},
            "result": {"q2_total": 2800000, "q3_total": 3100000, "growth": 0.107},
        },
    ]

    # Event-based tracing (insufficient)
    event_tracer = EventBasedTracer(max_event_size=256)
    event_span = event_tracer.trace_agent_step("What's our Q3 revenue?", tool_calls)
    print_trace(event_tracer.spans, "Event-based tracing (INSUFFICIENT)")

    print("\n    PROBLEMS:")
    print("    - Tool args truncated at 256 chars (500 customer IDs lost)")
    print("    - Tool results NOT captured at all")
    print("    - Cannot determine if agent error was from bad tool data or bad reasoning")

    # Span-based tracing (correct)
    span_tracer = SpanBasedTracer()
    span_parent = span_tracer.trace_agent_step("What's our Q3 revenue?", tool_calls)
    print_trace(span_tracer.spans, "Span-based tracing (CORRECT)")

    print("\n    ADVANTAGES:")
    print("    - Full tool args captured (all 500 customer IDs)")
    print("    - Full tool results captured (revenue breakdown)")
    print("    - Can determine: tool returned $3.1M, agent reported $2.4M → reasoning error")
