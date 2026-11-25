"""HiJenny command line interface.

Examples
--------
    python3 -m hijenny.cli demo --package data/sample_package
    python3 -m hijenny.cli run --project prj_xxxx --force
    python3 -m hijenny.cli status --project prj_xxxx
    python3 -m hijenny.cli serve --port 8000
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from hijenny import config, pipeline, store


def _load_package(project_id: str, package_dir: Path) -> Path:
    """Copy package files into the project upload directory and register the path."""
    dest = config.DATA_DIR / "projects" / project_id / "documents"
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in sorted(package_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in {".pdf", ".docx", ".pptx", ".xlsx"}:
            target = dest / path.name
            if not target.exists() or target.stat().st_size != path.stat().st_size:
                shutil.copy2(path, target)
                copied += 1
    return dest


def cmd_demo(args: argparse.Namespace) -> int:
    package_dir = Path(args.package).resolve()
    if not package_dir.is_dir():
        print(f"package directory not found: {package_dir}", file=sys.stderr)
        return 2
    with store.session_scope() as session:
        project = store.create_project(
            session,
            name=args.name or f"DD - {package_dir.name}",
            asset_name=args.asset or "ASD-101",
            indication=args.indication or "MTAP-deleted advanced solid tumors",
            phase=args.phase or "Phase 2",
        )
        project_id = project["project_id"]
        dest = _load_package(project_id, package_dir)
        store.update_project(session, project_id, meta={"package_dir": str(dest), "source_package": str(package_dir)})
    print(f"project created: {project_id}  documents: {dest}")
    return _run(project_id, force=args.force)


def _convert_pdf(docx_path: Path, out_dir: Path) -> Path | None:
    """Convert a DOCX report to PDF with LibreOffice, when available."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    profile_dir = Path(tempfile.mkdtemp(prefix="hijenny_lo_"))
    try:
        subprocess.run(
            [
                soffice,
                # Isolated, writable LibreOffice profile (required in sandboxed/CI environments).
                f"-env:UserInstallation=file://{profile_dir}",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(docx_path),
            ],
            check=False,
            timeout=180,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    pdf = out_dir / (docx_path.stem + ".pdf")
    return pdf if pdf.is_file() and pdf.stat().st_size > 0 else None


def cmd_demo_kit(args: argparse.Namespace) -> int:
    """Build an isolated, ready-to-present demo workspace (DB + project + deliverables)."""
    package_dir = Path(args.package).resolve()
    if not package_dir.is_dir():
        print(f"package directory not found: {package_dir}", file=sys.stderr)
        return 2

    out = Path(args.out).resolve()
    data_dir = out / "data"
    deliverables = out / "deliverables"
    data_dir.mkdir(parents=True, exist_ok=True)
    deliverables.mkdir(parents=True, exist_ok=True)

    # Isolate the demo: its own data dir, DB and project store.
    config.DATA_DIR = data_dir
    config.DB_PATH = data_dir / "hijenny.db"
    store.configure(config.DB_PATH)
    store.init_db()

    with store.session_scope() as session:
        project = store.create_project(
            session,
            name=args.name or "ASD-101 License-in Clinical Due Diligence",
            asset_name=args.asset or "ASD-101 (zafinostat)",
            indication=args.indication or "MTAP-deleted advanced solid tumors",
            phase=args.phase or "Phase 2",
        )
        project_id = project["project_id"]
        dest = _load_package(project_id, package_dir)
        store.update_project(session, project_id, meta={"package_dir": str(dest), "source_package": str(package_dir)})

    run = pipeline.run_pipeline(project_id)
    if run.get("error"):
        print(run["error"], file=sys.stderr)
        return 1

    exports_dir = data_dir / "projects" / project_id / "exports"
    copied: list[Path] = []
    for name in ("HiJenny_Report.md", "HiJenny_Report.docx", "Seller_Questions.xlsx"):
        src = exports_dir / name
        if src.is_file():
            target = deliverables / name
            shutil.copy2(src, target)
            copied.append(target)
    pdf_path = None
    docx = deliverables / "HiJenny_Report.docx"
    if not args.no_pdf and docx.is_file():
        pdf_path = _convert_pdf(docx, deliverables)
        if pdf_path:
            copied.append(pdf_path)

    with store.session_scope() as session:
        stats = store.project_stats(session, project_id)
        findings = store.list_findings(session, project_id)
        questions = store.list_questions(session, project_id)

    info = {
        "project_id": project_id,
        "workspace": str(out),
        "data_dir": str(data_dir),
        "db_path": str(config.DB_PATH),
        "documents": stats.get("documents"),
        "pages": stats.get("pages"),
        "evidence": stats.get("evidence"),
        "findings": len(findings),
        "findings_by_severity": stats.get("findings_by_severity"),
        "questions": len(questions),
        "coverage_by_status": stats.get("coverage_by_status"),
        "deliverables": [str(p.relative_to(out)) for p in copied],
        "serve_command": (
            f"HIJENNY_DATA_DIR={data_dir} HIJENNY_DB_PATH={config.DB_PATH} "
            f"hijenny serve --port 8123"
        ),
    }
    (out / "demo_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    (out / "README.md").write_text(_demo_readme(info), encoding="utf-8")

    print(json.dumps(info, indent=2))
    print(f"\ndemo workspace ready: {out}")
    print(f"open the UI: {info['serve_command']}")
    if args.serve:
        import uvicorn

        uvicorn.run("hijenny.api.main:app", host=args.host, port=args.port, reload=False)
    return 0


def _demo_readme(info: dict) -> str:
    return f"""# HiJenny demo workspace / 演示工作区

Generated by `hijenny demo-kit`. Everything here is isolated from your normal
`data/` directory, so it is safe to reset or delete.

- Project: `{info['project_id']}`
- Package: {info['documents']} documents / {info['pages']} pages
- Evidence: {info['evidence']} rows · Findings: {info['findings']} · Questions: {info['questions']}
- Deliverables: {', '.join(info['deliverables'])}

## Start the demo UI / 启动演示界面

```bash
{info['serve_command']}
```

Open <http://127.0.0.1:8123/> with a desktop browser. Suggested demo flow:
Projects → Upload & Analysis → Evidence → Findings & Questions → Report.

## Deliverables / 交付物

See `deliverables/`: due-diligence report (MD/DOCX/PDF) and the 6-sheet
`Seller_Questions.xlsx` workbook.

## Reset / 重置

Delete this directory and run `hijenny demo-kit --out <dir>` again.
"""


def cmd_run(args: argparse.Namespace) -> int:
    return _run(args.project, force=args.force)


def _run(project_id: str, force: bool) -> int:
    with store.session_scope() as session:
        project = store.get_project(session, project_id)
    if not project:
        print(f"project not found: {project_id}", file=sys.stderr)
        return 2
    print(f"running analysis for {project_id} ({project['name']}) ...")
    run = pipeline.run_pipeline(project_id, force=force)
    print(json.dumps({"run": pipeline.progress(run), "stats": run.get("stats", {})}, indent=2, default=str))
    if run.get("error"):
        print(run["error"], file=sys.stderr)
        return 1
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    from hijenny.ingestion import ingest_project

    with store.session_scope() as session:
        stats = ingest_project(session, args.project, force=args.force)
    print(json.dumps(stats, indent=2, default=str))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    with store.session_scope() as session:
        project = store.get_project(session, args.project)
        if not project:
            print(f"project not found: {args.project}", file=sys.stderr)
            return 2
        stats = store.project_stats(session, args.project)
        findings = store.list_findings(session, args.project)
        questions = store.list_questions(session, args.project)
        coverage = store.list_coverage(session, args.project)
        runs = store.list_runs(session, args.project, limit=5)
    print(
        json.dumps(
            {
                "project": project,
                "stats": stats,
                "findings": [
                    {"id": f["finding_id"], "type": f["finding_type"], "severity": f["severity"],
                     "status": f["status"], "title": f["title"]}
                    for f in findings
                ],
                "questions": [
                    {"id": q["question_id"], "priority": q["priority"], "owner": q["owner"],
                     "status": q["status"], "question": q["question"]}
                    for q in questions
                ],
                "coverage": coverage,
                "runs": runs,
            },
            indent=2,
            default=str,
        )
    )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("hijenny.api.main:app", host=args.host, port=args.port, reload=False)
    return 0


def cmd_projects(args: argparse.Namespace) -> int:
    with store.session_scope() as session:
        projects = store.list_projects(session)
    print(json.dumps(projects, indent=2, default=str))
    return 0


def cmd_pilot_progress(args: argparse.Namespace) -> int:
    try:
        with store.session_scope() as session:
            store.record_pilot_progress(session, args.receipt, args.stage, args.reference)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"{args.receipt} · {args.stage}")
    return 0


def cmd_pilot_metrics(args: argparse.Namespace) -> int:
    with store.session_scope() as session:
        print(json.dumps(store.pilot_metrics(session, args.since), ensure_ascii=False, indent=2))
    return 0


def cmd_pilot_inquiries(args: argparse.Namespace) -> int:
    with store.session_scope() as session:
        records = store.list_pilot_inquiries(session, args.receipt)
    print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hijenny", description="HiJenny clinical due diligence")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="create a project from a data package and run the full pipeline")
    demo.add_argument("--package", required=True)
    demo.add_argument("--name", default=None)
    demo.add_argument("--asset", default=None)
    demo.add_argument("--indication", default=None)
    demo.add_argument("--phase", default=None)
    demo.add_argument("--force", action="store_true")
    demo.set_defaults(func=cmd_demo)

    kit = sub.add_parser(
        "demo-kit",
        help="build an isolated, presentation-ready demo workspace (DB + project + deliverables)",
    )
    kit.add_argument("--package", default="data/sample_package")
    kit.add_argument("--out", default="demo_workspace")
    kit.add_argument("--name", default=None)
    kit.add_argument("--asset", default=None)
    kit.add_argument("--indication", default=None)
    kit.add_argument("--phase", default=None)
    kit.add_argument("--no-pdf", action="store_true", help="skip DOCX→PDF conversion")
    kit.add_argument("--serve", action="store_true", help="start the UI after building the workspace")
    kit.add_argument("--host", default="127.0.0.1")
    kit.add_argument("--port", type=int, default=8123)
    kit.set_defaults(func=cmd_demo_kit)

    run = sub.add_parser("run", help="run the analysis pipeline for an existing project")
    run.add_argument("--project", required=True)
    run.add_argument("--force", action="store_true")
    run.set_defaults(func=cmd_run)

    ingest = sub.add_parser("ingest", help="only run document ingestion")
    ingest.add_argument("--project", required=True)
    ingest.add_argument("--force", action="store_true")
    ingest.set_defaults(func=cmd_ingest)

    status = sub.add_parser("status", help="show project findings, questions and coverage")
    status.add_argument("--project", required=True)
    status.set_defaults(func=cmd_status)

    projects = sub.add_parser("projects", help="list projects")
    projects.set_defaults(func=cmd_projects)

    metrics = sub.add_parser("pilot-metrics", help="show cumulative anonymous usage and distinct sales receipt counts")
    metrics.add_argument("--since", type=date.fromisoformat, help="UTC cohort entry date (YYYY-MM-DD), inclusive")
    metrics.set_defaults(func=cmd_pilot_metrics)

    inquiries = sub.add_parser("pilot-inquiries", help="review pilot inquiries in this server database")
    inquiries.add_argument("--receipt", help="look up one intake receipt")
    inquiries.set_defaults(func=cmd_pilot_inquiries)

    progress = sub.add_parser("pilot-progress", help="record a sales milestone and its supporting reference")
    progress.add_argument("receipt")
    progress.add_argument("stage", choices=["已联系", "已报价", "已下单", "已回款", "已关闭"])
    progress.add_argument("reference", help="contact record, quote, order or payment reference")
    progress.set_defaults(func=cmd_pilot_progress)

    serve = sub.add_parser("serve", help="start the FastAPI application")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
