# Which Failure Class? — Top-Level Triage

Use this during an active incident to classify what you're seeing. Start at the top.

## Decision Tree

```
Is the agent producing output?
│
├── NO output (hung, timeout, or infinite spin)
│   │
│   ├── Is the agent consuming tokens/making API calls?
│   │   │
│   │   ├── YES → LOOP & RECURSION (Class 1)
│   │   │   Check: tool call frequency, identical call patterns
│   │   │   See: loop_or_not.md for sub-classification
│   │   │
│   │   └── NO (fully hung, no API activity)
│   │       │
│   │       ├── Is this a multi-agent system?
│   │       │   ├── YES → SUPERVISOR COORDINATION (Class 4)
│   │       │   │   Check: delegation logs, subagent health
│   │       │   │   Likely: AFT-030 (circular delegation) or AFT-031 (silent failure)
│   │       │   │
│   │       │   └── NO → TOOL EXECUTION (Class 6)
│   │       │       Check: pending tool calls, external API health
│   │       │       Likely: tool timeout with no retry
│   │       │
│   │       └── Did it work before and stop working after a deployment?
│   │           └── YES → SERIALIZATION & SCHEMA (Class 3)
│   │               Check: checkpoint loads, schema version mismatches
│   │               Likely: AFT-020 (checkpoint schema drift)
│
├── YES, output is PLAUSIBLE BUT WRONG
│   │
│   ├── Is the agent contradicting things the user said earlier in the session?
│   │   │
│   │   ├── YES, forgetting early-session context
│   │   │   → CONTEXT BUDGET (Class 2)
│   │   │   Check: session token count, system prompt presence
│   │   │   See: context_budget.md for sub-classification
│   │   │
│   │   └── YES, asserting stale facts from prior sessions
│   │       → MEMORY & RETRIEVAL (Class 5)
│   │       Check: retrieved context vs. current truth
│   │       Likely: AFT-040 (hybrid search staleness)
│   │
│   ├── Is the agent using wrong data that came from a tool?
│   │   │
│   │   ├── Tool returned wrong data → TOOL EXECUTION (Class 6)
│   │   │   Check: tool response content, partial success signals
│   │   │   Likely: AFT-050 (partial success) or AFT-051 (schema drop)
│   │   │
│   │   └── Tool data was correct but agent misinterpreted it
│   │       → CONTEXT BUDGET (Class 2) or SERIALIZATION (Class 3)
│   │       Check: was the tool result truncated in context?
│   │       Check: did the data cross an agent boundary?
│   │
│   └── Is this a multi-agent system where the wrong agent answered?
│       → SUPERVISOR COORDINATION (Class 4)
│       Check: delegation logs, which subagent was invoked
│
├── YES, output is ABSENT or PARTIAL
│   │
│   ├── Agent says "no results found" or "I don't have that information"
│   │   │
│   │   ├── Is the data actually available?
│   │   │   ├── YES → TOOL EXECUTION (Class 6) or SUPERVISOR (Class 4)
│   │   │   │   Check: was the tool called? Did the subagent error silently?
│   │   │   │   Likely: AFT-031 (silent failure) or AFT-050 (partial success)
│   │   │   │
│   │   │   └── NO (data genuinely missing) → Not a taxonomy failure
│   │   │
│   │   └── Agent returns truncated or incomplete answer
│   │       → CONTEXT BUDGET (Class 2)
│   │       Check: session length, tool result accumulation
│   │
│   └── Agent errors out explicitly
│       → Check error message and stack trace (not a silent failure)
│
└── CAN'T TELL (metrics look healthy but users report issues)
    → OBSERVABILITY (Class 7)
    Check: trace completeness, orphaned spans, tool call attribution
    Likely: AFT-060 (span loss) or AFT-061 (missing attribution)
    This class is a meta-failure — you're here because you can't
    see enough to classify into the other 6 classes.
```

## Quick Checks

| Signal | Most Likely Class |
|--------|-------------------|
| Token consumption spiking without user input | Loop & Recursion |
| Agent violating system prompt in later turns | Context Budget |
| Agent "forgetting" recent changes | Memory & Retrieval |
| Agent working after restart but not after resume | Serialization |
| Different answer depending on which subagent runs | Supervisor Coordination |
| Tool returns 200 but data is wrong/partial | Tool Execution |
| Metrics say healthy, users say broken | Observability |
