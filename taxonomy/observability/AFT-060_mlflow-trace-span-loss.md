## [AFT-060] MLflow Trace Span Loss

**Class:** Observability
**Severity:** P2
**Stacks Affected:** Databricks Apps (FastAPI), MLflow 3.x
**First Observed:** Agent monitoring setup on Databricks Apps with async FastAPI endpoints

---

### What I Expected

MLflow tracing captures the full execution tree: the agent step, each tool call within the step, and any nested LLM calls. The trace should show a parent span for the agent request, child spans for tool calls, and grandchild spans for any sub-operations within tools.

### What Actually Happened

The parent span (agent request) appeared in MLflow. The tool call spans were created but orphaned — they existed in the MLflow backend as standalone traces, not as children of the agent request. The resulting trace showed an agent that received a user message and produced a response with zero tool calls in between.

On the dashboard: agent latency looked healthy (200ms), tool calls showed as separate traces (150ms each), but there was no way to correlate which tools were called within which agent request. During an incident, the trace showed a "healthy" agent producing a wrong answer with no intermediate steps visible — as if the agent was hallucinating directly rather than reasoning from tool results.

```
Expected trace:
  agent_request (500ms)
    ├── tool_call: search_db (150ms)
    ├── tool_call: format_result (50ms)
    └── llm_call: generate_response (200ms)

Actual traces (three separate, uncorrelated):
  agent_request (500ms)        ← looks like pure hallucination
  search_db (150ms)            ← orphaned, no parent
  format_result (50ms)         ← orphaned, no parent
```

### Why It Was Non-Obvious

The spans were not missing — they existed. They were just disconnected. MLflow's trace UI showed the agent request trace as "complete" (no error, normal duration). The orphaned tool spans appeared separately in the trace list but were impossible to correlate visually with the agent request. If you searched for traces by request ID, you'd find the parent. The children had different trace IDs.

The root cause is async context propagation. In a synchronous execution flow, MLflow's trace context (the parent span ID) propagates via Python's contextvars. In an async FastAPI handler, tool calls launched with `asyncio.create_task()` or executed in a thread pool do not inherit the caller's contextvars. Each async task starts with a fresh context and creates its own root span.

### First (Wrong) Mitigation

Added `@mlflow.trace` decorators to all async tool functions. This created more spans, not fewer orphaned spans. Each decorated function now created its own root trace instead of attaching to the parent, because the decorator reads from the current context — which is empty in a new async task.

### Root Cause

Python's `contextvars.copy_context()` is not automatically called when creating async tasks. `asyncio.create_task()` and `loop.run_in_executor()` do not propagate the parent's context by default. MLflow's trace context is stored in a contextvar, so it's lost at async boundaries. The span is created with no parent, becoming a root span of a new trace.

### Correct Mitigation

Explicit context propagation at async boundaries. Capture the current span before spawning async work, and manually set the parent in the child context.

```python
import mlflow
from contextvars import copy_context

async def agent_handler(request):
    with mlflow.start_span(name="agent_request") as parent_span:
        # Option 1: copy_context for create_task
        ctx = copy_context()
        task = asyncio.create_task(ctx.run(tool_call, request.query))
        result = await task

        # Option 2: explicit parent_id for thread pool work
        parent_id = parent_span.span_id
        result = await loop.run_in_executor(
            executor,
            lambda: run_tool_with_parent(parent_id, request.query)
        )

def run_tool_with_parent(parent_span_id: str, query: str):
    with mlflow.start_span(name="tool_call", parent_id=parent_span_id):
        return execute_tool(query)
```

For FastAPI specifically, middleware that copies the trace context into the request state makes this less error-prone:

```python
@app.middleware("http")
async def trace_context_middleware(request, call_next):
    request.state.trace_context = copy_context()
    response = await call_next(request)
    return response
```

### Detection Signal

`trace_completion_rate` below 100%: count traces with zero child spans that should have children (agent steps with tool calls in application logs but no tool spans in the trace). Also: orphaned span count — traces with `parent_id=None` that correspond to tool calls, not top-level requests.

### Repro

See [`repros/aft060_repro.py`](repros/aft060_repro.py). Simulates async context loss by spawning tool calls in separate async tasks, showing orphaned spans.

### References

MLflow documentation on tracing does not explicitly cover async context propagation for Python asyncio. The Python `contextvars` documentation describes the propagation behavior, but the connection to MLflow tracing is not documented. This is a known gap in the MLflow 3.x async story.
