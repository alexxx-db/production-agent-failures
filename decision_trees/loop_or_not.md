# Loop or Not? — Loop Failure Sub-Classification

You're here because the agent is consuming resources without producing useful output. This tree disambiguates the specific loop pattern.

## Decision Tree

```
Agent is spinning / consuming tokens without progress
│
├── Are tool calls being made?
│   │
│   ├── YES — look at the tool call sequence
│   │   │
│   │   ├── Are the tool calls IDENTICAL (same name + same args)?
│   │   │   │
│   │   │   ├── YES → AFT-001: Naive Retry Amplification
│   │   │   │   Mechanism: retry logic appending to history instead of replacing
│   │   │   │   Key signal: input_tokens increasing >50% per call in same step
│   │   │   │   Fix: idempotency key pattern (replace, don't append)
│   │   │   │
│   │   │   └── Sort of — same tool name, DIFFERENT args each time
│   │   │       │
│   │   │       ├── Is it alternating between 2-3 tools? (A-B-A-B pattern)
│   │   │       │   │
│   │   │       │   └── YES → AFT-002: Tool Call Oscillation
│   │   │       │       Mechanism: two tools return individually insufficient results
│   │   │       │       Key signal: unique_tools / total_calls ratio < 0.4 over 10+ calls
│   │   │       │       Fix: oscillation detector with meta-prompt injection
│   │   │       │
│   │   │       └── Is it the same tool with progressively refined args?
│   │   │           → Possibly legitimate search refinement, not a loop
│   │   │           Check: is the agent making progress? Are results improving?
│   │   │           If no progress after 5 refinements → treat as AFT-002 variant
│   │   │
│   │   └── NO tool calls, but the agent is making LLM calls
│   │       │
│   │       └── Is the agent generating reasoning but not acting?
│   │           → "Thinking loop" — agent stuck in planning
│   │           Not in taxonomy yet, but related to AFT-002
│   │           Fix: force tool call after N consecutive reasoning-only steps
│   │
│   └── NO tool calls at all
│       │
│       ├── Is this a multi-agent system?
│       │   │
│       │   ├── YES → Check delegation logs
│       │   │   │
│       │   │   ├── Same agents appearing multiple times?
│       │   │   │   → AFT-030: Circular Delegation
│       │   │   │   Mechanism: agents redirecting to each other in a cycle
│       │   │   │   Key signal: agent_id appearing 2+ times in delegation chain
│       │   │   │   Fix: delegation chain tracking with cycle detection
│       │   │   │
│       │   │   └── Supervisor repeatedly calling same subagent?
│       │   │       → Supervisor retry loop (AFT-001 variant at supervisor level)
│       │   │       Check: is the subagent returning errors that the supervisor retries?
│       │   │
│       │   └── NO → Check for context exhaustion (not a loop)
│       │       See: context_budget.md
│       │
│       └── Is context growing without progress?
│           → AFT-010: Multi-turn State Blowup
│           This looks like a loop but is actually context exhaustion
│           See: context_budget.md
```

## Distinguishing Loops from Non-Loops

| Observation | Loop? | Likely Cause |
|-------------|-------|-------------|
| Same tool, same args, 3+ times | Yes | AFT-001 (retry amplification) |
| Alternating tools, different args | Yes | AFT-002 (oscillation) |
| Same tool, progressively refined args, results improving | No | Legitimate search refinement |
| Token count growing, no tool calls | Maybe | AFT-010 (context blowup) or delegation cycle |
| Delegation chain has repeated agent IDs | Yes | AFT-030 (circular delegation) |
| Agent produces output but it's wrong | No | Not a loop — check other classes |

## Key Metrics for Loop Detection

```
exact_repeat_count   = count(identical tool signatures in last N calls)
oscillation_ratio    = unique_tool_names / total_calls  (< 0.4 = oscillation)
token_growth_rate    = input_tokens[n] / input_tokens[n-1]  (> 1.5 = amplification)
delegation_cycle     = any(agent_id appearing 2+ times in chain)
```
