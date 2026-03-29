## [AFT-021] Agent Hop Type Mismatch

**Class:** Serialization & Schema
**Severity:** P2
**Stacks Affected:** Databricks Mosaic AI Supervisor, LangGraph
**First Observed:** Multi-agent pipeline where Agent A passes structured data to Agent B via the supervisor

---

### What I Expected

Agent A produces a structured result (a list of customer records with typed fields). The supervisor passes this to Agent B, which processes the records. Agent B should receive the same data types that Agent A produced.

### What Actually Happened

Agent A returned a list of dicts with `revenue` as `float` (e.g., `1234.56`). The supervisor serialized this to JSON for the handoff message. Agent B received it as a string in a user message. The LLM powering Agent B parsed the JSON string back into its internal representation, but interpreted `revenue` as `int` in some cases (truncating `1234.56` to `1234`) and as `str` in others (`"1234.56"`), depending on how it parsed the embedded JSON.

The type mismatch was non-deterministic. On 70% of runs, Agent B handled it correctly. On 30%, it produced subtly wrong calculations — off by cents, not dollars — making it hard to catch in testing but wrong enough to matter in financial reporting.

```
Agent A output: {"customers": [{"id": 1, "revenue": 1234.56}]}
Supervisor message to B: "Agent A found: {\"customers\": [{\"id\": 1, \"revenue\": 1234.56}]}"
Agent B internal parse (varies by run):
  - revenue = 1234.56  (correct, 70% of the time)
  - revenue = 1234     (truncated, 20% of the time)
  - revenue = "1234.56" (string, 10% of the time — downstream math fails silently)
```

### Why It Was Non-Obvious

The data was not "lost" — it was present in the message. The failure is in the serialization roundtrip: `structured data → JSON string → LLM message → LLM's internal parse → structured data`. The LLM step in the middle is a non-deterministic parser. There is no guarantee that the LLM will reconstruct types identically, especially for ambiguous cases like numeric strings, floats that look like ints, and nested arrays.

### First (Wrong) Mitigation

Added explicit type annotations in the supervisor's handoff prompt: "Revenue values are floats, not integers." This helped — reduced the mismatch rate from 30% to ~10% — but did not eliminate it. Prompt-based type enforcement is advisory, not contractual.

### Root Cause

Passing structured data through an LLM-mediated channel (the supervisor's natural-language handoff message) destroys type guarantees. JSON serialization preserves types, but the LLM that reads the JSON is not a JSON parser — it's a language model that approximates parsing. The serialization-deserialization path goes through a lossy channel (the LLM's comprehension) that introduces non-deterministic type coercion.

### Correct Mitigation

Bypass the LLM for structured data handoffs. Pass structured results through a typed side-channel (shared state dict, tool result schema, or explicit typed parameter) that is not mediated by the LLM's natural-language processing.

```python
class TypedHandoff:
    """Side-channel for structured data between agents."""

    def __init__(self):
        self._store: dict[str, Any] = {}

    def put(self, key: str, value: Any, schema: dict) -> str:
        """Store typed data, return a reference ID for the LLM message."""
        ref_id = f"handoff_{uuid4().hex[:8]}"
        self._store[ref_id] = {"value": value, "schema": schema}
        return ref_id

    def get(self, ref_id: str) -> Any:
        """Retrieve typed data by reference. Validates against stored schema."""
        entry = self._store[ref_id]
        validate(entry["value"], entry["schema"])
        return entry["value"]
```

The supervisor message references the data by ID: "Agent A found customer data (ref: handoff_a3f2c1). Process the revenue analysis." Agent B's tool retrieves the typed data from the side-channel.

### Detection Signal

Type assertion failures or unexpected string-to-numeric coercions in Agent B's processing logic. Monitor the types of values in structured fields across agent boundaries — if `revenue` is ever `str` when it should be `float`, the handoff is lossy.

### Repro

See [`repros/aft021_repro.py`](repros/aft021_repro.py). Simulates a supervisor handoff where structured data passes through a string serialization roundtrip, demonstrating non-deterministic type coercion.

### References

No public documentation addresses type fidelity in multi-agent handoffs. The problem is analogous to distributed systems type coercion (e.g., gRPC vs. REST), but the lossy channel here is an LLM, not a network protocol.
