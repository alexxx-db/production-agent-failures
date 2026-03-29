## [AFT-040] Hybrid Search Staleness

**Class:** Memory & Retrieval
**Severity:** P1
**Stacks Affected:** OpenClaw/Ari, any RAG-based agent with mutable user state
**First Observed:** OpenClaw/Ari WhatsApp assistant where a user updated their dietary preference

---

### What I Expected

User tells the assistant "I'm no longer vegetarian, I eat chicken now." The system updates the user's durable preference store (cold path). On the next query — "What should I have for dinner?" — the assistant recommends options including chicken.

### What Actually Happened

The user updated their preference. The cold-path store (vector DB with durable user facts) was updated: the old record `{"preference": "vegetarian"}` was replaced with `{"preference": "eats chicken"}`. On the next dinner query, the retrieval system searched both the cold path (durable facts) and the hot path (recent conversation log). Both returned results:

- Cold path: `{"preference": "eats chicken"}` (correct, similarity score: 0.82)
- Hot path: `{"preference": "vegetarian", "source": "conversation turn 5"}` (stale, similarity score: 0.89)

The hot-path result had higher semantic similarity because the user had discussed vegetarianism extensively in earlier turns — more context, more embedding overlap with "dinner preference." The LLM received both results and chose the vegetarian preference because it ranked higher in the retrieval results. The user got vegetarian recommendations after explicitly saying they eat chicken.

### Why It Was Non-Obvious

The retrieval system worked correctly — it returned relevant results ranked by similarity. The cold-path update worked correctly — the new preference was stored. The failure is in the interaction: the hot path retains historical context that the cold-path update was supposed to supersede, and semantic similarity doesn't encode "this is outdated."

Similarity score measures how related something is to the query, not how current it is. An extensive past discussion of vegetarianism will always score higher than a brief one-sentence update.

### First (Wrong) Mitigation

Added a recency boost to the retrieval scoring: `final_score = similarity * 0.7 + recency * 0.3`. This helped in some cases but was fragile — the boost coefficient required per-query tuning, and it didn't solve the fundamental problem: the hot path still contained a record that directly contradicted the cold path. For high-similarity stale records (the exact case that matters), the recency boost was insufficient to overcome the similarity gap.

### Root Cause

The cold-path write (update preference) and the hot-path state (historical conversation) are inconsistent. The cold path was updated, but the hot path still contains the old preference as a conversational artifact. The retrieval system treats both stores as sources of truth. There is no mechanism to mark hot-path records as superseded when the cold path is updated.

### Correct Mitigation

Write-through invalidation. When a cold-path fact is updated, a tombstone record is inserted into the hot path that the retrieval system interprets as an override:

```python
def update_user_preference(user_id: str, key: str, new_value: str):
    # 1. Update cold-path store
    cold_store.upsert(user_id, {key: new_value})

    # 2. Insert tombstone in hot path
    hot_store.append({
        "user_id": user_id,
        "type": "tombstone",
        "key": key,
        "supersedes": f"Any prior record where {key} != {new_value}",
        "current_value": new_value,
        "timestamp": now(),
    })

def retrieve(query: str, user_id: str) -> list[dict]:
    cold_results = cold_store.search(query, user_id)
    hot_results = hot_store.search(query, user_id)

    # Apply tombstones: remove hot-path results that are superseded
    tombstones = [r for r in hot_results if r.get("type") == "tombstone"]
    active_hot = [r for r in hot_results if r.get("type") != "tombstone"]

    for tomb in tombstones:
        active_hot = [
            r for r in active_hot
            if not (r.get(tomb["key"]) and r[tomb["key"]] != tomb["current_value"])
        ]

    return rank_and_merge(cold_results, active_hot)
```

The tombstone doesn't delete old records (which may be needed for audit). It marks them as superseded for retrieval purposes.

### Detection Signal

User issuing explicit corrections followed by the agent repeating the corrected behavior within the next 3 turns. Track `correction_followed_by_regression` as a metric. If a user says "no, I changed X to Y" more than once for the same fact, the memory system is returning stale data.

### Repro

See [`repros/aft040_repro.py`](repros/aft040_repro.py). Simulates a two-store memory system where a preference update in the cold path is contradicted by the hot path's historical data.

### References

No standard documentation covers consistency between hot and cold memory stores in agentic systems. The tombstone pattern is borrowed from distributed databases (Cassandra's tombstone model), adapted for vector store retrieval.
