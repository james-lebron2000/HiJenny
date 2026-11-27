"""Import one GitHub directory snapshot, then reconcile the running API over its Unix socket."""
import argparse
import json
import re
import tempfile
from contextlib import closing
from pathlib import Path

import httpx

from hijenny.public_events import REPO, connect, get, import_month
from tools.verify_public_archive import verify


def sync_verified(socket_path):
    listing = json.loads(get(REPO + "/contents/data/public/events?ref=main"))
    files = {f["name"]: f["sha"] for f in listing if f["name"] == "collection.json"
             or re.fullmatch(r"[0-9]{4}-[0-9]{2}\.jsonl", f["name"])}
    if "collection.json" not in files or len(files) < 2:
        raise ValueError("Missing GitHub public ledger or collection receipt")
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp)
        for name, sha in files.items():
            (archive / name).write_bytes(get(REPO + "/git/blobs/" + sha, "application/vnd.github.raw+json"))
        with closing(connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            expected_count = 0
            for name, sha in files.items():
                content = (archive / name).read_bytes()
                import_month(db, name, sha, content)
                if name.endswith(".jsonl"):
                    expected_count += len(content.splitlines())
            if db.execute("SELECT count(*) FROM events").fetchone()[0] != expected_count:
                raise ValueError("SQLite event count differs from the GitHub ledger")
        # Read the application after commit, using the same immutable blobs that were imported.
        transport = httpx.HTTPTransport(uds=socket_path)
        with httpx.Client(transport=transport, base_url="http://localhost", timeout=30) as client:
            def read(suffix):
                response = client.get("/api/public-events" + suffix)
                response.raise_for_status()
                return response.json()

            return verify("", archive, read_json=read)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("socket_path")
    print(json.dumps(sync_verified(parser.parse_args().socket_path), ensure_ascii=False))
