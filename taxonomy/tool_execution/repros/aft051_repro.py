"""
AFT-051 Repro: Schema Mismatch Silent Drop

Demonstrates how a Pydantic model with extra="ignore" silently drops renamed
fields from an API response, causing the agent to route on None values.

Run: python aft051_repro.py

Requires: pydantic (or runs with a simulation if not installed)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


# --- Simulated Pydantic-like behavior (no dependency required) ---

class SimulatedModel:
    """Mimics Pydantic model_validate with extra='ignore' behavior."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __repr__(self):
        attrs = ", ".join(f"{k}={v!r}" for k, v in self.__dict__.items())
        return f"{self.__class__.__name__}({attrs})"


class CustomerResponse(SimulatedModel):
    @classmethod
    def model_validate(cls, data: dict, extra: str = "ignore") -> "CustomerResponse":
        fields = {"name": str, "email": str, "tier": None, "last_purchase_date": str}
        parsed = {}
        for field, default in fields.items():
            parsed[field] = data.get(field, default)
        if extra == "forbid":
            extra_fields = set(data.keys()) - set(fields.keys())
            if extra_fields:
                raise ValueError(f"Extra fields not allowed: {extra_fields}")
        return cls(**parsed)


# --- Known alias mapping for correct mitigation ---

REQUIRED_FIELDS = {"name", "email", "tier"}
KNOWN_ALIASES = {"tier": ["account_tier", "customer_tier", "membership_tier"]}


class ToolSchemaError(Exception):
    pass


def validate_and_parse(raw: dict) -> CustomerResponse:
    """CORRECT: Check required fields with alias resolution."""
    raw = dict(raw)  # Don't mutate original
    for field_name in REQUIRED_FIELDS:
        if field_name not in raw:
            aliases = KNOWN_ALIASES.get(field_name, [])
            found_alias = next((a for a in aliases if a in raw), None)
            if found_alias:
                raw[field_name] = raw.pop(found_alias)
                print(f"    ALIAS RESOLVED: '{found_alias}' → '{field_name}'")
            else:
                raise ToolSchemaError(f"Required field '{field_name}' missing from response")
    return CustomerResponse.model_validate(raw)


def route_customer(customer: CustomerResponse) -> str:
    if customer.tier == "enterprise":
        return "premium_support"
    elif customer.tier == "standard":
        return "standard_support"
    else:
        return "basic_support"  # Default for None/unknown


if __name__ == "__main__":
    print("=" * 60)
    print("  AFT-051: Schema Mismatch Silent Drop")
    print("=" * 60)

    # Original API response (before schema change)
    old_api_response = {
        "name": "Acme Corp",
        "email": "admin@acme.com",
        "tier": "enterprise",
        "last_purchase_date": "2025-01-15",
    }

    # New API response (after rename: tier → account_tier)
    new_api_response = {
        "name": "Acme Corp",
        "email": "admin@acme.com",
        "account_tier": "enterprise",  # RENAMED
        "last_purchase_date": "2025-01-15",
    }

    # Scenario 1: Old API, everything works
    print("\n  Scenario 1: Original API schema")
    customer1 = CustomerResponse.model_validate(old_api_response)
    route1 = route_customer(customer1)
    print(f"    Parsed: tier={customer1.tier!r}")
    print(f"    Route:  {route1}")

    # Scenario 2: New API, silent drop with extra="ignore"
    print("\n  Scenario 2: After API rename (extra='ignore') — BROKEN")
    customer2 = CustomerResponse.model_validate(new_api_response, extra="ignore")
    route2 = route_customer(customer2)
    print(f"    Parsed: tier={customer2.tier!r}  ← 'account_tier' was silently dropped")
    print(f"    Route:  {route2}  ← WRONG: enterprise customer gets basic support")

    # Scenario 3: New API, extra="forbid" — too strict
    print("\n  Scenario 3: After API rename (extra='forbid') — TOO STRICT")
    try:
        customer3 = CustomerResponse.model_validate(new_api_response, extra="forbid")
    except ValueError as e:
        print(f"    ERROR: {e}")
        print(f"    Problem: Rejects the entire response, not just the renamed field")

    # Scenario 4: Correct — alias resolution
    print("\n  Scenario 4: With alias resolution — CORRECT")
    customer4 = validate_and_parse(new_api_response)
    route4 = route_customer(customer4)
    print(f"    Parsed: tier={customer4.tier!r}")
    print(f"    Route:  {route4}")
