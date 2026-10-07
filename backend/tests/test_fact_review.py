import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import fact_review as fr


def test_queue_label_and_accuracy(tmp_path):
    mem, root = tmp_path / "mem", tmp_path / "review"
    mem.mkdir()
    (mem / "AAA.json").write_text(json.dumps({"ticker": "AAA", "facts": [{"text": f"fact {i}", "source": "u"}
                                                                        for i in range(3)]}))
    q = fr.queue(10, root, mem)
    assert len(q) == 3
    fr.label(q[0]["id"], "correct", root=root)
    fr.label(q[1]["id"], "wrong", root=root)
    fr.label(q[2]["id"], "unclear", root=root)
    st = fr.status(root)
    assert st["accuracy"] == 0.5 and st["n_scored"] == 2 and st["queue"] == []
    assert fr.queue(10, root, mem) == []  # labelled facts are not asked again
    with pytest.raises(SystemExit):
        fr.label("nope", "correct", root=root)
