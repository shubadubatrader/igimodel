from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import math
import os
import re
from statistics import median
import subprocess
import tempfile

import fitz
import numpy as np

from .models import CoordinateRecord, DrawingData, Issue, TextItem
from .pdf_reader import IMAGE_SUFFIXES, read_source, tesseract_path


_BOREHOLE_ID = re.compile(
    r"(?i)(?:\b(?:скв(?:ажина)?\.?|bh|borehole)\s*(?:№\s*)?|\b[сc]\s*[-№]\s*)(\d{2,12})"
)
_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def _number(value: str) -> float | None:
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return None


def _parse_row(text: str, confidence: float) -> CoordinateRecord | None:
    cleaned = text.replace("—", "-").replace("–", "-").replace("|", " ")
    match = _BOREHOLE_ID.search(cleaned)
    if not match:
        return None
    values = [_number(token) for token in _NUMBER.findall(cleaned[match.end():])]
    values = [value for value in values if value is not None]
    if len(values) < 2:
        return None
    x, y = values[0], values[1]
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    elevation = values[2] if len(values) >= 3 and -1000 < values[2] < 10000 else None
    return CoordinateRecord(match.group(1), x, y, elevation, confidence)


def _row_strings(data: DrawingData) -> list[str]:
    result: list[str] = []
    pages: dict[int, list[TextItem]] = defaultdict(list)
    for item in data.texts:
        pages[item.page].append(item)
    for items in pages.values():
        items.sort(key=lambda item: item.y, reverse=True)
        groups: list[list[TextItem]] = []
        for item in items:
            tolerance = max(1.5, item.height * 0.7)
            if not groups or abs(item.y - median(value.y for value in groups[-1])) > tolerance:
                groups.append([item])
            else:
                groups[-1].append(item)
        for group in groups:
            group.sort(key=lambda item: item.x)
            line = " ".join(item.text.strip() for item in group if item.text.strip()).strip()
            if line:
                result.append(line)
    return result


def detect_coordinate_rows(data: DrawingData, confidence: float = 0.82) -> list[CoordinateRecord]:
    records: dict[str, CoordinateRecord] = {}
    for line in _row_strings(data):
        record = _parse_row(line, confidence)
        if record and (record.source_id not in records or record.confidence > records[record.source_id].confidence):
            records[record.source_id] = record
    return list(records.values())


def _centers(mask: np.ndarray) -> list[int]:
    indexes = np.flatnonzero(mask)
    if indexes.size == 0:
        return []
    groups: list[list[int]] = [[int(indexes[0])]]
    for raw in indexes[1:]:
        index = int(raw)
        if index <= groups[-1][-1] + 2:
            groups[-1].append(index)
        else:
            groups.append([index])
    return [round(sum(group) / len(group)) for group in groups]


def _longest_regular_run(lines: list[int], page_height: int) -> list[int]:
    best: list[int] = []
    minimum = max(8.0, page_height * 0.008)
    maximum = page_height * 0.14
    for start in range(len(lines) - 2):
        first_gap = lines[start + 1] - lines[start]
        if not minimum <= first_gap <= maximum:
            continue
        run = [lines[start], lines[start + 1]]
        gaps = [first_gap]
        for index in range(start + 1, len(lines) - 1):
            gap = lines[index + 1] - lines[index]
            typical = median(gaps)
            if 0.68 * typical <= gap <= 1.32 * typical:
                run.append(lines[index + 1])
                gaps.append(gap)
            else:
                break
        if len(run) > len(best):
            best = run
    return best


def _tesseract_env(tool: Path) -> dict[str, str] | None:
    tessdata = tool.parent / "tessdata"
    if not tessdata.exists():
        return None
    env = os.environ.copy()
    env["TESSDATA_PREFIX"] = str(tessdata)
    return env


def _ocr_grid_page(page, page_no: int, tool: Path, temp: Path) -> list[CoordinateRecord]:
    analysis_zoom = 2.0
    pix = page.get_pixmap(matrix=fitz.Matrix(analysis_zoom, analysis_zoom), alpha=False)
    raw = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    gray = raw[:, :, :3].mean(axis=2) if pix.n >= 3 else raw[:, :, 0]
    dark = gray < 195

    horizontal = _centers(dark.mean(axis=1) > 0.45)
    boundaries = _longest_regular_run(horizontal, pix.height)
    if len(boundaries) < 4:
        horizontal = _centers(dark.mean(axis=1) > 0.35)
        boundaries = _longest_regular_run(horizontal, pix.height)
    if len(boundaries) < 4:
        return []

    body = dark[boundaries[0]:boundaries[-1] + 1, :]
    columns = _centers(body.mean(axis=0) > 0.55)
    if len(columns) < 6:
        columns = _centers(body.mean(axis=0) > 0.35)
    columns = [value for value in columns if pix.width * 0.01 < value < pix.width * 0.99]
    if len(columns) < 6:
        return []

    scale_x = pix.width / page.rect.width
    scale_y = pix.height / page.rect.height
    records: list[CoordinateRecord] = []
    for row_no, (top, bottom) in enumerate(zip(boundaries, boundaries[1:])):
        if bottom - top < 6:
            continue
        clip = fitz.Rect(
            (columns[0] + 2) / scale_x,
            (top + 2) / scale_y,
            (columns[5] - 2) / scale_x,
            (bottom - 2) / scale_y,
        )
        if clip.is_empty:
            continue
        image = temp / f"page_{page_no}_row_{row_no}.png"
        page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=clip, alpha=False).save(image)
        proc = subprocess.run(
            [str(tool), str(image), "stdout", "-l", "rus+eng", "--psm", "6"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_tesseract_env(tool),
            timeout=45,
        )
        if proc.returncode != 0:
            continue
        record = _parse_row(proc.stdout, 0.98)
        if record:
            records.append(record)
    return records


def _grid_records(path: Path) -> list[CoordinateRecord]:
    tool = tesseract_path()
    if tool is None:
        return []
    document = fitz.open(path)
    result: dict[str, CoordinateRecord] = {}
    with tempfile.TemporaryDirectory(prefix="igi_coordinates_") as folder:
        temp = Path(folder)
        for page_no, page in enumerate(document):
            for record in _ocr_grid_page(page, page_no, tool, temp):
                result[record.source_id] = record
    return list(result.values())


def _repair_coordinate_magnitudes(records: list[CoordinateRecord]) -> int:
    """Restore decimal separators occasionally dropped by OCR.

    Coordinates in one statement belong to one survey system and therefore
    have the same order of magnitude.  Only clear 10/100/1000-fold outliers
    are changed; ordinary coordinate variation is left untouched.
    """

    repaired = 0
    for attribute in ("x", "y"):
        magnitudes = [abs(getattr(record, attribute)) for record in records if getattr(record, attribute) != 0]
        if len(magnitudes) < 4:
            continue
        typical = median(magnitudes)
        for record in records:
            value = getattr(record, attribute)
            absolute = abs(value)
            if absolute == 0:
                continue
            replacement = value
            for factor in (10.0, 100.0, 1000.0):
                ratio = absolute / typical
                if 0.75 * factor <= ratio <= 1.25 * factor:
                    replacement = value / factor
                    break
                inverse = typical / absolute
                if 0.75 * factor <= inverse <= 1.25 * factor:
                    replacement = value * factor
                    break
            if replacement != value:
                setattr(record, attribute, replacement)
                record.confidence = min(record.confidence, 0.94)
                repaired += 1
    return repaired


def detect_coordinate_file(path: str | Path) -> tuple[list[CoordinateRecord], list[Issue]]:
    source = Path(path)
    data = read_source(source)
    records = {record.source_id: record for record in detect_coordinate_rows(data)}
    raster_source = source.suffix.lower() in IMAGE_SUFFIXES or any(
        item.layer in {"PDF_OCR", "IMAGE_OCR"} for item in data.texts
    )
    if raster_source:
        grid_records = _grid_records(source)
        if len(grid_records) >= max(3, len(records) // 2):
            records = {record.source_id: record for record in grid_records}
        else:
            for record in grid_records:
                records[record.source_id] = record
    values = sorted(records.values(), key=lambda item: int(item.source_id) if item.source_id.isdigit() else math.inf)
    if not values:
        return [], [Issue("error", "В ведомости координат не распознаны строки скважин с X, Y и отметкой устья.")]
    repaired = _repair_coordinate_magnitudes(values)
    missing_z = sum(record.elevation is None for record in values)
    issues = [Issue("info", f"Ведомость координат: распознано строк скважин — {len(values)}; координаты имеют приоритет над схемой.")]
    if repaired:
        issues.append(Issue("warning", f"OCR восстановил пропущенный десятичный разделитель в {repaired} значениях координат; проверьте эти строки по ведомости."))
    if missing_z:
        issues.append(Issue("warning", f"В ведомости у {missing_z} скважин не распознана отметка Z; она будет взята из колонки или схемы."))
    return values, issues
