## [AFT-002] Tool Call Oscillation

**Class:** Loop & Recursion
**Severity:** P2
**Stacks Affected:** LangGraph, Databricks Mosaic AI Supervisor
**First Observed:** Agent workflow handling ambiguous user queries requiring multiple data sources

---

### What I Expected

The agent should call Tool A, evaluate the result, determine it needs Tool B for a complementary piece of information, call Tool B, and synthesize a response.

### What Actually Happened

The agent called Tool A, received a partial result, determined it needed Tool B. Called Tool B, received a result that the model judged insufficient, decided Tool A with a different query would help. Called Tool A again, got a slightly different partial result, decided it needed Tool B again. This oscillation continued until the context window filled or a max-iterations guard fired.

The tool calls were not identical — each iteration used slightly different query parameters — so exact-match loop detection did not trigger. From the outside, the agent appeared to be "thinking hard" about a complex query.

```
Step 1: call search_docs(query="customer retention policy")
Step 2: call query_metrics(metric="churn_rate", period="Q3")
Step 3: call search_docs(query="customer retention policy Q3 churn")
Step 4: call query_metrics(metric="churn_rate", period="Q3", segment="enterprise")
Step 5: call search_docs(query="enterprise customer retention Q3")
... [continues for 14 more steps]
```

### Why It Was Non-Obvious

Each individual tool call was reasonable. The agent was genuinely trying to gather enough information to answer the question. The oscillation pattern only becomes visible when you look at the sequence of tool names, not the individual calls. Standard loop detection checks for identical calls, not for A-B-A-B patterns.

### First (Wrong) Mitigation

Added a `max_tool_calls` limit of 10 per agent step. This stopped the oscillation but also broke legitimate multi-step workflows that required 8-12 tool calls. The limit was either too low (breaking valid flows) or too high (allowing oscillation to run long enough to burn tokens).

### Root Cause

The model enters oscillation when two tools return results that are individually insufficient but don't overlap enough to synthesize a complete answer. The model correctly identifies that more information is needed, but its planning horizon is too short to realize it's cycling between the same two sources. Each call produces a slightly different result (due to different query parameters), which the model interprets as "progress."

### Correct Mitigation

Tool call signature tracking with fuzzy matching. Track not just exact `(function_name, args)` tuples, but `function_name` frequency in a sliding window. If the same tool is called more than N times within a window of M calls, inject a meta-prompt: "You have called {tool} {N} times in {M} steps. Summarize what you have learned so far and determine if a different approach is needed."

```python
class OscillationDetector:
    def __init__(self, window: int = 6, threshold: int = 3):
        self.window = window
        self.threshold = threshold
        self.history: list[str] = []

    def record(self, tool_name: str) -> str | None:
        self.history.append(tool_name)
        recent = self.history[-self.window:]
        for name in set(recent):
            if recent.count(name) >= self.threshold:
                return name
        return None
```

### Detection Signal

Tool call name sequence showing alternating patterns: `A, B, A, B` or `A, B, C, A, B, C` within a single agent step. Monitor the ratio of unique tool names to total tool calls per step — a ratio below 0.4 over 10+ calls indicates oscillation.

### Repro

See [`repros/aft002_repro.py`](repros/aft002_repro.py). Sets up two mock tools that return partial results, triggering the model into an oscillation pattern.

### References

Related to LangGraph issue discussions on recursion limits. The oscillation pattern is distinct from simple recursion — it's a multi-tool cycle, not a single-tool loop.
