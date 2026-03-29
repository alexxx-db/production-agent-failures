# Context Budget — Context Failure Triage

You're here because the agent's output quality is degrading in a way that suggests context window issues. This tree helps identify the specific pattern.

## Decision Tree

```
Agent producing wrong/degraded output, suspected context issue
│
├── WHEN did the quality start degrading?
│   │
│   ├── EARLY in session (< 10 turns)
│   │   │
│   │   ├── Are tool results large (> 1000 tokens each)?
│   │   │   │
│   │   │   └── YES → AFT-011: Tool Result Accumulation
│   │   │       Mechanism: raw tool results filling context with data, not knowledge
│   │   │       Key signal: tool_result_tokens / total_tokens > 0.4
│   │   │       Check: are SQL results, JSON blobs, or document chunks
│   │   │              being injected verbatim into conversation?
│   │   │       Fix: tool result summarization at the tool boundary
│   │   │
│   │   └── NO — tools returning small results, still degrading early
│   │       → Check system prompt size and multi-agent overhead
│   │       If system_prompt + agent_metadata > 30% of context → reduce prompt
│   │       If multi-agent: each hop adds overhead to context
│   │
│   ├── LATE in session (30+ turns)
│   │   │
│   │   ├── Is the system prompt still being followed?
│   │   │   │
│   │   │   ├── NO (agent violating its own instructions)
│   │   │   │   → AFT-010: Multi-turn State Blowup
│   │   │   │   Mechanism: system prompt truncated by context limit
│   │   │   │   Key signal: session_tokens / max_context > 0.8
│   │   │   │   Check: is history trimming enabled? If yes, is it trimming
│   │   │   │          from the front (which drops system prompt)?
│   │   │   │   Fix: proactive summarization at 60% threshold
│   │   │   │
│   │   │   └── YES (system prompt followed, but agent forgetting earlier turns)
│   │   │       │
│   │   │       ├── Is history trimming enabled?
│   │   │       │   │
│   │   │       │   ├── YES → AFT-010 variant: aggressive trimming
│   │   │       │   │   Trimming strategy is dropping important context
│   │   │       │   │   Check: what is being trimmed? User preferences?
│   │   │       │   │   Fix: summarization preserves preferences; trimming doesn't
│   │   │       │   │
│   │   │       │   └── NO → Context is full but not trimmed
│   │   │       │       The API is silently truncating
│   │   │       │       Fix: implement context budget tracking immediately
│   │   │       │
│   │   │       └── Is the agent confusing results across tool calls?
│   │   │           → AFT-011 variant: attribution confusion
│   │   │           Too many similar tool results in context
│   │   │           Model can't attribute which result came from which query
│   │   │           Fix: tool result summarization with clear query labels
│   │   │
│   │   └── GRADUAL degradation (not sudden)
│   │       → Combination of AFT-010 and AFT-011
│   │       Both history and tool results accumulating
│   │       Fix: context budget tracking with both message and tool thresholds
│   │
│   └── INCONSISTENT (sometimes good, sometimes bad at same session length)
│       → Check for variable tool result sizes
│       Some queries return 500 tokens, others return 5000
│       The large ones push context over the edge unpredictably
│       Fix: tool result size caps with summarization for large results
```

## Key Metrics

```
session_token_ratio     = session_tokens / max_context_tokens
tool_result_ratio       = tool_result_tokens / total_context_tokens
system_prompt_present   = boolean (is it in the active context?)
trimming_active         = boolean (is history trimming currently engaged?)
turns_since_summary     = count of turns since last summarization checkpoint
```

## Thresholds

| Metric | Safe | Warning | Critical |
|--------|------|---------|----------|
| `session_token_ratio` | < 0.5 | 0.5 - 0.7 | > 0.7 |
| `tool_result_ratio` | < 0.3 | 0.3 - 0.5 | > 0.5 |
| `system_prompt_present` | true | — | false |
| `turns_since_summary` | < 20 | 20 - 40 | > 40 |

## When It's NOT a Context Issue

- Agent is wrong on the first turn → not context; check system prompt or tool
- Agent is wrong about facts from previous sessions → memory/retrieval (Class 5)
- Agent is wrong but context usage is < 30% → not context; check tool execution or supervisor
- Output is absent, not wrong → likely loop or tool execution failure
