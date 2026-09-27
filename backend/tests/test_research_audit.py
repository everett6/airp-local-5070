import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from research_audit import audit


def test_audit_flags_late_facts_and_foreign_sources(tmp_path):
    rec = {"accession": "a", "ticker": "X", "as_of": "2025-05-06T20:00:00+00:00", "evidence": "see https://s/1 text",
           "brief": {"facts": [{"text": "ok", "source": "https://s/1", "date": "2025-05-06"},
                               {"text": "late", "source": "https://s/1", "date": "2025-05-07"},
                               {"text": "elsewhere", "source": "https://s/2", "date": ""}]}}
    (tmp_path / "a.json").write_text(json.dumps(rec))
    (tmp_path / "b.json").write_text(json.dumps({"accession": "b", "ticker": "Y", "as_of": "2025-01-01"}))
    n, bad = audit(tmp_path)
    assert n == {"releases": 2, "with_brief": 1, "facts": 3, "dated_facts": 2}
    assert len(bad) == 2 and "after the decision day" in bad[0] and "not in the evidence" in bad[1]
