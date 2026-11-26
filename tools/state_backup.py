"""Back up a quiescent HiJenny state directory and restore it into a fresh directory."""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path


def inventory(root):
    files = {}
    for path in root.rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError(f"Unsupported state entry: {path.relative_to(root)}")
        if path.is_file():
            with path.open("rb") as stream:
                files[path.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
    return files


def recover_database(root, original, files):
    with closing(sqlite3.connect(root / "hijenny.db")) as db, db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity check failed")
        for doc_id, stored, digest in db.execute("SELECT document_id,stored_path,sha256 FROM documents").fetchall():
            if not stored:
                continue
            relative = (original / stored).relative_to(original)
            if ".." in relative.parts or files.get(relative.as_posix()) != digest or not (root / relative).is_file():
                raise ValueError(f"Document content missing or inconsistent: {doc_id}")
            if Path(stored).is_absolute():
                db.execute("UPDATE documents SET stored_path=? WHERE document_id=?", (relative.as_posix(), doc_id))


def run(action, source, destination):
    source, destination = source.resolve(strict=True), destination.absolute()
    if source == destination.resolve() or source in destination.resolve().parents:
        raise ValueError("Destination must be outside the source tree")
    destination.mkdir(mode=0o700, exist_ok=False)
    try:
        if action == "backup":
            before = inventory(source)
            if "hijenny.db" not in before:
                raise ValueError("Expected hijenny.db in the state directory")
            payload = destination / "state"
            shutil.copytree(source, payload, symlinks=True)
            if inventory(payload) != before or inventory(source) != before:
                raise ValueError("State changed while copying; retry with writers stopped")
            manifest = {"version": 1, "original": str(source), "sha256": before}
            (destination / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
            return len(before)
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        payload = source / "state"
        if manifest["version"] != 1 or payload.is_symlink() or "hijenny.db" not in manifest["sha256"]:
            raise ValueError("Invalid state backup")
        if inventory(payload) != manifest["sha256"]:
            raise ValueError("Backup digest mismatch")
        for item in payload.iterdir():
            target = destination / item.name
            if item.is_dir():
                shutil.copytree(item, target, symlinks=True)
            else:
                shutil.copy2(item, target, follow_symlinks=False)
        if inventory(destination) != manifest["sha256"]:
            raise ValueError("Restored files differ from manifest")
        recover_database(destination, Path(manifest["original"]), manifest["sha256"])
        return len(manifest["sha256"])
    except BaseException:
        shutil.rmtree(destination)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    print(f"{args.action}: {run(args.action, args.source, args.destination)} files verified")
