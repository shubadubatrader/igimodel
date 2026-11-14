from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .models import AnalysisResult, Borehole, SoilRecord


SOIL_COLUMNS = [
    ("ige", "№ ИГЭ"), ("symbol", "Усл. обозн."), ("name", "Наименование грунта"), ("color", "Цвет"),
    ("deformation_modulus", "Модуль деформации, т/м²"), ("poisson", "Коэффициент Пуассона"),
    ("unit_weight_1", "Удельный вес γI, т/м³"), ("unit_weight_2", "Удельный вес γII, т/м³"),
    ("transition_factor", "Коэффициент перехода ко 2 модулю"), ("moisture", "Природная влажность W"),
    ("liquidity_index", "Показатель текучести IL"), ("plasticity_index", "Число пластичности IP"),
    ("water_flag", "Вода"), ("porosity", "Коэффициент пористости e"),
    ("plant_content", "Растительные остатки q"), ("cohesion_1", "Сцепление cI, т/м²"),
    ("cohesion_2", "Сцепление cII, т/м²"), ("friction_1", "Угол трения φI, град."),
    ("friction_2", "Угол трения φII, град."), ("tensile_strength", "Rs"), ("svintsov", "Co"),
    ("rock_strength", "Rc"), ("strength_reduction", "Ks"), ("subgrade_k", "K, тс/м⁴"),
    ("code", "Код"), ("description", "Описание"),
]

SOIL_PALETTE = {"1": "159D9A", "2": "FFF200", "3": "F59BA6", "4": "9A5B2E", "5": "8B0000"}


def _fmt(value: float | None, digits: int = 3):
    if value is None:
        return ""
    rounded = round(float(value), digits)
    return int(rounded) if rounded.is_integer() else rounded


def borehole_matrix(boreholes: list[Borehole], include_labels: bool = True) -> list[list[object]]:
    max_layers = max((len(item.layers) for item in boreholes), default=0)
    rows: list[list[object]] = []
    base = [
        ("№", [item.display_id for item in boreholes]),
        ("X", [_fmt(item.x) for item in boreholes]),
        ("Y", [_fmt(item.y) for item in boreholes]),
        ("Z", [_fmt(item.elevation) for item in boreholes]),
    ]
    for label, values in base:
        rows.append(([label] if include_labels else []) + values)
    for layer_index in range(max_layers):
        values: list[object] = []
        for item in boreholes:
            if layer_index < len(item.layers):
                layer = item.layers[layer_index]
                values.append(f"{layer.ige} + {_fmt(layer.thickness)}")
            else:
                values.append("")
        rows.append((["ИГЭ + t"] if include_labels else []) + values)
    return rows


def soils_matrix(soils: list[SoilRecord], include_header: bool = True) -> list[list[object]]:
    rows = [[header for _, header in SOIL_COLUMNS]] if include_header else []
    rows.extend([[_fmt(value) if isinstance(value, float) else ("" if value is None else value) for key, _ in SOIL_COLUMNS for value in [getattr(item, key)]] for item in soils])
    return rows


def matrix_tsv(rows: list[list[object]]) -> str:
    def render(value: object) -> str:
        if value is None:
            return ""
        return str(value).replace(".", ",") if isinstance(value, float) else str(value)
    return "\r\n".join("\t".join(render(value) for value in row) for row in rows)


def export_xlsx(result: AnalysisResult, output: str | Path) -> Path:
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    soils_ws = workbook.active
    soils_ws.title = "Характеристики грунтов"
    boreholes_ws = workbook.create_sheet("Скважины")
    for row in soils_matrix(result.soils):
        soils_ws.append(row)
    for row in borehole_matrix(result.boreholes):
        boreholes_ws.append(row)

    header_fill = PatternFill("solid", fgColor="D9EAF7")
    label_fill = PatternFill("solid", fgColor="EAF2F8")
    thin = Side(style="thin", color="A6A6A6")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for cell in soils_ws[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    soils_ws.row_dimensions[1].height = 60
    soils_ws.freeze_panes = "A2"
    soils_ws.auto_filter.ref = soils_ws.dimensions
    for row in soils_ws.iter_rows():
        for cell in row:
            cell.border = border
            if cell.row > 1:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
    color_column = next(index for index, (key, _header) in enumerate(SOIL_COLUMNS, start=1) if key == "color")
    for row_index, soil in enumerate(result.soils, start=2):
        color = soil.color.strip().lstrip("#") if soil.color else SOIL_PALETTE.get(soil.ige, "")
        if len(color) == 6:
            soils_ws.cell(row_index, color_column).fill = PatternFill("solid", fgColor=color.upper())
    for index, (_key, header) in enumerate(SOIL_COLUMNS, start=1):
        width = 28 if header in {"Наименование грунта", "Описание"} else min(22, max(10, len(header) * 0.8))
        soils_ws.column_dimensions[get_column_letter(index)].width = width

    boreholes_ws.freeze_panes = "B1"
    boreholes_ws.column_dimensions["A"].width = 13
    for cell in boreholes_ws[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
    for row in boreholes_ws.iter_rows():
        row[0].font = Font(bold=True)
        row[0].fill = label_fill
        for cell in row:
            cell.border = border
            cell.alignment = Alignment(horizontal="center", vertical="center")
    for index in range(2, boreholes_ws.max_column + 1):
        boreholes_ws.column_dimensions[get_column_letter(index)].width = 12
    workbook.save(output_path)
    return output_path
