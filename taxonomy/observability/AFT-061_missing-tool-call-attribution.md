## [AFT-061] Missing Tool Call Attribution

**Class:** Observability
**Severity:** P3
**Stacks Affected:** Databricks Mosaic AI Supervisor, LangGraph
**First Observed:** Post-incident review of a supervisor agent that produced incorrect financial calculations

---

### What I Expected

When reviewing an agent trace after an incident, the trace should show which tool was called, what arguments were passed, what result was returned, and how the agent used that result in its reasoning. This is the minimum information needed to determine whether the agent's error was caused by bad tool data or bad reasoning.

### What Actually Happened

The trace showed the LLM's input (user message + system prompt) and the LLM's output (final response). The tool calls were logged as events within the LLM span, but the event payload contained only the tool name and a truncated argument string (cut at 256 characters). The tool result was not captured at all.

During the post-incident review, the team could see that the agent called `calculate_revenue` but could not see what arguments were passed (the customer ID list was truncated) or what the tool returned. They could not determine whether the tool returned wrong data or the agent misinterpreted correct data. The root cause investigation took 3 days instead of 3 hours.

```
Trace span: llm_call (duration: 2.3s)
  Events:
    - tool_call: calculate_revenue(customer_ids=[1, 2, 3, ...])  ← truncated at 256 chars
    - tool_result: <not captured>
  Input: [user message, system prompt]
  Output: "Total revenue for Q3 is $2.4M"   ← wrong, should be $3.1M
```

### Why It Was Non-Obvious

The trace existed. It had spans. It had events. It looked "complete" in the MLflow UI. The missing data was invisible — you had to know what should be there to notice what wasn't. The truncation of tool arguments was a default configuration in the tracing library, not a conscious choice. The tool result omission was a gap in the instrumentation, not an error.

Most teams don't audit their trace completeness until they need the data for an incident. By then, the data is gone (or was never captured).

### First (Wrong) Mitigation

Increased the event payload size limit from 256 to 4096 characters. This captured more of the tool arguments but still truncated large inputs (e.g., a customer ID list with 500 entries). It also did nothing for the missing tool results — that required separate instrumentation.

### Root Cause

The tracing instrumentation captured the LLM interaction boundary (input → output) but not the tool interaction boundary (call → result). Tool calls were logged as events on the LLM span (metadata), not as separate spans with their own input/output capture. This is a common pattern in quick-start tracing setups: the LLM call is the "unit of work" being traced, and tool calls are treated as metadata within that unit rather than as first-class operations.

### Correct Mitigation

Tool calls must be first-class spans, not events. Each tool call gets its own span with:
- `input`: full tool arguments (not truncated)
- `output`: full tool result
- `attributes`: tool name, call ID, execution duration
- Parent: the agent step span, not the LLM call span

```python
def instrumented_tool_call(tool_name: str, args: dict, parent_span) -> Any:
    with mlflow.start_span(
        name=f"tool:{tool_name}",
        parent_id=parent_span.span_id,
        attributes={"tool.name": tool_name, "tool.call_id": uuid4().hex},
    ) as span:
        span.set_inputs(args)  # Full args, no truncation
        try:
            result = execute_tool(tool_name, args)
            span.set_outputs(result)
            span.set_status("OK")
            return result
        except Exception as e:
            span.set_status("ERROR")
            span.set_attributes({"error.message": str(e)})
            raise
```

For the argument truncation specifically: configure the tracing backend to accept payloads up to at least 64KB for tool spans. If tool results are truly large (e.g., full SQL result sets), capture a summary in the span output and store the full result as an artifact linked by reference ID.

### Detection Signal

During a test run of your agent, compare: (1) the tool calls in application logs, (2) the tool call spans in the trace. If the counts don't match, or if any tool span is missing `output`, your instrumentation has gaps. This is a one-time audit, not a runtime metric — but run it after every instrumentation change.

### Repro

See [`repros/aft061_repro.py`](repros/aft061_repro.py). Compares event-based tool call logging (metadata on LLM span) vs. span-based tool call logging (first-class spans with full input/output).

### References

MLflow 3.x documentation on custom spans. The distinction between events and spans is standard in OpenTelemetry, which MLflow's tracing is built on. OpenTelemetry semantic conventions for LLM spans are still in development and do not yet standardize tool call span structure.
