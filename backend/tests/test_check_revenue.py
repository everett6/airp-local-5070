import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_features import check_revenue


def test_revenue_in_scale_is_kept():
    assert check_revenue(3800.0, 3899.0) == 3800.0


def test_unit_mistakes_are_rescaled():
    assert check_revenue(177.8, 165609.0) == 177800.0  # reported in billions
    assert check_revenue(680523.0, 740.55) == 680.523  # reported in thousands


def test_wrong_line_is_dropped():
    assert check_revenue(-3.5, 3899.0) is None  # KVUE: a -4M line read as revenue
    assert check_revenue(6787.0, 20849.0) is None  # a segment, not the total


def test_unchecked_without_sec_history():
    assert check_revenue(446.0, None) == 446.0
    assert check_revenue(None, 3899.0) is None
