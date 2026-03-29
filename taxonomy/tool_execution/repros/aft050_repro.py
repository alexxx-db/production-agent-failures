"""
AFT-050 Repro: Partial Tool Success

Demonstrates how a batch API returning partial results on timeout causes
an agent to present incomplete data as complete.

Run: python aft050_repro.py
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class Account:
    id: int
    name: str
    region: str
    revenue: float


class MockBatchAPI:
    """Simulates a batch API that returns partial results on timeout."""

    def __init__(self, timeout_seconds: float = 0.05):
        self.timeout = timeout_seconds
        self.data: dict[str, list[Account]] = {
            "NA": [Account(i, f"NA_Customer_{i}", "NA", 1000 + i * 100) for i in range(400)],
            "EU": [Account(i + 400, f"EU_Customer_{i}", "EU", 800 + i * 80) for i in range(447)],
            "APAC": [Account(i + 847, f"APAC_Customer_{i}", "APAC", 600 + i * 60) for i in range(312)],
        }

    def search(self, regions: list[str]) -> dict:
        accounts = []
        for region in regions:
            # Simulate: APAC takes too long, times out
            if region == "APAC":
                time.sleep(self.timeout + 0.01)  # Exceeds timeout
                continue  # API returns what it has so far
            accounts.extend(self.data.get(region, []))
        return {
            "accounts": [{"id": a.id, "name": a.name, "region": a.region, "revenue": a.revenue}
                         for a in accounts],
            "total": len(accounts),
        }


def naive_tool_wrapper(api: MockBatchAPI, regions: list[str]) -> dict:
    """WRONG: passes API response through without completeness check."""
    return api.search(regions)


def reconciling_tool_wrapper(api: MockBatchAPI, regions: list[str]) -> dict:
    """CORRECT: validates response completeness against request."""
    raw = api.search(regions)

    returned_regions = set(a["region"] for a in raw["accounts"])
    requested_regions = set(regions)
    missing = requested_regions - returned_regions

    result = {
        **raw,
        "completeness": "complete" if not missing else "partial",
        "requested_regions": list(requested_regions),
        "returned_regions": list(returned_regions),
        "missing_regions": list(missing) if missing else None,
    }
    if missing:
        result["warning"] = (
            f"PARTIAL RESULTS. Missing data for: {missing}. "
            f"Do not present as complete."
        )
    return result


if __name__ == "__main__":
    print("=" * 60)
    print("  AFT-050: Partial Tool Success")
    print("=" * 60)

    api = MockBatchAPI()
    regions = ["NA", "EU", "APAC"]

    true_total = sum(len(api.data[r]) for r in regions)
    print(f"\n  True total across all regions: {true_total} accounts")

    # Naive wrapper
    print("\n  Naive tool wrapper (no completeness check):")
    naive_result = naive_tool_wrapper(api, regions)
    print(f"    Returned: {naive_result['total']} accounts")
    print(f"    Missing:  {true_total - naive_result['total']} accounts (not detected)")
    print(f"    Agent would say: 'Your global customer base is {naive_result['total']} accounts'")
    print(f"    WRONG: Missing {true_total - naive_result['total']} APAC accounts")

    # Reconciling wrapper
    print("\n  Reconciling tool wrapper (with completeness check):")
    smart_result = reconciling_tool_wrapper(api, regions)
    print(f"    Returned: {smart_result['total']} accounts")
    print(f"    Completeness: {smart_result['completeness']}")
    print(f"    Missing regions: {smart_result.get('missing_regions')}")
    if smart_result.get("warning"):
        print(f"    Warning: {smart_result['warning']}")
    print(f"    Agent would say: 'I found {smart_result['total']} accounts but APAC data is missing'")
