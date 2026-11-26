import os
import sqlite3
from contextlib import closing
import subprocess
import sys

import pytest

from tools.state_backup import run


def test_recover_isolated_state_via_api(tmp_path):
    source, archive, restored = [tmp_path / name for name in ("state", "backup", "restore")]
    receipt = tmp_path / "receipt.json"
    script = """
import hashlib, json, sys
from pathlib import Path
from fastapi.testclient import TestClient
from hijenny.api.main import app
from hijenny.config import DATA_DIR
with TestClient(app) as client:
    def checked(response):
        response.raise_for_status()
        return response
    def digest(url):
        return hashlib.sha256(checked(client.get(url)).content).hexdigest()
    receipt = Path(sys.argv[2])
    if sys.argv[1] == "seed":
        expected = []
        for org in ["alpha", "beta"]:
            project = checked(client.post("/api/projects", json={"name": "Drill " + org, "org_id": org})).json()["project"]
            route = "/api/projects/" + project["project_id"]
            doc = checked(client.post(route + "/documents?parse=false",
                          files={"files": ("example.txt", org.encode(), "text/plain")})).json()["documents"][0]
            export = checked(client.post(route + "/exports/questions")).json()
            urls = [doc["file_url"], export["download_url"]]
            expected.append([route, org, {u: digest(u) for u in urls}])
        receipt.write_text(json.dumps(expected))
    else:
        for route, org, files in json.loads(receipt.read_text()):
            project = checked(client.get(route, headers={"X-Org-Id":org})).json()["project"]
            assert project["name"] == "Drill " + org
            assert client.get(route, headers={"X-Org-Id":"other"}).status_code == 404
            documents = checked(client.get(route + "/documents")).json()["documents"]
            assert documents[0]["file_url"] in files
            assert all(digest(url) == sha for url, sha in files.items())
"""
    def request(mode, root):
        env = dict(os.environ, HIJENNY_DATA_DIR=str(root), HIJENNY_DB_PATH=str(root / "hijenny.db"))
        subprocess.run([sys.executable, "-c", script, mode, str(receipt)], env=env, check=True)

    request("seed", source)
    with closing(sqlite3.connect(source / "hijenny.db")) as db, db:
        doc_id, relative = db.execute("SELECT document_id, stored_path FROM documents LIMIT 1").fetchone()
        db.execute("UPDATE documents SET stored_path=? WHERE document_id=?", (str(source / relative), doc_id))
    count = run("backup", source, archive)
    source.rename(tmp_path / "offline")
    assert run("restore", archive, restored) == count
    request("check", restored)
    with pytest.raises(FileExistsError):
        run("restore", archive, restored)
    next((archive / "state").rglob("example.txt")).write_bytes(b"bad")
    with pytest.raises(ValueError, match="digest mismatch"):
        run("restore", archive, tmp_path / "damaged")
    assert not (tmp_path / "damaged").exists()
