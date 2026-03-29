"""
AFT-020 Repro: Checkpoint Schema Drift

Demonstrates how schema evolution on a checkpoint store silently introduces
null values in load-bearing fields, corrupting agent routing logic.

Run: python aft020_repro.py
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Callable


class InMemoryCheckpointStore:
    """Simulates a Delta table checkpoint backend."""

    def __init__(self, schema_evolution: str = "merge"):
        self.store: dict[str, dict] = {}
        self.schema_evolution = schema_evolution

    def save(self, session_id: str, state: dict) -> None:
        self.store[session_id] = deepcopy(state)

    def load(self, session_id: str) -> dict | None:
        raw = self.store.get(session_id)
        if raw is None:
            return None
        return deepcopy(raw)


class CheckpointSchemaMismatchError(Exception):
    pass


class VersionedCheckpointStore:
    """Checkpoint store with explicit schema versioning and migration."""

    def __init__(self):
        self.store: dict[str, dict] = {}
        self.current_version = 2
        self.migrations: dict[tuple[int, int], Callable] = {
            (1, 2): self._migrate_v1_to_v2,
        }

    @staticmethod
    def _migrate_v1_to_v2(state: dict) -> dict:
        """Add user_tier field with a derived default."""
        state = deepcopy(state)
        # In production, you'd look up the user's tier from a service
        state["user_tier"] = "standard"  # Safe default, not None
        state["_schema_version"] = 2
        return state

    def save(self, session_id: str, state: dict) -> None:
        state = deepcopy(state)
        state["_schema_version"] = self.current_version
        self.store[session_id] = state

    def load(self, session_id: str) -> dict | None:
        raw = self.store.get(session_id)
        if raw is None:
            return None

        raw = deepcopy(raw)
        stored_version = raw.get("_schema_version", 1)

        while stored_version < self.current_version:
            next_version = stored_version + 1
            migrate = self.migrations.get((stored_version, next_version))
            if migrate is None:
                raise CheckpointSchemaMismatchError(
                    f"No migration path from v{stored_version} to v{next_version}. "
                    f"Current code expects v{self.current_version}."
                )
            raw = migrate(raw)
            stored_version = next_version

        return raw


def route_agent(state: dict) -> str:
    """Production routing logic that depends on user_tier."""
    tier = state.get("user_tier")
    if tier == "enterprise":
        return "production_enterprise_tools"
    elif tier == "standard":
        return "production_standard_tools"
    elif tier == "trial":
        return "production_trial_tools"
    else:
        return "DEBUG_TOOLS"  # Should never reach production traffic


if __name__ == "__main__":
    print("=" * 60)
    print("  AFT-020: Checkpoint Schema Drift")
    print("=" * 60)

    # Save a v1 checkpoint (no user_tier field)
    v1_state = {
        "session_id": "abc123",
        "messages": [{"role": "user", "content": "Hello"}],
        "current_agent": "intake",
    }

    # --- Scenario 1: Unversioned store with schema evolution ---
    print("\n  Scenario 1: Delta-style schema evolution (BROKEN)")
    naive_store = InMemoryCheckpointStore(schema_evolution="merge")
    naive_store.save("abc123", v1_state)

    loaded = naive_store.load("abc123")
    route = route_agent(loaded)
    print(f"    user_tier = {loaded.get('user_tier')!r}")
    print(f"    Routed to: {route}")
    print(f"    PROBLEM: {route == 'DEBUG_TOOLS'}")

    # --- Scenario 2: Versioned store with migration ---
    print("\n  Scenario 2: Versioned checkpoint with migration (CORRECT)")
    versioned_store = VersionedCheckpointStore()
    # Simulate: checkpoint was saved by old code (v1, no version field)
    versioned_store.store["abc123"] = deepcopy(v1_state)

    loaded_v = versioned_store.load("abc123")
    route_v = route_agent(loaded_v)
    print(f"    user_tier = {loaded_v.get('user_tier')!r}")
    print(f"    Routed to: {route_v}")
    print(f"    Correct:   {route_v != 'DEBUG_TOOLS'}")
