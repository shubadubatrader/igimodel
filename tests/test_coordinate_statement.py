from __future__ import annotations

import pytest

from igi_model.coordinate_detection import detect_coordinate_rows
from igi_model.detection import apply_coordinate_statement, merge_boreholes
from igi_model.models import (
    Borehole, CoordinateRecord, DetectionProfile, DrawingData, LayerRecord, TextItem,
)


def test_coordinate_row_is_read_without_geometry():
    data = DrawingData(texts=[
        TextItem("1. С-66847 485352,41 2163321.42 102,51 151 27,0 24.03.2026", 10, 20),
    ])
    records = detect_coordinate_rows(data)
    assert len(records) == 1
    assert records[0].source_id == "66847"
    assert records[0].x == pytest.approx(485352.41)
    assert records[0].y == pytest.approx(2163321.42)
    assert records[0].elevation == pytest.approx(102.51)


def test_statement_has_priority_and_plan_is_control():
    plan = [
        Borehole("66847", "66847", x_global=2163321.42, y_global=485352.41, elevation=102.50, confidence=0.9),
        Borehole("66848", "66848", x_global=2163292.22, y_global=485354.90, elevation=102.47, confidence=0.9),
    ]
    records = [CoordinateRecord("66847", 485352.41, 2163321.42, 102.51, 0.98)]
    result, issues = apply_coordinate_statement(plan, records, DetectionProfile())

    statement = next(item for item in result if item.source_id == "66847")
    fallback = next(item for item in result if item.source_id == "66848")
    assert statement.x_global == pytest.approx(2163321.42)
    assert statement.y_global == pytest.approx(485352.41)
    assert statement.elevation == pytest.approx(102.51)
    assert statement.coordinate_source == "statement"
    assert fallback.coordinate_source == "plan"
    assert any("строки в ведомости нет" in issue.message for issue in issues)
    assert not any("координаты ведомости и схемы различаются" in issue.message for issue in issues)


def test_statement_elevation_wins_over_column():
    base = Borehole(
        "66853", "66853", x_global=1, y_global=2, elevation=103.02,
        coordinate_source="statement", confidence=0.98,
    )
    column = Borehole(
        "66853", "66853", elevation=103.22,
        layers=[LayerRecord("1", 2.0)], confidence=0.9,
    )
    result, issues = merge_boreholes([base], [column])
    assert result[0].elevation == pytest.approx(103.02)
    assert result[0].layers[0].ige == "1"
    assert any("взята отметка ведомости" in issue.message for issue in issues)
