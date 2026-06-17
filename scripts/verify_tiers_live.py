import json
import urllib.request

BASE = "https://web-production-b5477.up.railway.app"


def get_tier(tier: str) -> dict:
    with urllib.request.urlopen(f"{BASE}/decision-matrix/tiers/{tier}") as r:
        return json.loads(r.read())


def has_comp(data: dict, component: str) -> bool:
    return any(r["component"] == component for r in data.get("selected_rows", []))


for tier in ("must_do", "should_do", "nice_to_do", "aspirational"):
    d = get_tier(tier)
    print(
        f"{tier}: {d['selected_count']} rows, "
        f"cost {d['cost_low_total']}-{d['cost_high_total']}, "
        f"by_min={d['counts_by_minimum_tier']}"
    )

must = get_tier("must_do")
should = get_tier("should_do")
nice = get_tier("nice_to_do")
asp = get_tier("aspirational")

checks = [
    ("must garage", has_comp(must, "Garage door")),
    ("must smoke", has_comp(must, "Indoor air quality")),
    ("must water", has_comp(must, "Ceiling water damage")),
    ("must popcorn", has_comp(must, "Popcorn ceiling")),
    ("should includes garage", has_comp(should, "Garage door")),
    ("nice countertops", has_comp(nice, "Countertops")),
]
for label, ok in checks:
    print(f"{label}: {'OK' if ok else 'FAIL'}")

for r in must["selected_rows"]:
    assert r["minimum_tier"] == "must_do", r
print("must_do minimum_tier check: OK")
print(f"aspirational total: {asp['selected_count']}")

with urllib.request.urlopen(f"{BASE}/report?id=budget_15k_general") as r:
    rep = json.loads(r.read())
print(f"report_source: {rep.get('report_source')}")
