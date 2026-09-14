"""Headless smoke test: the dashboard renders every run without exceptions."""
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from app.dashboard import data as D

APP = Path(__file__).resolve().parents[1] / "app" / "dashboard" / "app.py"


@pytest.mark.skipif(not D.list_runs(), reason="no published results")
def test_every_run_renders_without_errors():
    at = AppTest.from_file(str(APP), default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    runs = list(at.sidebar.selectbox[0].options)
    for label in runs:
        at.sidebar.selectbox[0].select(label).run()
        assert not at.exception, (label, [e.value for e in at.exception])
    at.sidebar.toggle[0].set_value(False).run()
    assert not at.exception
    stamped = [r for r in D.list_runs() if D.load_report(r["tag"]).get("provenance")]
    assert stamped, "expected at least one run with receipts"
