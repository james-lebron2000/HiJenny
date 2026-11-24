#!/usr/bin/env python3
"""Capture HiJenny demo screenshots from a running local server.

Usage:
    # terminal 1
    hijenny demo-kit --out demo_workspace
    HIJENNY_DATA_DIR=demo_workspace/data HIJENNY_DB_PATH=demo_workspace/data/hijenny.db \
        hijenny serve --port 8123

    # terminal 2
    python3 tools/capture_screenshots.py --base http://127.0.0.1:8123 \
        --out docs/assets/screenshots

Requires Google Chrome (macOS path by default) and a running demo server.
The script reads IDs from the API, so screenshots always reflect real data.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
VIEWS = ["projects", "upload", "evidence", "findings", "report"]
SHOT_WIDTH, SHOT_HEIGHT = 1600, 1000


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _find_evidence(base: str, project_id: str) -> tuple[str | None, str | None]:
    docs = _get(base, f"/api/projects/{project_id}/documents").get("documents", [])
    csr = next((d for d in docs if d.get("filename") == "Clinical_Study_Report_v1.0.pdf"), None)
    evidence = _get(base, f"/api/projects/{project_id}/evidence?field_name=orr&limit=500").get("evidence", [])
    target = None
    for ev in evidence:
        if csr and ev.get("document_id") == csr.get("document_id") and ev.get("page") == 11:
            target = ev
            break
    return (csr or {}).get("document_id"), (target or {}).get("evidence_id")


def capture(chrome: str, url: str, dest: Path, profile: Path) -> None:
    subprocess.run(
        [
            chrome,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-crash-reporter",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            f"--window-size={SHOT_WIDTH},{SHOT_HEIGHT}",
            "--virtual-time-budget=20000",
            f"--user-data-dir={profile}",
            f"--screenshot={dest}",
            url,
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8123")
    parser.add_argument("--out", default="docs/assets/screenshots")
    parser.add_argument("--chrome", default=shutil.which("google-chrome") or DEFAULT_CHROME)
    args = parser.parse_args()

    if not Path(args.chrome).exists():
        raise SystemExit(f"Chrome not found: {args.chrome}")

    projects = _get(args.base, "/api/projects").get("projects", [])
    if not projects:
        raise SystemExit("No projects on the server; run `hijenny demo-kit` first.")
    project_id = projects[0]["project_id"]
    doc_id, evidence_id = _find_evidence(args.base, project_id)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="hijenny_chrome_"))

    urls = {
        "projects": f"{args.base}/#/projects?project={project_id}",
        "upload": f"{args.base}/#/upload?project={project_id}",
        "findings": f"{args.base}/#/findings?project={project_id}",
        "report": f"{args.base}/#/report?project={project_id}",
    }
    if doc_id and evidence_id:
        urls["evidence"] = (
            f"{args.base}/#/evidence?project={project_id}&doc={doc_id}&page=11"
            f"&preview=text&evidence={evidence_id}"
        )
    else:
        urls["evidence"] = f"{args.base}/#/evidence?project={project_id}"

    for index, view in enumerate(VIEWS, start=1):
        if view not in urls:
            continue
        dest = out / f"{index:02d}_{view}.png"
        capture(args.chrome, urls[view], dest, profile)
        print(f"captured {dest} ({dest.stat().st_size} bytes)")

    # Keep a machine-readable index for docs/decks.
    (out / "index.json").write_text(
        json.dumps(
            {
                "project_id": project_id,
                "base": args.base,
                "views": {
                    view: f"{index:02d}_{view}.png" for index, view in enumerate(VIEWS, start=1)
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
