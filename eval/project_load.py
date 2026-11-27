"""Run the 10-project synthetic 1,600-page benchmark in a fresh temporary database."""
from contextlib import redirect_stdout
import hashlib
import json
import math
import os
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def benchmark():
    import fitz
    from fastapi.testclient import TestClient
    from hijenny.api.main import app

    source = Path(__file__).resolve().parents[1] / "data/sample_package/Clinical_Study_Report_v1.0.pdf"
    with fitz.open(source) as original, fitz.open() as package:
        for _ in range(8):
            package.insert_pdf(original, from_page=0, to_page=199)
        assert len(package) == 1600
        content = package.tobytes(deflate=True)
    barrier = threading.Barrier(10)
    with TestClient(app, raise_server_exceptions=False) as client:
        def get(path):
            response = client.get(path)
            response.raise_for_status()
            return response.json()

        projects = []
        for i in range(10):
            response = client.post("/api/projects", json={"name": f"Load fixture {i + 1}"})
            response.raise_for_status()
            pid = response.json()["project"]["project_id"]
            response = client.post(f"/api/projects/{pid}/documents?parse=false",
                                   files={"files": ("Clinical_Study_Report.pdf", content, "application/pdf")})
            response.raise_for_status()
            projects.append(pid)

        def analyze(pid):
            barrier.wait()
            start = time.monotonic()
            row = {"project": pid}
            try:
                response = client.post(f"/api/projects/{pid}/analyze")
                response.raise_for_status()
                run_id = response.json()["run_id"]
                row["run"] = run_id
                while time.monotonic() - start < 1800:
                    run = get(f"/api/runs/{run_id}")["run"]
                    if run["status"] != "running":
                        break
                    time.sleep(1)
                row.update(status=run["status"], error=run.get("error"),
                           stages_done=[k for k,v in run["stats"].items()
                                        if k.startswith("stage_") and v.get("state") == "done"])
                detail = get(f"/api/projects/{pid}")
                row["pages"] = detail["stats"]["pages"]
                row["evidence"] = detail["stats"]["evidence"]
                if row["status"] == "succeeded":
                    report = client.get(detail["outputs"]["report_md"]["url"])
                    report.raise_for_status()
                    row["report_bytes"] = len(report.content)
                    row["report_sha256"] = hashlib.sha256(report.content).hexdigest()
            except Exception as exc:
                row["error"] = str(exc)
            row["seconds"] = round(time.monotonic() - start, 3)
            row["passed"] = (row.get("status") == "succeeded" and row.get("pages") == 1600
                             and row.get("evidence", 0) > 0 and row.get("report_bytes", 0) > 0
                             and len(row.get("stages_done", [])) == 7 and not row.get("error"))
            return row

        with ThreadPoolExecutor(max_workers=10) as pool:
            rows = list(pool.map(analyze, projects))
    times = sorted(r["seconds"] for r in rows if r["passed"])
    return dict(mode="rules", fixture="first 200 pages of synthetic CSR repeated eight times",
                source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                passed_projects=len(times), p95_seconds=times[math.ceil(.95*len(times))-1] if times else None,
                median_seconds=statistics.median(times) if times else None, projects=rows)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="hijenny-project-load-") as tmp:
        os.environ.update(HIJENNY_DATA_DIR=tmp, HIJENNY_DB_PATH=str(Path(tmp)/"test.db"), HIJENNY_EXTRACT_MODE="rules")
        with redirect_stdout(sys.stderr):
            result = benchmark()
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result["passed_projects"] == 10 else 1)
