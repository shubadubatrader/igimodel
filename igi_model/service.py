from __future__ import annotations

from pathlib import Path

from .coordinate_detection import detect_coordinate_file
from .detection import analyze
from .models import AnalysisResult, DetectionProfile
from .pdf_reader import read_source
from .soil_detection import detect_soils


def analyze_files(
    plan_path: str | Path | None,
    columns_path: str | Path,
    profile: DetectionProfile | None = None,
    soils_path: str | Path | None = None,
    coordinates_path: str | Path | None = None,
) -> AnalysisResult:
    soils = None
    soil_issues = []
    if soils_path:
        soils, soil_issues = detect_soils(read_source(soils_path))
        if not soils:
            soils = None
    coordinate_records = None
    coordinate_issues = []
    if coordinates_path:
        coordinate_records, coordinate_issues = detect_coordinate_file(coordinates_path)
    plan_data = read_source(plan_path) if plan_path else None
    result = analyze(plan_data, read_source(columns_path), profile, soils, coordinate_records)
    result.issues = coordinate_issues + soil_issues + result.issues
    return result
