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
import json
import shutil
import sys
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
