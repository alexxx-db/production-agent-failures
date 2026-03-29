"""
AFT-011 Repro: Tool Result Accumulation

Demonstrates how large tool results accumulate in context, consuming budget
disproportionately and degrading the model's ability to attribute results
to their source queries.

Run: python aft011_repro.py
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class QueryResult:
    query: str
    rows: list[dict]
    token_estimate: int


def generate_mock_sql_result(query_num: int, num_rows: int = 25) -> QueryResult:
    """Generate a realistic SQL result table."""
    columns = [
        ("customer_id", lambda i: i + query_num * 100),
        ("name", lambda i: f"Customer_{i}_{query_num}"),
        ("revenue", lambda i: round(1000 + i * 123.45 + query_num * 500, 2)),
        ("region", lambda i: ["NA", "EU", "APAC"][i % 3]),
        ("segment", lambda i: ["enterprise", "mid-market", "smb"][i % 3]),
        ("churn_risk", lambda i: round(0.1 + (i * 0.03) % 0.8, 2)),
    ]
    rows = [{col: fn(i) for col, fn in columns} for i in range(num_rows)]
    raw = json.dumps(rows, indent=2)
    return QueryResult(
        query=f"SELECT * FROM customers WHERE segment_query_{query_num} ...",
        rows=rows,
        token_estimate=len(raw) // 4,
    )


def summarize_result(result: QueryResult, artifact_id: int) -> str:
    """Produce a compact summary instead of dumping raw data."""
    columns = list(result.rows[0].keys()) if result.rows else []
    preview = result.rows[:3]
    return (
        f"Query: {result.query}\n"
        f"Rows returned: {len(result.rows)}\n"
        f"Columns: {', '.join(columns)}\n"
        f"Preview (first 3 rows):\n{json.dumps(preview, indent=2)}\n"
        f"[Full result stored as artifact #{artifact_id}]"
    )


def simulate_raw_accumulation(num_queries: int) -> list[int]:
    """Simulate accumulating raw tool results in context."""
    context_tokens = 500  # Base system prompt + initial user message
    tokens_per_step = []

    for i in range(num_queries):
        result = generate_mock_sql_result(i)
        context_tokens += 50  # User/assistant reasoning per step
        context_tokens += result.token_estimate  # Full raw result
        tokens_per_step.append(context_tokens)

    return tokens_per_step


def simulate_summarized_accumulation(num_queries: int) -> list[int]:
    """Simulate accumulating summarized tool results in context."""
    context_tokens = 500
    tokens_per_step = []

    for i in range(num_queries):
        result = generate_mock_sql_result(i)
        summary = summarize_result(result, artifact_id=i)
        context_tokens += 50
        context_tokens += len(summary) // 4  # Summary tokens (much smaller)
        tokens_per_step.append(context_tokens)

    return tokens_per_step


if __name__ == "__main__":
    NUM_QUERIES = 8

    print("=" * 60)
    print("  AFT-011: Tool Result Accumulation")
    print("=" * 60)

    raw_tokens = simulate_raw_accumulation(NUM_QUERIES)
    summary_tokens = simulate_summarized_accumulation(NUM_QUERIES)

    print(f"\n  {'Query':<8} {'Raw Tokens':<14} {'Summarized':<14} {'Savings':<10}")
    print(f"  {'-' * 44}")
    for i in range(NUM_QUERIES):
        savings = raw_tokens[i] - summary_tokens[i]
        print(f"  {i + 1:<8} {raw_tokens[i]:<14} {summary_tokens[i]:<14} {savings:<10}")

    print(f"\n  After {NUM_QUERIES} queries:")
    print(f"    Raw accumulation:  ~{raw_tokens[-1]} tokens ({raw_tokens[-1] / 128000 * 100:.1f}% of 128k context)")
    print(f"    With summaries:    ~{summary_tokens[-1]} tokens ({summary_tokens[-1] / 128000 * 100:.1f}% of 128k context)")
    print(f"    Token budget saved: ~{raw_tokens[-1] - summary_tokens[-1]} tokens")

    tool_ratio = (raw_tokens[-1] - 500) / raw_tokens[-1]
    print(f"\n  Tool result ratio (raw): {tool_ratio:.1%} of context is tool data")
    print(f"  ALERT: Ratio > 0.4 indicates AFT-011 risk" if tool_ratio > 0.4 else "")
