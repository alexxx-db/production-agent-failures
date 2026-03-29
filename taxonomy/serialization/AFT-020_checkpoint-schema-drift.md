## [AFT-020] Checkpoint Schema Drift

**Class:** Serialization & Schema
**Severity:** P1
**Stacks Affected:** LangGraph, Databricks Mosaic AI Supervisor
**First Observed:** After a state schema update to a LangGraph agent with Delta table checkpoint backend

---

### What I Expected

After adding a new field to the agent's state schema (`user_tier: str`), existing checkpoints should either fail to load with a clear error or be migrated to include the new field with a default value.

### What Actually Happened

Existing checkpoints loaded without error. The Delta table's schema evolution mode (`mergeSchema`) silently added the new column with null values. The agent resumed from checkpoint and began operating with `user_tier = None`. The downstream logic used `user_tier` to select a tool routing strategy — the None value matched no routing rule and fell through to a default path that was designed for internal testing, not production traffic. The agent started returning raw debug output to end users.

There was no schema validation error. No type mismatch warning. The Delta table happily evolved, and the agent happily operated on corrupted state.

```
# Checkpoint loaded — no error
state = checkpoint.load(session_id="abc123")
# state.user_tier is None (should be "enterprise")
# Routing logic falls through:
if state.user_tier == "enterprise": use_production_tools()
elif state.user_tier == "trial": use_limited_tools()
else: use_debug_tools()  # <-- this path was never supposed to see real traffic
```

### Why It Was Non-Obvious

Delta table schema evolution is a feature, not a bug. `mergeSchema` is the recommended mode for evolving data pipelines. But the semantics that make it safe for ETL pipelines (additive columns with nulls are fine for analytics) are exactly wrong for agent state checkpoints (null in a routing field is a logic bomb). The failure was masked by the very feature designed to prevent schema-related crashes.

### First (Wrong) Mitigation

Set `schema_evolution_mode = "rescue"` on the Delta table. This is even worse — rescue mode silently coerces incompatible types and stuffs unrecognized columns into a `_rescued_data` column. The corrupted state was now invisible even in the raw checkpoint data.

### Root Cause

Agent state checkpoints have stricter schema requirements than data pipeline tables. Every field in agent state is load-bearing — a null, a missing key, or a type coercion changes the agent's behavior. Delta table schema evolution assumes additive-only, analytics-safe changes. There is no built-in mechanism to enforce "if the schema doesn't match exactly, fail loud."

### Correct Mitigation

Explicit checkpoint schema versioning with migration functions. Each state schema has a version number. When a checkpoint is loaded, its schema version is compared to the current code's expected version. Mismatches trigger a migration function, not silent evolution.

```python
SCHEMA_V1 = {"session_id": str, "messages": list, "current_agent": str}
SCHEMA_V2 = {**SCHEMA_V1, "user_tier": str}  # Added field

MIGRATIONS = {
    (1, 2): lambda state: {**state, "user_tier": infer_tier(state["session_id"])},
}

def load_checkpoint(session_id: str) -> dict:
    raw = delta_table.read(session_id)
    stored_version = raw.get("_schema_version", 1)
    target_version = 2  # current

    state = raw
    while stored_version < target_version:
        migrate = MIGRATIONS.get((stored_version, stored_version + 1))
        if migrate is None:
            raise CheckpointSchemaMismatchError(
                f"No migration from v{stored_version} to v{stored_version + 1}"
            )
        state = migrate(state)
        stored_version += 1

    state["_schema_version"] = target_version
    return state
```

### Detection Signal

Any `null` value in a checkpoint field that has a `NOT NULL` semantic in application logic. Monitor checkpoint loads and assert that all required fields are populated. This is a startup-time check, not a runtime check — catch it on agent restart before it serves traffic.

### Repro

See [`repros/aft020_repro.py`](repros/aft020_repro.py). Creates a v1 checkpoint, evolves the schema to v2, and shows how unversioned loading produces corrupted state vs. versioned loading with migration.

### References

Delta Lake documentation covers `mergeSchema` and `rescue` mode for data pipelines. There is no documentation specific to using Delta tables as agent checkpoint backends. LangGraph's checkpoint interface is storage-agnostic and does not enforce schema validation.
