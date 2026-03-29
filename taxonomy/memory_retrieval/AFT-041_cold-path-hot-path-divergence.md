## [AFT-041] Cold-Path / Hot-Path Divergence

**Class:** Memory & Retrieval
**Severity:** P2
**Stacks Affected:** OpenClaw/Ari, any system with tiered memory architecture
**First Observed:** OpenClaw/Ari daily fact extraction pipeline, batch processing delay

---

### What I Expected

The system has two memory tiers:
1. **Hot path:** Append-only daily conversation logs, searchable in real-time
2. **Cold path:** Extracted durable facts, updated by a nightly batch job that processes daily logs

A fact established at 9 AM should be in the hot path immediately and in the cold path by the next morning. Queries during the day use the hot path; queries the next day use both.

### What Actually Happened

The nightly extraction job failed silently for 3 days — the cron job ran, but the extraction model (a smaller LLM used for summarization) was returning empty outputs due to a prompt regression after a model version update. No facts were extracted. No error was raised because the job treated "zero facts extracted" as a valid outcome (some days genuinely have no new facts to extract).

During those 3 days, users interacted normally. New preferences and facts were established in conversations and stored in the hot path. But the hot-path daily logs have a 7-day retention window. On day 8, facts established on day 1 aged out of the hot path — and they were never promoted to the cold path because the extraction job was silently broken.

The agent forgot things users told it a week ago. No error. No trace of the lost data except in the raw conversation logs (which are archived, not indexed for retrieval).

### Why It Was Non-Obvious

The extraction job was running. It completed successfully. It just extracted zero facts — which is a valid output. The monitoring only checked "did the job run?" not "did the job produce results?" A three-day extraction drought was indistinguishable from "users didn't say anything interesting for three days" in the metrics.

The hot-path retention window is a cost optimization (keeping 7 days of daily logs indexed is expensive at scale). It's the right architectural choice. But it creates a cliff: if the cold-path promotion pipeline fails for longer than the retention window, data is permanently lost from the retrieval system.

### First (Wrong) Mitigation

Extended the hot-path retention window from 7 days to 30 days. This reduced the risk window but increased storage costs 4x and slowed retrieval (more data to search). It also didn't fix the root cause — it just moved the cliff further out. If the extraction job fails for 31 days, same problem.

### Root Cause

The hot-path retention window and the cold-path promotion pipeline are coupled but not monitored together. The hot path assumes the cold path will pick up facts within the retention window. The cold path has no SLA enforcement or output validation. The gap between these two assumptions is a data loss window.

### Correct Mitigation

Two changes:

1. **Extraction job output validation:** The nightly job must produce a minimum expected output based on conversation volume. If `conversations_today > 0` and `facts_extracted == 0` for two consecutive days, fire an alert. This is not a hard threshold — it's a sanity check.

2. **Retention-aware promotion SLA:** Track `max_unextracted_age` — the age of the oldest hot-path record that has not been promoted to cold path. Alert when this approaches 50% of the retention window. This gives you 3.5 days of runway to fix the extraction pipeline before data starts aging out.

```python
def check_extraction_health(
    conversations_today: int,
    facts_extracted: int,
    consecutive_zero_days: int,
    oldest_unextracted_age_days: float,
    retention_window_days: int = 7,
) -> list[str]:
    alerts = []
    if conversations_today > 0 and facts_extracted == 0 and consecutive_zero_days >= 2:
        alerts.append(
            f"WARN: Zero facts extracted for {consecutive_zero_days} consecutive days "
            f"despite {conversations_today} conversations today"
        )
    sla_threshold = retention_window_days * 0.5
    if oldest_unextracted_age_days > sla_threshold:
        alerts.append(
            f"CRITICAL: Oldest unextracted record is {oldest_unextracted_age_days:.1f} days old. "
            f"Retention window is {retention_window_days} days. "
            f"Data loss in {retention_window_days - oldest_unextracted_age_days:.1f} days."
        )
    return alerts
```

### Detection Signal

`oldest_unextracted_age_days` approaching `retention_window_days * 0.5`. This is the single metric that captures the data loss risk. If you monitor nothing else in your memory pipeline, monitor this.

### Repro

See [`repros/aft041_repro.py`](repros/aft041_repro.py). Simulates a hot/cold memory pipeline where the extraction job fails silently, demonstrating data loss when records age out of the hot path before promotion.

### References

No public documentation covers this specific failure pattern. The architecture is similar to Lambda architecture (batch + speed layer) data loss scenarios, adapted for agent memory systems.
