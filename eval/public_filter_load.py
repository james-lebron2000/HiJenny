"""Replay four public filter requests through an existing HiJenny endpoint."""
import argparse
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.request import urlopen

CASES = {
    "company": "?asset=Summit&limit=3",
    "regulator": "?source=NMPA&limit=20",
    "clinical": "/asset-evidence?asset=osimertinib",
    "historical": "?source=SEC&as_of=2026-11-27&limit=20",
}


def matches(case, data):
    if case == "clinical":
        return any(row["record"] == "NCT02296125" for row in data["trials"])
    rows = data["items"]
    if case == "company":
        return bool(rows) and all("summit" in row["subject"].lower() for row in rows)
    if case == "historical":
        return bool(rows) and all(row["source"] == "SEC" and row["source_date"] <= "2026-11-27" for row in rows)
    return bool(rows) and all(row["source"] == "NMPA" for row in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    args = parser.parse_args()
    started = datetime.now(timezone.utc).isoformat()
    gate = threading.Barrier(20)
    cases = list(CASES)

    def user(user_id):
        samples = []
        gate.wait()
        for turn in range(12):
            case = cases[(user_id + turn) % len(cases)]
            url = args.base_url.rstrip("/") + "/api/public-events" + CASES[case]
            begin = time.perf_counter()
            error = None
            try:
                with urlopen(url, timeout=15) as response:
                    body = response.read()
                elapsed = time.perf_counter() - begin
                if not matches(case, json.loads(body)):
                    error = "unexpected result"
            except Exception as exc:
                elapsed = time.perf_counter() - begin
                error = type(exc).__name__ + ": " + str(exc)
            samples.append((case, elapsed, error))
        return samples

    with ThreadPoolExecutor(max_workers=20) as pool:
        samples = [sample for group in pool.map(user, range(20)) for sample in group]
    result = {}
    passed = True
    for name in [*cases, "all"]:
        group = [row for row in samples if name == "all" or row[0] == name]
        times = sorted(row[1] for row in group)
        errors = [row[2] for row in group if row[2]]
        p95 = times[math.ceil(0.95 * len(times)) - 1]
        result[name] = dict(requests=len(group), errors=len(errors), p95_ms=round(p95 * 1000, 1),
                            max_ms=round(max(times) * 1000, 1), error_examples=sorted(set(errors))[:3])
        passed = passed and not errors and p95 <= 3
    print(json.dumps(dict(started_utc=started, concurrent_users=20, results=result, passed=passed), indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
