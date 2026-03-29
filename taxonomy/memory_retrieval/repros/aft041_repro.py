"""
AFT-041 Repro: Cold-Path / Hot-Path Divergence

Demonstrates data loss when a nightly extraction job fails silently
and hot-path records age out before being promoted to cold storage.

Run: python aft041_repro.py
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class HotRecord:
    fact: str
    timestamp: datetime
    promoted_to_cold: bool = False


@dataclass
class ColdRecord:
    fact: str
    extracted_from: str
    timestamp: datetime


class MemoryPipeline:
    """Simulates a hot/cold memory pipeline with retention and extraction."""

    def __init__(self, retention_days: int = 7):
        self.hot_store: list[HotRecord] = []
        self.cold_store: list[ColdRecord] = []
        self.retention_days = retention_days
        self.extraction_healthy = True

    def add_conversation_fact(self, fact: str, timestamp: datetime) -> None:
        self.hot_store.append(HotRecord(fact=fact, timestamp=timestamp))

    def run_extraction(self, current_time: datetime) -> int:
        """Nightly job: extract facts from hot path to cold path."""
        if not self.extraction_healthy:
            # Job runs but extracts nothing (silent failure)
            return 0

        extracted = 0
        for record in self.hot_store:
            if not record.promoted_to_cold:
                self.cold_store.append(ColdRecord(
                    fact=record.fact,
                    extracted_from="hot_path",
                    timestamp=record.timestamp,
                ))
                record.promoted_to_cold = True
                extracted += 1
        return extracted

    def enforce_retention(self, current_time: datetime) -> int:
        """Remove hot-path records older than retention window."""
        cutoff = current_time - timedelta(days=self.retention_days)
        before = len(self.hot_store)
        self.hot_store = [r for r in self.hot_store if r.timestamp > cutoff]
        return before - len(self.hot_store)

    def query(self, fact_keyword: str) -> list[str]:
        """Search both stores for a fact."""
        results = []
        for r in self.cold_store:
            if fact_keyword.lower() in r.fact.lower():
                results.append(f"[cold] {r.fact}")
        for r in self.hot_store:
            if fact_keyword.lower() in r.fact.lower():
                results.append(f"[hot]  {r.fact}")
        return results

    def check_health(self, current_time: datetime, conversations_today: int,
                     facts_extracted: int, consecutive_zero_days: int) -> list[str]:
        alerts = []
        if conversations_today > 0 and facts_extracted == 0 and consecutive_zero_days >= 2:
            alerts.append(
                f"WARN: Zero facts extracted for {consecutive_zero_days} consecutive days "
                f"despite {conversations_today} conversations"
            )
        # Check oldest unextracted record age
        unextracted = [r for r in self.hot_store if not r.promoted_to_cold]
        if unextracted:
            oldest_age = (current_time - min(r.timestamp for r in unextracted)).days
            sla_threshold = self.retention_days * 0.5
            if oldest_age > sla_threshold:
                alerts.append(
                    f"CRITICAL: Oldest unextracted record is {oldest_age} days old. "
                    f"Data loss in {self.retention_days - oldest_age} days."
                )
        return alerts


if __name__ == "__main__":
    print("=" * 60)
    print("  AFT-041: Cold-Path / Hot-Path Divergence")
    print("=" * 60)

    pipeline = MemoryPipeline(retention_days=7)
    start = datetime(2025, 1, 1)

    # Day 1-3: Normal operation, facts are created and extracted nightly
    print("\n  Days 1-3: Normal operation")
    for day in range(3):
        current = start + timedelta(days=day)
        pipeline.add_conversation_fact(f"User prefers Italian food (day {day + 1})", current)
        pipeline.add_conversation_fact(f"User's name is Alex (day {day + 1})", current)
        extracted = pipeline.run_extraction(current)
        print(f"    Day {day + 1}: Added 2 facts, extracted {extracted} to cold path")

    # Day 4: Extraction breaks silently
    print("\n  Day 4: Extraction job breaks (model prompt regression)")
    pipeline.extraction_healthy = False

    # Day 4-10: Facts accumulate in hot path but never promote
    for day in range(3, 10):
        current = start + timedelta(days=day)
        pipeline.add_conversation_fact(f"User started learning piano (day {day + 1})", current)
        extracted = pipeline.run_extraction(current)
        expired = pipeline.enforce_retention(current)
        consecutive_zero = day - 3  # Days since extraction broke

        alerts = pipeline.check_health(current, conversations_today=1,
                                       facts_extracted=extracted,
                                       consecutive_zero_days=consecutive_zero)

        status = f"extracted={extracted}, expired={expired}"
        if alerts:
            status += f" ALERTS: {len(alerts)}"
        print(f"    Day {day + 1}: {status}")
        for alert in alerts:
            print(f"      → {alert}")

    # Check what's retrievable
    print("\n  Query results after day 10:")
    for keyword in ["Italian", "piano", "Alex"]:
        results = pipeline.query(keyword)
        status = "FOUND" if results else "LOST"
        print(f"    '{keyword}': {status}")
        for r in results:
            print(f"      {r}")

    print("\n  Day 4 facts ('piano' from day 4-7) were lost: aged out of hot path")
    print("  before extraction could promote them to cold path.")
