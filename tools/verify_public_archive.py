"""Compare every public event and the collection receipt against a deployed HiJenny API."""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ARCHIVE = Path(__file__).resolve().parents[1] / "data/public/events"


def verify(base_url, archive=ARCHIVE):
    endpoint = base_url.rstrip("/") + "/api/public-events"

    def get(suffix):
        with urlopen(endpoint + suffix, timeout=30) as response:
            return json.load(response)

    expected = {}
    counts = Counter()
    records = defaultdict(set)
    for month in sorted(archive.glob("*.jsonl")):
        for line in month.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row["id"] in expected:
                raise ValueError(f"Repeated archived event {row['id']} in {month.name}")
            expected[row["id"]] = row
            counts[row["source"]] += 1
            records[row["source"]].add(row["record"])
    receipt = json.loads((archive / "collection.json").read_text(encoding="utf-8"))
    if get("/collection")["receipt"] != receipt:
        raise ValueError("Deployed collection receipt differs from this checkout")
    visited = set()
    while True:
        page = get("?" + urlencode({"limit": 100, "offset": len(visited)}))
        summary = {r["source"]: (r["events"], r["records"]) for r in page["source_counts"]}
        if page["total"] != len(expected) or summary != {s: (n, len(records[s])) for s, n in counts.items()}:
            raise ValueError("Deployed total or per-source counts differ from the ledger")
        for row in page["items"]:
            key = row["id"]
            if key in visited or expected.get(key) != row:
                raise ValueError(f"Repeated, unexpected or changed deployed event {key}")
            visited.add(key)
        if len(visited) == len(expected):
            break
        if not page["items"]:
            raise ValueError("Deployed pagination ended before all events were read")
    if get("/collection")["receipt"] != receipt:
        raise ValueError("Collection receipt changed during verification; refresh checkout and rerun")
    return {"events_verified": len(visited), "sources_verified": len(counts),
            "collection_finished_utc": receipt["finished_utc"], "collection_run": receipt.get("run_id")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", help="HiJenny service URL, including any configured proxy prefix")
    print(json.dumps(verify(parser.parse_args().base_url), indent=2))
