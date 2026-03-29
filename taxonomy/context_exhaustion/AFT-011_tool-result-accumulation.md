## [AFT-011] Tool Result Accumulation

**Class:** Context Budget
**Severity:** P2
**Stacks Affected:** LangGraph, Databricks Mosaic AI Supervisor
**First Observed:** Agent workflows performing multi-step data analysis with SQL tools

---

### What I Expected

An agent running a sequence of SQL queries should accumulate knowledge across queries, using each result to inform the next query. The tool results should be available for the model to reference.

### What Actually Happened

The agent executed 5 SQL queries in sequence. Each query returned ~2,000 tokens of tabular results. By the 5th query, the conversation history contained 10,000+ tokens of raw SQL output, plus the queries themselves, plus the model's reasoning about each result. Total context usage was 18,000 tokens — more than half the budget — consumed by tool results alone, with only 5 turns elapsed.

The model's 6th response began hallucinating column names that existed in the 2nd query's result but not the 5th, indicating it was confusing results across queries. The raw table data was still in context, but the model was unable to correctly attribute which result came from which query.

### Why It Was Non-Obvious

The tool results were all present in context — nothing was truncated yet. The failure was not context exhaustion but context pollution. Large tool results dilute the model's attention. The model performs worse at attributing information to its source when the context contains multiple large, structurally similar blocks (e.g., multiple SQL result tables with overlapping column names).

### First (Wrong) Mitigation

Set a `max_tool_result_tokens` limit of 500 tokens, truncating tool results that exceeded it. This prevented context blowup but also destroyed the data the agent needed to reason correctly. A 500-token truncation of a SQL result table cuts rows arbitrarily, which is worse than useless — the model reasons over a partial table as if it were complete.

### Root Cause

Tool results are the highest-entropy content in an agent's context window. Unlike conversation turns (which are semantically compressed by the model), tool results are raw data: tables, JSON blobs, error traces. They consume disproportionate context budget relative to the reasoning they enable. Without a mechanism to summarize or scope tool results before injecting them into the conversation, context fills with data instead of knowledge.

### Correct Mitigation

Tool result summarization at the tool boundary, not in the conversation. The tool itself — or a wrapper around it — produces both a full result (stored externally for reference) and a summary (injected into the conversation). The summary is structured: row count, column schema, key aggregates, and the first/last 3 rows as examples.

```python
def summarize_sql_result(result: list[dict], query: str) -> str:
    row_count = len(result)
    columns = list(result[0].keys()) if result else []
    preview_rows = result[:3]
    return (
        f"Query: {query}\n"
        f"Rows returned: {row_count}\n"
        f"Columns: {', '.join(columns)}\n"
        f"Preview (first 3 rows):\n{json.dumps(preview_rows, indent=2)}\n"
        f"[Full result stored as artifact #{artifact_id}]"
    )
```

If the model needs to reference the full result later, it requests it by artifact ID — a retrieval step, not a context dump.

### Detection Signal

`tool_result_tokens / total_context_tokens` ratio exceeding 0.4 for any session. Tool results should be the minority of context, not the majority. If your tool results are more than 40% of context by token count, you are on the path to this failure.

### Repro

See [`repros/aft011_repro.py`](repros/aft011_repro.py). Simulates sequential tool calls with large results, showing context token growth and cross-query result confusion.

### References

LangGraph documentation recommends using `return_direct=False` for tools, which passes results through the graph state. The documentation does not address the context budget implications of large tool results accumulated across multiple steps.
