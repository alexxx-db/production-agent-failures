## [AFT-031] Subagent Silent Failure

**Class:** Supervisor Coordination
**Severity:** P1
**Stacks Affected:** Databricks Mosaic AI Supervisor, LangGraph
**First Observed:** Production supervisor where a subagent's external API dependency went down

---

### What I Expected

When a subagent fails — exception, timeout, or tool error — the supervisor should detect the failure and either retry with a different subagent, return a graceful error to the user, or attempt the task itself.

### What Actually Happened

The subagent (a customer lookup agent) called an external CRM API that returned a 500 error. The subagent's error handling caught the exception and returned an empty dict `{}` as its response to the supervisor. The supervisor received `{}`, interpreted it as "the customer lookup returned no results," and continued reasoning: "I was unable to find any customer records matching your query. This could mean the customer doesn't exist in our system."

The customer did exist. The API was down. The supervisor confidently told the user their customer record didn't exist. No error was logged at the supervisor level. The subagent's error log existed but was in a different log stream that the on-call engineer didn't check for 4 hours.

```
Subagent log (not monitored):
  ERROR CRMTool: HTTPError 500 from api.crm.com/customers?q=acme
  WARN  CustomerAgent: Caught exception, returning empty result

Supervisor log (monitored):
  INFO  Supervisor: Received result from customer_agent: {}
  INFO  Supervisor: Generating response — no customer records found
  # ^^^ This looks completely healthy
```

### Why It Was Non-Obvious

The subagent did not return `None`. It returned `{}` — a valid, non-null response. The supervisor had no way to distinguish "the lookup returned zero results" from "the lookup failed and the error was swallowed." The empty dict is a semantically valid response in the success case (no matching customers). The error and the success case produce identical supervisor-visible output.

The subagent developer did the "right thing" by catching the exception (don't let infrastructure errors crash the agent). But by returning an empty result instead of an error signal, they made the failure invisible to the supervisor.

### First (Wrong) Mitigation

Added a null check in the supervisor: `if result is None: treat as error`. Did nothing — the result was never `None`. It was `{}`. Then added an emptiness check: `if not result: treat as error`. This broke the legitimate "no results found" path — now every empty lookup result was treated as an error, generating false positives on 15% of queries.

### Root Cause

The subagent-to-supervisor communication protocol has no distinction between "success with empty data" and "failure." Both produce the same response shape. Without a structured response envelope that explicitly signals success/failure, the supervisor must guess — and guessing wrong in either direction is bad.

### Correct Mitigation

Structured tool response envelope with an explicit status field. Every subagent response wraps its data in an envelope that the supervisor is prompt-engineered to interpret.

```python
@dataclass
class AgentResponse:
    status: str  # "success" | "error" | "partial"
    data: Any = None
    error_message: str | None = None
    error_code: str | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "data": self.data,
            "error_message": self.error_message,
            "error_code": self.error_code,
        }

# Subagent returns:
# On success: AgentResponse(status="success", data={"customers": [...]})
# On empty:   AgentResponse(status="success", data={"customers": []})
# On failure: AgentResponse(status="error", error_message="CRM API returned 500",
#                           error_code="UPSTREAM_API_FAILURE")
```

The supervisor system prompt includes: "When a subagent returns `status: error`, do NOT treat the data field as valid. Instead, inform the user that the lookup could not be completed and suggest they try again. Never infer 'no results' from an error response."

### Detection Signal

Subagent error rates diverging from supervisor-reported error rates. If subagents are logging errors but the supervisor isn't, responses are being swallowed. Monitor the ratio: `supervisor_error_responses / subagent_error_logs` should be close to 1.0.

### Repro

See [`repros/aft031_repro.py`](repros/aft031_repro.py). Simulates a supervisor receiving empty responses from a failing subagent, showing how the supervisor misinterprets the failure as a valid empty result.

### References

No public documentation covers structured error propagation between agents in Mosaic AI Supervisor or LangGraph. The pattern is borrowed from gRPC status codes and HTTP problem details (RFC 7807), adapted for LLM-mediated communication.
