## [AFT-051] Schema Mismatch Silent Drop

**Class:** Tool Execution
**Severity:** P2
**Stacks Affected:** LangGraph, Databricks Mosaic AI Supervisor
**First Observed:** After an external API provider updated their response schema without notice

---

### What I Expected

Agent calls a tool wrapping an external API. The API returns customer data with fields: `name`, `email`, `tier`, `last_purchase_date`. The tool schema defines these fields. The agent uses `tier` to make a routing decision.

### What Actually Happened

The API provider renamed `tier` to `account_tier` in a minor release. The tool wrapper used a Pydantic model to parse the response. Pydantic's default behavior (`model_config = ConfigDict(extra="ignore")`) silently dropped the unrecognized `account_tier` field and set `tier` to its default value of `None`.

The agent received a response where every customer had `tier: None`. It routed all customers to the default (lowest-privilege) path. Enterprise customers lost access to premium features. No error. No warning. The Pydantic validation passed because `tier` was Optional with a default.

```python
class CustomerResponse(BaseModel):
    name: str
    email: str
    tier: str | None = None  # Was populated until the API changed
    last_purchase_date: str

# API returns: {"name": "Acme", "email": "...", "account_tier": "enterprise", ...}
# Pydantic parses to: CustomerResponse(name="Acme", email="...", tier=None, ...)
# "account_tier" silently dropped. "tier" defaults to None.
```

### Why It Was Non-Obvious

Pydantic models with `extra="ignore"` (the default) are designed to be forward-compatible — unknown fields are dropped silently. This is the correct behavior for API clients that need to tolerate schema additions. But when a field is renamed (not added), the old field disappears from the response and the new field is dropped by the parser. The result is data loss that looks like missing data.

The tool schema validation passed. The response matched the expected type signature. The model was well-formed. It was just wrong.

### First (Wrong) Mitigation

Changed the Pydantic model to `extra="forbid"` to catch unknown fields. This broke every API call because the response contained other fields that had been silently ignored for months (fields that were irrelevant to the agent but present in the API response). The fix was too strict — it went from silently dropping one field to rejecting every response.

### Root Cause

The tool schema assumes the external API's field names are stable. There is no contract enforcement between the API and the tool wrapper. The tool was written against a point-in-time API snapshot, and the validation logic (Pydantic) was configured to tolerate exactly the kind of change that broke it.

### Correct Mitigation

Two layers:

1. **Required field presence check** (not type check): Before parsing, verify that all required field names exist in the raw response. If `tier` is required for agent routing, assert its presence before parsing.

2. **Schema drift detection**: Log all `extra` fields seen in API responses. When a new field appears that is semantically similar to a required field (e.g., `account_tier` vs. `tier`), fire an alert.

```python
REQUIRED_FIELDS = {"name", "email", "tier"}
KNOWN_ALIASES = {"tier": ["account_tier", "customer_tier", "membership_tier"]}

def validate_and_parse(raw: dict) -> CustomerResponse:
    # Check required fields, including known aliases
    for field in REQUIRED_FIELDS:
        if field not in raw:
            aliases = KNOWN_ALIASES.get(field, [])
            found_alias = next((a for a in aliases if a in raw), None)
            if found_alias:
                raw[field] = raw.pop(found_alias)
                log.warning(f"Field '{field}' missing, used alias '{found_alias}'")
            else:
                raise ToolSchemaError(f"Required field '{field}' missing from API response")

    return CustomerResponse.model_validate(raw)
```

### Detection Signal

Required fields defaulting to `None` when they were previously populated. Monitor the null rate of critical fields in tool responses — if `tier` goes from 0% null to 100% null overnight, the API schema changed.

### Repro

See [`repros/aft051_repro.py`](repros/aft051_repro.py). Simulates an API schema change where a field is renamed, showing how Pydantic's default behavior silently drops the data.

### References

Pydantic V2 documentation on `extra` configuration. This is not a Pydantic bug — it's working as designed. The failure is in the mismatch between Pydantic's intended use case (tolerant parsing) and the agent's requirement (strict schema compliance for routing-critical fields).
