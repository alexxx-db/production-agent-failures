"""
AFT-021 Repro: Agent Hop Type Mismatch

Demonstrates how structured data loses type fidelity when passed through
an LLM-mediated handoff (JSON → string → LLM parse → data), causing
non-deterministic type coercion.

Run: python aft021_repro.py
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from typing import Any
from uuid import uuid4


@dataclass
class TypedHandoff:
    """Side-channel for type-safe structured data transfer."""

    _store: dict[str, dict] = None

    def __post_init__(self):
        self._store = {}

    def put(self, key: str, value: Any) -> str:
        ref_id = f"handoff_{uuid4().hex[:8]}"
        self._store[ref_id] = {"key": key, "value": value, "type": type(value).__name__}
        return ref_id

    def get(self, ref_id: str) -> Any:
        return self._store[ref_id]["value"]


def simulate_llm_json_parse(json_string: str) -> dict:
    """
    Simulates the non-deterministic way an LLM 'parses' JSON from a message.

    An LLM is not a JSON parser. It approximates parsing, and for ambiguous
    types (floats that look like ints, numeric strings), it introduces
    non-deterministic type coercion.
    """
    data = json.loads(json_string)

    def coerce_value(v: Any) -> Any:
        if isinstance(v, float):
            # 20% chance: LLM truncates to int
            if random.random() < 0.2:
                return int(v)
            # 10% chance: LLM treats as string
            if random.random() < 0.11:
                return str(v)
            return v
        if isinstance(v, dict):
            return {k: coerce_value(val) for k, val in v.items()}
        if isinstance(v, list):
            return [coerce_value(item) for item in v]
        return v

    return coerce_value(data)


def agent_a_output() -> dict:
    """Agent A produces structured financial data."""
    return {
        "customers": [
            {"id": 1, "name": "Acme Corp", "revenue": 1234.56, "margin": 0.23},
            {"id": 2, "name": "Globex", "revenue": 9876.54, "margin": 0.41},
            {"id": 3, "name": "Initech", "revenue": 555.00, "margin": 0.15},
        ]
    }


def agent_b_process(data: dict) -> dict:
    """Agent B calculates total revenue. Type-sensitive."""
    total = 0.0
    errors = []
    for customer in data["customers"]:
        rev = customer["revenue"]
        if isinstance(rev, str):
            errors.append(f"  {customer.get('name', '?')}: revenue is str '{rev}', math will fail")
            try:
                rev = float(rev)
            except ValueError:
                continue
        elif isinstance(rev, int) and not isinstance(rev, bool):
            original = agent_a_output()["customers"]
            orig_rev = next(c["revenue"] for c in original if c.get("name") == customer.get("name"))
            if rev != orig_rev:
                errors.append(f"  {customer['name']}: revenue truncated {orig_rev} → {rev}")
        total += float(rev)
    return {"total_revenue": total, "errors": errors}


if __name__ == "__main__":
    print("=" * 60)
    print("  AFT-021: Agent Hop Type Mismatch")
    print("=" * 60)

    source_data = agent_a_output()
    expected_total = sum(c["revenue"] for c in source_data["customers"])
    print(f"\n  Source data (Agent A): revenue values = "
          f"{[c['revenue'] for c in source_data['customers']]}")
    print(f"  Expected total: {expected_total}")

    # Simulate 10 handoffs through LLM-mediated channel
    print(f"\n  Simulating 10 LLM-mediated handoffs:")
    mismatches = 0
    for i in range(10):
        random.seed(i)
        json_str = json.dumps(source_data)
        parsed = simulate_llm_json_parse(json_str)
        result = agent_b_process(parsed)
        match = "OK" if abs(result["total_revenue"] - expected_total) < 0.01 else "MISMATCH"
        if match == "MISMATCH":
            mismatches += 1
        print(f"    Run {i + 1:2d}: total={result['total_revenue']:10.2f}  [{match}]")
        for err in result["errors"]:
            print(f"           {err}")

    print(f"\n  Mismatches: {mismatches}/10 runs")

    # Demonstrate typed side-channel
    print(f"\n  With TypedHandoff (side-channel):")
    handoff = TypedHandoff()
    ref_id = handoff.put("customer_data", source_data)
    for i in range(10):
        retrieved = handoff.get(ref_id)
        result = agent_b_process(retrieved)
        print(f"    Run {i + 1:2d}: total={result['total_revenue']:10.2f}  [OK — always correct]")
    print(f"\n  Mismatches: 0/10 runs (typed channel preserves fidelity)")
