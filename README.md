# Production AI Agent Failures

Production failure modes in multi-agent systems. Not edge cases from blog posts.

These are failures I have hit running agents in production across Databricks Mosaic AI Supervisor, LangGraph, OpenClaw/Ari (WhatsApp AI assistant with Claude backend), and Databricks Apps. Each entry has: the system context, what I expected to happen, what actually happened, the first (wrong) mitigation I tried, and what actually fixed it.

If you are running agents in production and nothing has broken yet, you haven't had enough traffic.

## What This Is

A practitioner's field guide to the operational failures that take down production agent systems. The thesis: everyone talks about hallucination. The actual failure modes that kill your agent in production are **operational**, not semantic -- retry logic that amplifies instead of recovering, context windows that silently truncate your system prompt, supervisor agents that enter delegation cycles, memory stores that return stale facts with high confidence.

Every entry includes a "first wrong fix" because the obvious mitigation is frequently wrong. Knowing what _not_ to do is half the battle.

## What This Is Not

- Not a tutorial on building agents
- Not a list of prompt engineering tips
- Not theoretical -- every failure was observed in a real system
- Not comprehensive -- this is what I've hit so far, not everything that can go wrong

## How to Navigate

**By failure class:** Start with [TAXONOMY.md](TAXONOMY.md) for the seven failure classes and their definitions. Each class has a directory under `taxonomy/` with individual failure entries.

**By symptom (during an incident):** Use the [decision trees](decision_trees/):
- [Which failure class?](decision_trees/which_failure_class.md) -- top-level triage
- [Loop or not?](decision_trees/loop_or_not.md) -- disambiguate loop patterns
- [Context budget](decision_trees/context_budget.md) -- context exhaustion triage

**By stack:**
| Stack | Most Relevant Entries |
|-------|----------------------|
| Databricks Mosaic AI Supervisor | AFT-001, AFT-020, AFT-030, AFT-031 |
| LangGraph | AFT-002, AFT-011, AFT-020, AFT-021 |
| OpenClaw/Ari | AFT-010, AFT-040, AFT-041 |
| Databricks Apps (FastAPI) | AFT-060, AFT-061 |

## Stack Context

- **Databricks Mosaic AI Supervisor:** Multi-agent orchestration where a supervisor LLM delegates to specialized subagents. Failures concentrate at delegation boundaries and in supervisor-subagent communication.
- **LangGraph:** Stateful agent graphs with cycles, checkpointing, and tool nodes. Failures concentrate in state accumulation, checkpoint serialization, and graph cycle management.
- **OpenClaw/Ari:** WhatsApp AI assistant with Claude backend, two-tier memory architecture (hot-path daily logs + cold-path durable facts). Failures concentrate in long-running sessions and memory consistency.
- **Databricks Apps:** FastAPI-based agent deployments with MLflow tracing. Failures concentrate in async context propagation and observability gaps.

## Quick Reference

| ID | Name | Class | Severity | Detection Signal | First Wrong Fix | Correct Fix |
|----|------|-------|----------|-----------------|----------------|-------------|
| [AFT-001](taxonomy/loop_detection/AFT-001_naive-retry-amplification.md) | Naive Retry Amplification | Loop & Recursion | P1 | input_tokens increasing >50% per call | Increase retry limit | Idempotency key + replace semantics |
| [AFT-002](taxonomy/loop_detection/AFT-002_tool-call-oscillation.md) | Tool Call Oscillation | Loop & Recursion | P2 | unique_tools/total_calls < 0.4 | max_tool_calls limit | Oscillation detector + meta-prompt |
| [AFT-010](taxonomy/context_exhaustion/AFT-010_multiturn-state-blowup.md) | Multi-turn State Blowup | Context Budget | P1 | session_tokens/max_context > 0.6 | Naive history trimming | Proactive summarization at 60% |
| [AFT-011](taxonomy/context_exhaustion/AFT-011_tool-result-accumulation.md) | Tool Result Accumulation | Context Budget | P2 | tool_result_tokens/total > 0.4 | Truncate tool results | Tool result summarization at boundary |
| [AFT-020](taxonomy/serialization/AFT-020_checkpoint-schema-drift.md) | Checkpoint Schema Drift | Serialization | P1 | null in required checkpoint fields | schema_evolution_mode=rescue | Versioned schema + migration functions |
| [AFT-021](taxonomy/serialization/AFT-021_agent-hop-type-mismatch.md) | Agent Hop Type Mismatch | Serialization | P2 | Type assertion failures across agent boundary | Type hints in prompt | Typed side-channel for structured data |
| [AFT-030](taxonomy/supervisor_deadlock/AFT-030_circular-delegation.md) | Circular Delegation | Supervisor | P1 | Same agent_id 2x in delegation chain | max_delegation_depth | Delegation chain + cycle detection |
| [AFT-031](taxonomy/supervisor_deadlock/AFT-031_subagent-silent-failure.md) | Subagent Silent Failure | Supervisor | P1 | Subagent errors != supervisor errors | Check for None returns | Structured response envelope |
| [AFT-040](taxonomy/memory_retrieval/AFT-040_hybrid-search-staleness.md) | Hybrid Search Staleness | Memory | P1 | User corrections repeated 2x for same fact | Recency boost in scoring | Write-through invalidation + tombstones |
| [AFT-041](taxonomy/memory_retrieval/AFT-041_cold-path-hot-path-divergence.md) | Cold/Hot Path Divergence | Memory | P2 | oldest_unextracted_age > retention/2 | Extend retention window | Extraction output validation + SLA |
| [AFT-050](taxonomy/tool_execution/AFT-050_partial-tool-success.md) | Partial Tool Success | Tool Execution | P2 | Requested scope != returned scope | Validate internal consistency | Request-response reconciliation |
| [AFT-051](taxonomy/tool_execution/AFT-051_schema-mismatch-silent-drop.md) | Schema Mismatch Silent Drop | Tool Execution | P2 | Required field null rate spike | extra="forbid" | Required field check + alias resolution |
| [AFT-060](taxonomy/observability/AFT-060_mlflow-trace-span-loss.md) | MLflow Trace Span Loss | Observability | P2 | Orphaned spans / traces with 0 children | Add @mlflow.trace decorators | Explicit async context propagation |
| [AFT-061](taxonomy/observability/AFT-061_missing-tool-call-attribution.md) | Missing Tool Call Attribution | Observability | P3 | Tool call count mismatch (logs vs traces) | Increase event payload size | First-class tool spans with full I/O |

## Mitigation Patterns

Reusable Python modules extracted from the correct fixes. No framework dependencies -- these run anywhere.

| Module | Addresses | Description |
|--------|-----------|-------------|
| [`loop_circuit_breaker.py`](mitigation_patterns/loop_circuit_breaker.py) | AFT-001, AFT-002 | Tracks tool call signatures in a sliding window, detects exact loops and oscillation |
| [`context_budget_tracker.py`](mitigation_patterns/context_budget_tracker.py) | AFT-010, AFT-011 | Tracks token consumption, fires callbacks at configurable thresholds |
| [`checkpoint_schema_validator.py`](mitigation_patterns/checkpoint_schema_validator.py) | AFT-020 | Versioned schema registry with migration chain for checkpoint validation |
| [`supervisor_heartbeat.py`](mitigation_patterns/supervisor_heartbeat.py) | AFT-030, AFT-031 | Delegation chain tracking + structured response envelopes |

## Notebooks

Interactive demonstrations that reproduce each failure and walk through wrong vs. correct mitigations:

- [`aft001_loop_amplification_demo.ipynb`](notebooks/aft001_loop_amplification_demo.ipynb)
- [`aft010_context_exhaustion_demo.ipynb`](notebooks/aft010_context_exhaustion_demo.ipynb)
- [`aft030_supervisor_deadlock_demo.ipynb`](notebooks/aft030_supervisor_deadlock_demo.ipynb)

## Contributing

Found a failure mode not covered here? Open an issue using the [new failure mode template](.github/ISSUE_TEMPLATE/new_failure_mode.md). The template mirrors the taxonomy format -- fill in what you saw, what you tried first, and what actually worked.

The bar for inclusion: it happened in production (or a production-realistic load test), it was non-obvious, and the first fix was wrong.
