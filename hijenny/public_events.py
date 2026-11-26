"""Import the public GitHub event ledger into a separate, transactional SQLite index."""
import argparse
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.request import Request, urlopen

from hijenny.config import DATA_DIR, REPO_ROOT

REPO = "https://api.github.com/repos/james-lebron2000/HiJenny"


def get(url, accept="application/vnd.github+json"):
    with urlopen(Request(url, headers={"User-Agent": "HiJenny-public-index", "Accept": accept}), timeout=30) as response:
        return response.read()


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATA_DIR / "public-events.db", timeout=20)
    db.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, asset TEXT, region TEXT, date TEXT, payload TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS files (name TEXT PRIMARY KEY, sha TEXT)")
    return db


def import_month(db, name, sha, content):
    if hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest() != sha:
        raise ValueError("Git blob digest mismatch")
    for line in content.splitlines():
        row = json.loads(line)
        if not row["url"].startswith(("https://www.sec.gov/Archives/", "https://clinicaltrials.gov/study/", "https://pubmed.ncbi.nlm.nih.gov/")):
            raise ValueError("Unexpected public source")
        payload = json.dumps(row, sort_keys=True, ensure_ascii=False)
        old = db.execute("SELECT payload FROM events WHERE id=?", (row["id"],)).fetchone()
        if old and old[0] != payload:
            raise ValueError("Conflicting immutable event")
        db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)",
                   (row["id"], row["asset"] or row["subject"], row["region"], row["source_date"], payload))
    db.execute("INSERT OR REPLACE INTO files VALUES (?,?)", (name, sha))


def sync(local=False):
    with closing(connect()) as db, db:
        if local:
            for path in sorted((REPO_ROOT / "data/public/events").glob("*.jsonl")):
                data = path.read_bytes()
                sha = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                import_month(db, path.name, sha, data)
        else:
            listing = json.loads(get(REPO + "/contents/data/public/events?ref=main"))
            for file in listing:
                if not re.fullmatch(r"[0-9]{4}-[0-9]{2}\.jsonl", file["name"]):
                    continue
                old = db.execute("SELECT sha FROM files WHERE name=?", (file["name"],)).fetchone()
                if old != (file["sha"],):
                    content = get(REPO + "/git/blobs/" + file["sha"], "application/vnd.github.raw+json")
                    import_month(db, file["name"], file["sha"], content)
        return db.execute("SELECT count(*) FROM events").fetchone()[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true")
    print("Indexed public events:", sync(parser.parse_args().local))
