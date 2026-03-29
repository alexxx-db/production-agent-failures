"""
AFT-040 Repro: Hybrid Search Staleness

Demonstrates how a two-store memory architecture returns stale preferences
when the hot path retains historical data that contradicts a cold path update.

Run: python aft040_repro.py
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class MemoryRecord:
    key: str
    value: str
    source: str  # "cold" or "hot"
    timestamp: datetime
    similarity_score: float = 0.0
    record_type: str = "fact"  # "fact" or "tombstone"
    superseded_value: str | None = None


class VectorStore:
    """Simulated vector store with similarity scoring."""

    def __init__(self, name: str):
        self.name = name
        self.records: list[MemoryRecord] = []

    def upsert(self, key: str, value: str, **kwargs) -> None:
        # Remove existing record with same key (for cold-path updates)
        self.records = [r for r in self.records if r.key != key or r.record_type == "tombstone"]
        self.records.append(MemoryRecord(
            key=key, value=value, source=self.name,
            timestamp=kwargs.get("timestamp", datetime.now()),
            record_type=kwargs.get("record_type", "fact"),
            superseded_value=kwargs.get("superseded_value"),
        ))

    def append(self, record: MemoryRecord) -> None:
        self.records.append(record)

    def search(self, query: str) -> list[MemoryRecord]:
        results = []
        for record in self.records:
            # Simulate similarity: longer value = more context = higher similarity
            base_sim = 0.7 + min(len(record.value) / 500, 0.25)
            if any(word in record.value.lower() for word in query.lower().split()):
                base_sim += 0.05
            record.similarity_score = round(base_sim, 3)
            results.append(record)
        return sorted(results, key=lambda r: r.similarity_score, reverse=True)


def retrieve_naive(cold: VectorStore, hot: VectorStore, query: str) -> list[MemoryRecord]:
    """Naive retrieval: merge and rank by similarity only."""
    all_results = cold.search(query) + hot.search(query)
    return sorted(all_results, key=lambda r: r.similarity_score, reverse=True)


def retrieve_with_tombstones(cold: VectorStore, hot: VectorStore, query: str) -> list[MemoryRecord]:
    """Correct retrieval: apply tombstones from hot path to filter stale records."""
    cold_results = cold.search(query)
    hot_results = hot.search(query)

    tombstones = [r for r in hot_results if r.record_type == "tombstone"]
    active_hot = [r for r in hot_results if r.record_type != "tombstone"]

    # Filter out hot-path facts that are superseded by tombstones
    for tomb in tombstones:
        active_hot = [
            r for r in active_hot
            if not (r.key == tomb.key and r.value != tomb.value)
        ]

    all_results = cold_results + active_hot
    return sorted(all_results, key=lambda r: r.similarity_score, reverse=True)


if __name__ == "__main__":
    print("=" * 60)
    print("  AFT-040: Hybrid Search Staleness")
    print("=" * 60)

    now = datetime.now()

    # Setup: cold path has updated preference
    cold = VectorStore("cold")
    cold.upsert("diet", "eats chicken", timestamp=now)

    # Hot path has extensive historical discussion about vegetarianism
    hot = VectorStore("hot")
    hot.append(MemoryRecord(
        key="diet", value=(
            "User discussed vegetarian preferences extensively. Mentioned they are "
            "vegetarian for ethical reasons. Prefers plant-based restaurants. Has been "
            "vegetarian for 5 years. Dislikes mock meat. Favorite vegetarian restaurant "
            "is Green Garden on 5th street."
        ),
        source="hot", timestamp=now - timedelta(days=2), record_type="fact",
    ))

    # Naive retrieval
    print("\n  Naive retrieval (similarity-ranked):")
    naive_results = retrieve_naive(cold, hot, "dinner preference")
    for r in naive_results[:3]:
        label = "STALE" if "vegetarian" in r.value.lower() and "eats chicken" not in r.value.lower() else "CURRENT"
        print(f"    [{r.source}] sim={r.similarity_score:.3f} [{label}]: {r.value[:80]}...")
    print(f"    → LLM will pick the highest-similarity result: VEGETARIAN (wrong)")

    # Now add tombstone and use tombstone-aware retrieval
    hot.append(MemoryRecord(
        key="diet",
        value="eats chicken",
        source="hot",
        timestamp=now,
        record_type="tombstone",
        superseded_value="vegetarian",
    ))

    print("\n  Tombstone-aware retrieval:")
    smart_results = retrieve_with_tombstones(cold, hot, "dinner preference")
    for r in smart_results[:3]:
        label = "CURRENT" if "chicken" in r.value.lower() else "FILTERED"
        print(f"    [{r.source}] sim={r.similarity_score:.3f} [{label}]: {r.value[:80]}...")
    print(f"    → LLM sees only current data: EATS CHICKEN (correct)")
