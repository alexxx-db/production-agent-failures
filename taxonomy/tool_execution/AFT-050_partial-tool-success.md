## [AFT-050] Partial Tool Success

**Class:** Tool Execution
**Severity:** P2
**Stacks Affected:** Databricks Mosaic AI Supervisor, LangGraph, OpenClaw/Ari
**First Observed:** Agent calling a batch API that returns partial results on timeout

---

### What I Expected

Agent calls a tool that queries a customer database for accounts in three regions (NA, EU, APAC). The tool should return all matching accounts or fail entirely.

### What Actually Happened

The tool hit a 10-second timeout after processing NA and EU but before completing APAC. The HTTP response was a 200 with a body containing NA and EU results — the API returned what it had before the timeout. No error. No indication of partial results. The agent received 2 out of 3 regions and reasoned as if it had complete data.

The agent's response: "Based on your global customer base of 847 accounts across all regions..." It was actually 847 accounts across 2 regions. The APAC data (312 accounts) was silently missing. The user made a business decision based on incomplete data presented with full confidence.

```
Tool response (HTTP 200):
{
  "accounts": [...],    // 847 accounts (NA + EU only)
  "query": "region IN ('NA', 'EU', 'APAC')",
  "total": 847
}
// No "regions_completed" field. No "partial" flag. 200 OK.
```

### Why It Was Non-Obvious

The HTTP status was 200. The response body was valid JSON matching the expected schema. The `total` field was internally consistent (847 matched the array length). There was no signal in the tool response that data was missing — the tool reported what it found, not what it didn't find. The agent has no way to know that 847 is less than the true total unless it independently knows the expected count.

Partial-success APIs are common in batch operations, search systems, and database queries with timeouts. The API designers consider this a feature (return partial results instead of nothing on timeout). For an LLM agent, partial results presented as complete results is strictly worse than an error.

### First (Wrong) Mitigation

Added a tool-result validation check: verify that the `total` field matches `len(accounts)`. This passed — the total was consistent with the returned data. It just wasn't the total across all requested regions. The validation checked internal consistency, not completeness.

### Root Cause

The tool's API returns partial results on timeout without signaling that the results are incomplete. The tool wrapper trusts the API response shape and passes it to the LLM unmodified. The LLM trusts the tool result because its tool-use training teaches it to treat tool outputs as factual. No component in the chain validates that the response contains results for all requested inputs.

### Correct Mitigation

Request-response reconciliation at the tool wrapper level. The wrapper compares what was requested against what was returned and adds a completeness signal to the tool result.

```python
def search_accounts_tool(regions: list[str]) -> dict:
    raw_result = api.search(regions=regions, timeout=10)

    returned_regions = set(r["region"] for r in raw_result["accounts"])
    requested_regions = set(regions)
    missing_regions = requested_regions - returned_regions

    result = {
        "accounts": raw_result["accounts"],
        "total": raw_result["total"],
        "completeness": "complete" if not missing_regions else "partial",
        "missing_regions": list(missing_regions) if missing_regions else None,
        "warning": (
            f"Results are PARTIAL. Missing data for regions: {missing_regions}. "
            f"Do not present these results as complete."
        ) if missing_regions else None,
    }
    return result
```

The agent's system prompt includes: "If a tool result contains `completeness: partial`, explicitly tell the user which data is missing. Never present partial results as if they are complete."

### Detection Signal

Mismatch between the requested scope (e.g., 3 regions) and the response scope (e.g., results from only 2 regions). Monitor `requested_items - returned_items` as a metric per tool call. Any non-zero value is a completeness issue.

### Repro

See [`repros/aft050_repro.py`](repros/aft050_repro.py). Simulates a batch API that returns partial results on timeout, showing how the agent processes incomplete data as complete.

### References

No standard documentation addresses partial-success tool results in agentic systems. The pattern is analogous to partial-failure handling in distributed systems (e.g., multi-shard database queries), where the query coordinator must reconcile responses from individual shards.
