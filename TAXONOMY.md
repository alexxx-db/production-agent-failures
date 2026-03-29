# Agent Failure Taxonomy — Classification System

This document defines the seven failure classes used to categorize production agent failures. Every entry in the `taxonomy/` directory maps to exactly one class.

These classes were derived from failures observed in Databricks Mosaic AI Supervisor, LangGraph, OpenClaw/Ari, and Databricks Apps deployments. They are not theoretical — each one has burned real production time.

---

## 1. Loop & Recursion Failures

**Definition:** The agent enters a state it cannot escape without external intervention. The system continues consuming resources (tokens, API calls, compute) while making no progress toward the user's goal.

**Why it's non-obvious:** Loop failures rarely look like infinite loops in the traditional sense. The agent produces slightly different output each iteration — different phrasing, different tool arguments — which makes simple exact-match deduplication miss them. The LLM interprets its own retry attempts as new information, compounding the error state with each cycle.

**Leading indicators:**
- Tool call frequency spike without corresponding user input
- Token consumption rate increasing faster than conversation turn rate
- Identical tool function names appearing 3+ times in a single agent step
- Agent response latency increasing linearly per turn within a session

**Most susceptible stacks:**
- *Mosaic AI Supervisor:* Subagent retry logic interacts with the supervisor's own retry policy, creating multiplicative loops
- *LangGraph:* Cycles in the graph topology are legal by design, making accidental infinite cycles hard to distinguish from intentional iteration

---

## 2. Context Budget Failures

**Definition:** The context window fills up due to accumulated state — message history, tool results, agent handoff metadata, or system prompts — causing the model to reason over truncated or degraded context without signaling that it is doing so.

**Why it's non-obvious:** Context exhaustion is silent. No exception is thrown. The model does not say "I've lost context." It simply starts producing answers that are plausible but grounded in incomplete information. The quality degradation is gradual, making it hard to pinpoint the turn where things went wrong.

**Leading indicators:**
- Session token count approaching 60% of model context limit
- Model responses becoming shorter or more generic in later turns
- System prompt instructions being violated in later turns (the system prompt was truncated)
- Summarization or history trimming kicking in during mid-conversation (if you have it)

**Most susceptible stacks:**
- *OpenClaw/Ari:* WhatsApp conversations are inherently long-running; users return days later and expect continuity
- *LangGraph:* Stateful agents accumulate tool results in the graph state; large tool outputs (e.g., SQL results, document chunks) blow the budget fast

---

## 3. Serialization & Schema Failures

**Definition:** Data loses fidelity when crossing a boundary — agent-to-agent handoff, checkpoint-to-restart cycle, or state persistence layer. The deserialized object is structurally valid but semantically wrong.

**Why it's non-obvious:** These failures pass type checks. The checkpoint loads without error. The agent starts running. But the state it's working from is corrupted — a field was dropped during schema evolution, a type was coerced silently, or a nested object was flattened. The system behaves as if it has amnesia about specific details, not total memory loss.

**Leading indicators:**
- Agent behaving as if it has partial knowledge of prior turns (not total amnesia)
- Checkpoint restore succeeding but agent making decisions inconsistent with prior session state
- Schema evolution warnings in Delta table logs
- Type coercion warnings in JSON deserialization (often at DEBUG level, invisible in production logging)

**Most susceptible stacks:**
- *LangGraph:* Delta table checkpoint backends with schema evolution enabled mask breakage silently
- *Mosaic AI Supervisor:* State passed between supervisor and subagents is serialized/deserialized at each hop

---

## 4. Supervisor Coordination Failures

**Definition:** Multi-agent orchestration breaks down. The supervisor either cannot decide which subagent to invoke, delegates to the wrong one, enters a delegation cycle, or fails to detect that a subagent has failed.

**Why it's non-obvious:** Supervisor failures look like subagent failures from the outside. The supervisor says "Agent A could not help with this" when in reality Agent A was never called, or was called with the wrong context, or raised an error that the supervisor interpreted as a valid empty response. Debugging requires tracing the full delegation chain, which most observability setups don't capture.

**Leading indicators:**
- Supervisor producing "I don't have enough information" responses despite relevant subagents being available
- Delegation latency increasing (the supervisor is trying multiple subagents before responding)
- Subagent error rates visible in subagent-level monitoring but invisible in supervisor-level monitoring
- Repeated delegation to the same subagent within a single user request

**Most susceptible stacks:**
- *Mosaic AI Supervisor:* The supervisor is itself an LLM, so delegation decisions are non-deterministic and sensitive to prompt phrasing
- *LangGraph:* Conditional edges in the graph can create unintended routing patterns when agent outputs are ambiguous

---

## 5. Memory & Retrieval Failures

**Definition:** Long-term or hybrid memory returns incorrect, stale, or contradictory context that corrupts agent reasoning. The agent trusts retrieved context with the same confidence as fresh user input.

**Why it's non-obvious:** The retrieval system returns results. The results are not empty. They are semantically related to the query. But they are wrong — outdated, from a different user context, or contradicted by more recent information that the retrieval system ranked lower. The LLM has no built-in mechanism to distrust retrieved context.

**Leading indicators:**
- Agent confidently asserting facts that were true in a prior session but have since been updated
- Contradictory statements across turns in the same session (retrieved context conflicts with conversation history)
- Retrieval latency variance (cold path vs. hot path response times diverging)
- User corrections being ignored or overridden by retrieved context in subsequent turns

**Most susceptible stacks:**
- *OpenClaw/Ari:* Two-store architecture (durable facts + daily logs) creates fundamental consistency challenges
- *Any RAG system:* Embedding similarity does not encode recency or correctness

---

## 6. Tool Execution Failures

**Definition:** A tool call partially succeeds, silently drops data, or returns a response that structurally matches the expected schema but contains incorrect or incomplete data. The LLM interprets the malformed result as valid.

**Why it's non-obvious:** The tool did not throw an exception. The response is valid JSON. The schema matches. But the data is wrong — a field is null that shouldn't be, a list is truncated, or the HTTP response was a 200 with an error body. The LLM's tool-use training biases it toward trusting tool responses, so it incorporates the bad data into its reasoning without questioning it.

**Leading indicators:**
- Tool responses containing unexpected null values or empty collections
- Agent conclusions that contradict tool output (the agent "hallucinated past" bad tool data in some turns but not others)
- HTTP 200 responses with error-shaped bodies in tool call logs
- Tool response token count significantly smaller than expected for the query type

**Most susceptible stacks:**
- *Any stack with external API tools:* Third-party APIs fail in creative ways that tool schemas don't anticipate
- *Mosaic AI Supervisor:* Subagent responses are themselves "tool results" to the supervisor — partial subagent failure looks like valid partial data

---

## 7. Observability Failures

**Definition:** Tracing, logging, or attribution gaps make other failure classes undetectable or undebuggable in production. The system is failing, but the monitoring says everything is fine.

**Why it's non-obvious:** You don't know what you can't see. Observability failures are meta-failures — they don't cause incorrect behavior directly, but they prevent you from diagnosing the failures that do. The most dangerous variant is when traces exist but are incomplete: you see the agent step, you see the final response, but the tool calls in between are missing or orphaned, making it look like the agent reasoned correctly without tools.

**Leading indicators:**
- Trace completion rate below 100% (orphaned spans indicate dropped context)
- Agent step durations that don't add up (total step time > sum of child span times, or vice versa)
- Tool call counts in traces not matching tool call counts in application logs
- "Healthy" agent metrics during known-bad user sessions

**Most susceptible stacks:**
- *Databricks Apps with FastAPI:* Async request handling breaks MLflow trace context propagation by default
- *LangGraph:* Custom nodes that don't inherit the tracing context create gaps in the span tree
- *Any multi-service agent:* Distributed tracing across agent boundaries requires explicit context propagation that most quickstarts skip

---

## Cross-Cutting Patterns

Several patterns recur across failure classes:

1. **Silent degradation:** The most dangerous failures don't throw exceptions. They produce plausible-but-wrong output. Detection requires knowing what correct output looks like, which is exactly what you're trying to automate.

2. **Fix amplification:** The first mitigation attempt often makes things worse. Increasing retry limits amplifies loops. Adding caching masks staleness. Enabling schema evolution hides corruption. The "obvious fix" is frequently wrong because it addresses the symptom, not the mechanism.

3. **Boundary failures:** Most failures occur at boundaries — between agents, between the LLM and tools, between memory stores, between sync and async execution contexts. The individual components work correctly in isolation.

4. **Non-deterministic reproduction:** Because LLMs are non-deterministic, these failures don't reproduce on every run. They reproduce under load, with specific conversation histories, or with specific tool response patterns. Fixed seeds help but don't guarantee reproduction.
