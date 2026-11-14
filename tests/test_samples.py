from __future__ import annotations

from pathlib import Path

import pytest

from igi_model.profile_io import load_profile
from igi_model.service import analyze_files


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def sample_result():
    required = [ROOT / "Inbox" / name for name in ("plan.dwg", "skvazhiny.dwg", "table.dwg")]
    if not all(path.exists() for path in required):
        pytest.skip("sample DWG files are absent")
    return analyze_files(
        required[0], required[1], load_profile(ROOT / "profiles" / "inbox_example.json"), required[2]
    )


def test_sample_boreholes(sample_result):
    assert [item.display_id for item in sample_result.boreholes] == [str(value) for value in range(47, 62)]
    first = sample_result.boreholes[0]
    assert first.x == pytest.approx(64.4, abs=0.06)
    assert first.y == pytest.approx(4.7, abs=0.06)
    assert first.elevation == pytest.approx(102.51)
    assert [(layer.ige, layer.thickness) for layer in first.layers] == [
        ("1", 1.7), ("2", 1.4), ("5", 3.7), ("4", 0.5), ("5", 1.8),
        ("4", 1.6), ("5", 3.9), ("4", 1.6), ("5", 10.8),
    ]


def test_sample_soils(sample_result):
    assert [item.ige for item in sample_result.soils] == ["1", "2", "3", "4", "5"]
    assert [item.deformation_modulus for item in sample_result.soils] == [1427, 1529, 1631, 1529, 2039]
    assert [item.subgrade_k for item in sample_result.soils] == [400, 340.6, 552, 380.2, 600]
    assert [item.code for item in sample_result.soils] == ["Ls", "Lp", "Ls", "Lp", "Cs"]


def test_only_known_elevation_discrepancy(sample_result):
    messages = [issue.message for issue in sample_result.issues]
    assert any("66853" in message for message in messages)
    assert not any(issue.level == "error" for issue in sample_result.issues)
