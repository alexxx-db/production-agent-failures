## [AFT-030] Circular Delegation

**Class:** Supervisor Coordination
**Severity:** P1
**Stacks Affected:** Databricks Mosaic AI Supervisor, LangGraph
**First Observed:** Mosaic AI Supervisor deployment with 3+ specialized subagents handling overlapping domains

---

### What I Expected

User asks a question that spans two domains. Supervisor delegates to Agent A (customer data). Agent A determines the answer also requires pricing data, which is Agent B's domain. Agent B handles the pricing lookup and returns a result. The supervisor synthesizes the final response.

### What Actually Happened

Supervisor delegated to Agent A. Agent A determined it needed pricing data, but instead of calling a pricing tool directly, it returned a message saying "This requires pricing analysis — Agent B should handle this." The supervisor received this, interpreted it as "Agent A cannot answer, try Agent B." Supervisor delegated to Agent B. Agent B determined it needed customer context to price correctly, returned "This requires customer data — Agent A should handle this." The supervisor re-delegated to Agent A.

This cycle ran for 7 iterations before hitting the framework's default recursion limit. Total latency: 45 seconds. Total tokens consumed: 28,000. User saw a timeout error.

```
[0.0s]  Supervisor → Agent A (query: "What's the renewal price for Acme Corp?")
[2.1s]  Agent A → Supervisor: "Requires pricing analysis, delegate to pricing agent"
[2.3s]  Supervisor → Agent B (query: "Price renewal for Acme Corp")
[4.5s]  Agent B → Supervisor: "Need customer tier data to calculate, delegate to customer agent"
[4.7s]  Supervisor → Agent A (query: "Customer tier for Acme Corp for pricing")
[6.8s]  Agent A → Supervisor: "Requires pricing context, delegate to pricing agent"
... [cycles 4 more times]
[45.2s] RecursionError: maximum delegation depth exceeded
```

### Why It Was Non-Obvious

Each individual delegation decision was correct. Agent A genuinely needs pricing data. Agent B genuinely needs customer data. The problem is not in any single agent's reasoning — it's in the topology. Neither agent has the tools to answer the question independently, and the supervisor doesn't detect that it's re-delegating to the same pair of agents.

Standard recursion limits catch the symptom (depth exceeded) but not the cause (cycle). A depth limit of 5 would also block legitimate chains like: Supervisor → A → B → C → D → E. Depth and cycles are different problems.

### First (Wrong) Mitigation

Added `max_delegation_depth = 3` to the supervisor. This stopped the cycle but also broke legitimate 4-step delegation chains (which existed in the pricing workflow: supervisor → routing agent → customer agent → pricing agent → fulfillment agent). The depth limit was either too low (breaking valid flows) or too high (allowing cycles to burn tokens).

### Root Cause

The supervisor tracks delegation depth but not delegation history. It knows it has delegated 3 times, but not that it has delegated to Agent A twice. Cycle detection requires tracking the set of agents visited per request, not just the count.

### Correct Mitigation

Delegation chain tracking with per-request cycle detection. Each request carries a `delegation_chain: list[str]` in its metadata. Before delegating, the supervisor checks if the target agent already appears in the chain. If it does, the supervisor is instructed (via its system prompt) to either answer directly with available information or return an explicit "cannot answer — circular dependency between agents" response.

```python
class DelegationTracker:
    def __init__(self):
        self._chains: dict[str, list[str]] = {}

    def delegate(self, request_id: str, target_agent: str) -> None:
        chain = self._chains.setdefault(request_id, [])
        if target_agent in chain:
            cycle_path = chain[chain.index(target_agent):] + [target_agent]
            raise CircularDelegationError(
                f"Cycle detected: {' → '.join(cycle_path)}"
            )
        chain.append(target_agent)

    def reset(self, request_id: str) -> None:
        self._chains.pop(request_id, None)
```

Additionally, the supervisor system prompt includes: "If you have already delegated to an agent for this request and they redirected back, do not re-delegate. Instead, synthesize the best answer from available information and note what data is missing."

### Detection Signal

Same agent ID appearing twice in a single request's delegation log. Monitor `delegation_chain_length` and `unique_agents_in_chain` as metrics. When `chain_length > unique_agents * 1.5`, you have a cycle or near-cycle.

### Repro

See [`repros/aft030_repro.py`](repros/aft030_repro.py). Simulates a 3-agent supervisor topology where two agents redirect to each other, demonstrating both the naive depth-limit approach and the cycle-detection approach.

### References

Databricks Mosaic AI Agent Framework documentation describes supervisor patterns but does not address circular delegation. LangGraph's `StateGraph` supports cycles by design (for iterative workflows), which makes accidental cycles indistinguishable from intentional ones at the framework level.
