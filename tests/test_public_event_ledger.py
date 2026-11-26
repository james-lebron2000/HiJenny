import hashlib
import json
from contextlib import closing
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from hijenny import public_events

spec = spec_from_file_location("collector", Path(__file__).resolve().parents[1] / "tools/collect_public_events.py")
collector = module_from_spec(spec)
spec.loader.exec_module(collector)


def test_retry_change_reversion_and_sql_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(public_events, "DATA_DIR", tmp_path)
    row = dict(record="NCT05899608", field="登记状态", source="ClinicalTrials.gov", value="A",
               source_date="2026-11-01", asset="demo", subject="demo", region=None,
               url="https://clinicaltrials.gov/study/NCT05899608")
    history = []
    for value in ("A", "A", "B", "A"):
        history.extend(collector.changes([dict(row, value=value)], history, "2026-11-02T00:00:00Z"))
    assert [entry["previous"] for entry in history] == [None, "A", "B"]
    assert not list(collector.changes([dict(row, value="B", source_date="2026-10-21")], history, "later"))
    content = "\n".join(json.dumps(entry) for entry in history).encode()
    sha = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
    with closing(public_events.connect()) as db, db:
        for _ in range(2):
            public_events.import_month(db, "2026-11.jsonl", sha, content)
        assert db.execute("select count(*) from events").fetchone()[0] == 3
        with pytest.raises(ValueError, match="digest"):
            public_events.import_month(db, "2026-11.jsonl", sha, content + b" ")
