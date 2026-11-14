from __future__ import annotations

import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import ezdxf
from ezdxf.disassemble import recursive_decompose

from .models import DrawingData, Polyline, TextItem


def resource_root() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parent.parent


def _dwg2dxf_path() -> Path:
    candidates = [
        resource_root() / "vendor" / "libredwg" / "dwg2dxf.exe",
        Path(sys.executable).resolve().parent / "vendor" / "libredwg" / "dwg2dxf.exe",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(
        "Не найден локальный конвертер DWG. Ожидался vendor/libredwg/dwg2dxf.exe"
    )


def convert_dwg(source: Path, target: Path) -> Path:
    tool = _dwg2dxf_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PATH"] = str(tool.parent) + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(
        [str(tool), "-o", str(target), str(source)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=180,
    )
    if proc.returncode != 0 or not target.exists():
        details = (proc.stderr or proc.stdout).strip()[-1200:]
        raise RuntimeError(f"Не удалось прочитать DWG ({proc.returncode}): {details}")
    return target


def _text_value(entity) -> str:
    if entity.dxftype() == "MTEXT":
        return entity.plain_text().replace("\\P", " ").strip()
    return str(entity.dxf.get("text", "")).strip()


def _insert_xy(entity) -> tuple[float, float]:
    point = entity.dxf.get("insert", (0.0, 0.0, 0.0))
    return float(point.x), float(point.y)


def _add_virtual_entities(data: DrawingData, insert) -> None:
    try:
        virtuals = insert.virtual_entities()
    except Exception:
        return
    for entity in virtuals:
        kind = entity.dxftype()
        if kind in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
            text = _text_value(entity)
            if text:
                x, y = _insert_xy(entity)
                data.texts.append(TextItem(text, x, y, entity.dxf.get("layer", "")))


def read_dxf(path: Path) -> DrawingData:
    doc = ezdxf.readfile(path)
    data = DrawingData(source=path)
    modelspace = doc.modelspace()
    for insert in modelspace.query("INSERT"):
        x, y = _insert_xy(insert)
        data.marker_points.append((x, y, str(insert.dxf.get("layer", "")), 0))
    for entity in recursive_decompose(modelspace):
        kind = entity.dxftype()
        layer = str(entity.dxf.get("layer", ""))
        if kind in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
            text = _text_value(entity)
            if text:
                x, y = _insert_xy(entity)
                height = float(entity.dxf.get("char_height", 1.0) if kind == "MTEXT" else entity.dxf.get("height", 1.0))
                data.texts.append(TextItem(text, x, y, layer, height))
        elif kind == "LWPOLYLINE":
            pts = [(float(x), float(y)) for x, y in entity.get_points("xy")]
            if entity.closed and pts and pts[0] != pts[-1]:
                pts.append(pts[0])
            if len(pts) >= 2:
                data.polylines.append(Polyline(pts, layer))
        elif kind == "POLYLINE":
            pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
            if entity.is_closed and pts and pts[0] != pts[-1]:
                pts.append(pts[0])
            if len(pts) >= 2:
                data.polylines.append(Polyline(pts, layer))
        elif kind == "LINE":
            a, b = entity.dxf.start, entity.dxf.end
            data.polylines.append(Polyline([(float(a.x), float(a.y)), (float(b.x), float(b.y))], layer))
        elif kind in {"POINT", "CIRCLE", "ARC"}:
            point = entity.dxf.get("location", None) if kind == "POINT" else entity.dxf.get("center", None)
            if point is not None:
                data.marker_points.append((float(point.x), float(point.y), layer, 0))
    return data


def read_cad(path: str | Path) -> DrawingData:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".dxf":
        return read_dxf(source)
    if suffix != ".dwg":
        raise ValueError(f"Неподдерживаемый CAD-формат: {source.suffix}")
    with tempfile.TemporaryDirectory(prefix="igi_dwg_") as temp:
        converted = Path(temp) / f"{source.stem}.dxf"
        result = read_dxf(convert_dwg(source, converted))
        result.source = source
        return result


def polyline_length(polyline: Polyline) -> float:
    return sum(math.dist(a, b) for a, b in zip(polyline.points, polyline.points[1:]))
