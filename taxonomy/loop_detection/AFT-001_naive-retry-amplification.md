## [AFT-001] Naive Retry Amplification

**Class:** Loop & Recursion
**Severity:** P1
**Stacks Affected:** Databricks Mosaic AI Supervisor, LangGraph
**First Observed:** During load testing of a Mosaic AI Supervisor deployment with external API tool calls

---

### What I Expected

When a subagent's tool call times out, the retry logic should re-execute the tool call and return the result to the LLM. The agent should continue reasoning from the successful retry.

### What Actually Happened

Each retry attempt was appended to the conversation history as a separate tool result. The LLM received the original timeout error, the first retry's timeout error, and then the successful result — all as distinct tool responses. By the third retry, the model was reasoning over three "results" from what it believed were three separate calls. It began synthesizing across all three, treating errors as partial data. Token consumption increased 4x per retry. In a 3-subagent supervisor topology, this cascaded: the supervisor retried the subagent, which retried its tool, producing a multiplicative blowup of `supervisor_retries × subagent_retries × tool_retries`.

Log signature:
```
WARN  ToolExecutor: Retry attempt 3/5 for tool=search_customer_db
DEBUG LLMCall: input_tokens=12847 (previous call: 3201)
ERROR SupervisorAgent: Response exceeded token budget after subagent retry cascade
```

### Why It Was Non-Obvious

Retry logic is the correct pattern for transient failures. The problem is not that retries happened — it's that the retry implementation appended to the conversation history instead of replacing the failed attempt. In a traditional API client, retries are transparent to the caller. In an LLM agent, retries are visible state that the model reasons over.

### First (Wrong) Mitigation

Increased the retry limit from 3 to 5 and the timeout from 10s to 30s. Reasoning: "the tool is slow, give it more time." This made the problem catastrophically worse — more retries meant more phantom tool results in the conversation, and the longer timeout meant each retry consumed more wall-clock time before failing.

### Root Cause

The tool executor treated each retry as an independent tool invocation, appending its result (success or failure) to the LLM's message history. The LLM has no concept of "this replaces the previous tool result" — every message in the history is treated as ground truth. Retries created phantom tool results that the model incorporated into its reasoning.

### Correct Mitigation

Idempotency key pattern: each tool call gets a unique `call_id`. Retries reuse the same `call_id`. The message history manager replaces (not appends) tool results with matching `call_id` values. Only the final result — success or last failure — is visible to the LLM.

```python
@tool(retry_strategy="replace")
def search_customer_db(query: str, _call_id: str = None) -> ToolResult:
    call_id = _call_id or uuid4().hex
    try:
        result = db.search(query, timeout=10)
        return ToolResult(call_id=call_id, status="success", data=result)
    except TimeoutError:
        return ToolResult(call_id=call_id, status="retry", data=None)
```

The message history manager:
```python
def append_tool_result(self, result: ToolResult):
    # Replace, don't append, for matching call_ids
    for i, msg in enumerate(self.messages):
        if msg.get("call_id") == result.call_id:
            self.messages[i] = result.to_message()
            return
    self.messages.append(result.to_message())
```

### Detection Signal

`input_tokens` for sequential LLM calls within the same agent step increasing by more than 50% per call. This is the earliest signal — it fires before the retry cascade reaches the supervisor level.

### Repro

See [`repros/aft001_repro.py`](repros/aft001_repro.py). Simulates a tool that fails 2 out of 3 times, with a naive retry-and-append executor. Observe token count growth per retry.

### References

No public documentation covers this specific interaction between retry logic and LLM message history. LangGraph's `ToolNode` retry logic was refactored in v0.2.x to address a related issue, but the fix is incomplete for multi-agent topologies.
