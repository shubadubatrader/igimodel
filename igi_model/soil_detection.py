from __future__ import annotations

from collections import defaultdict
import math
import re
from statistics import median

from .detection import SOIL_CODE_NAMES, default_soils, parse_decimal
from .models import DrawingData, Issue, SoilRecord, TextItem


TONNES_PER_M2_PER_MPA = 101.9716213
KPA_PER_TONNE_M2 = 9.80665


def _integer(text: str) -> int | None:
    match = re.fullmatch(r"\s*(\d{1,3})\s*", text)
    return int(match.group(1)) if match else None


def _header_columns(data: DrawingData) -> tuple[float, dict[int, float]] | None:
    numeric = [item for item in data.texts if _integer(item.text) is not None]
    numeric.sort(key=lambda item: item.y, reverse=True)
    groups: list[list[TextItem]] = []
    for item in numeric:
        tolerance = max(2.0, item.height * 0.6)
        if not groups or abs(item.y - median(value.y for value in groups[-1])) > tolerance:
            groups.append([item])
        else:
            groups[-1].append(item)
    candidates: list[tuple[float, dict[int, float]]] = []
    for group in groups:
        by_number: dict[int, list[TextItem]] = defaultdict(list)
        for item in group:
            number = _integer(item.text)
            if number is not None:
                by_number[number].append(item)
        if not all(number in by_number for number in range(1, 19)):
            continue
        for start in by_number[1]:
            columns = {1: start.x}
            previous = start.x
            valid = True
            for number in range(2, 19):
                choices = [item for item in by_number[number] if item.x > previous]
                if not choices:
                    valid = False
                    break
                choice = min(choices, key=lambda item: item.x - previous)
                columns[number] = choice.x
                previous = choice.x
            if valid:
                y = median(item.y for item in group)
                candidates.append((y, columns))
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: candidate[1][18] - candidate[1][1])


def _nearest_value(items: list[TextItem], x: float, y: float, x_tolerance: float, y_tolerance: float) -> float | None:
    choices = []
    for item in items:
        value = parse_decimal(item.text)
        if value is None:
            continue
        dx, dy = abs(item.x - x), abs(item.y - y)
        if dx <= x_tolerance and dy <= y_tolerance:
            choices.append((dx + dy * 0.1, value))
    return min(choices, default=(0.0, None), key=lambda pair: pair[0])[1]


def _short_name(description: str) -> str:
    folded = description.casefold()
    if "техног" in folded or "насып" in folded:
        return "Техногенный (насыпной)"
    kind = ""
    if "суглин" in folded:
        kind = "Суглинок"
    elif "супес" in folded:
        kind = "Супесь"
    elif "глин" in folded:
        kind = "Глина"
    elif "пес" in folded:
        kind = "Песок"
    states = [
        ("мягкопласт", "мягкопластичный"), ("текучепласт", "текучепластичный"),
        ("тугопласт", "тугопластичный"), ("полутверд", "полутвердый"),
        ("тверд", "твердый"), ("пластич", "пластичный"),
    ]
    state = next((label for needle, label in states if needle in folded), "")
    if kind and state:
        if kind == "Глина":
            state = state.replace("ый", "ая").replace("ий", "ичная")
        return f"{kind} {state}"
    if kind:
        return kind
    compact = re.sub(r"\s+", " ", description).strip(" ,.;")
    return compact[:100] or "Грунт"


def _poisson(name: str) -> float:
    folded = name.casefold()
    if "глина" in folded:
        return 0.42
    if "суглин" in folded:
        return 0.35
    return 0.30


def _soil_code(name: str, liquidity: float | None, fallback: str = "") -> str:
    folded = name.casefold()
    value = liquidity if liquidity is not None else 0.0
    if "суглин" in folded or "техног" in folded:
        return "Lh" if value < 0 else "Ls" if value <= 0.5 else "Lp" if value <= 0.75 else "Lf"
    if "глина" in folded:
        return "Ch" if value < 0 else "Cs" if value <= 0.5 else "Cp" if value <= 0.75 else "Cf"
    if "супес" in folded:
        return "Sh" if value < 0 else "Sp"
    return fallback


def _subgrade_k(code: str, liquidity: float | None) -> float | None:
    value = liquidity if liquidity is not None else 0.0
    if code in {"Ls", "Cs"}:
        return round(600.0 - min(max(value, 0.0), 0.5) / 0.5 * 200.0, 1)
    if code in {"Lp", "Cp"}:
        return round(400.0 - min(max(value - 0.5, 0.0), 0.25) / 0.25 * 165.0, 1)
    if code in {"Lh", "Ch"}:
        return 600.0
    return None


def detect_soils(data: DrawingData) -> tuple[list[SoilRecord], list[Issue]]:
    header = _header_columns(data)
    if header is None:
        return [], [Issue("error", "В файле характеристик не найдена таблица с пронумерованными графами 1…18.")]
    header_y, columns = header
    first_gap = max(1.0, columns[2] - columns[1])
    anchors = []
    for item in data.texts:
        number = _integer(item.text)
        if number is None or item.y >= header_y - max(2.0, item.height):
            continue
        if abs(item.x - columns[1]) <= first_gap * 0.45:
            anchors.append((str(number), item))
    anchors.sort(key=lambda pair: pair[1].y, reverse=True)
    if not anchors:
        return [], [Issue("error", "Не найдены строки ИГЭ под заголовком таблицы характеристик.")]

    seed = {item.ige: item for item in default_soils()}
    row_gaps = [abs(a[1].y - b[1].y) for a, b in zip(anchors, anchors[1:]) if abs(a[1].y - b[1].y) > 1]
    typical_gap = median(row_gaps) if row_gaps else max(100.0, anchors[0][1].height * 8)
    soils: list[SoilRecord] = []
    issues: list[Issue] = []
    for index, (ige, anchor) in enumerate(anchors):
        upper = (header_y + anchor.y) / 2 if index == 0 else (anchors[index - 1][1].y + anchor.y) / 2
        lower = anchor.y - typical_gap / 2 if index == len(anchors) - 1 else (anchor.y + anchors[index + 1][1].y) / 2
        row_items = [item for item in data.texts if lower <= item.y <= upper]
        col3_left = (columns[2] + columns[3]) / 2
        col3_right = (columns[3] + columns[4]) / 2
        description_items = [item for item in row_items if col3_left <= item.x <= col3_right and not item.text.strip().isdigit()]
        description_items.sort(key=lambda item: item.y, reverse=True)
        description = re.sub(r"\s+", " ", " ".join(item.text.strip() for item in description_items)).strip()
        name = _short_name(description)
        x_tolerance = max(15.0, median(columns[n + 1] - columns[n] for n in range(5, 18)) * 0.42)
        y_tolerance = max(20.0, typical_gap * 0.2)
        moisture_pct = _nearest_value(row_items, columns[6], anchor.y, x_tolerance, y_tolerance)
        plasticity_pct = _nearest_value(row_items, columns[9], anchor.y, x_tolerance, y_tolerance)
        liquidity = _nearest_value(row_items, columns[10], anchor.y, x_tolerance, y_tolerance)
        if liquidity is None:
            relational_zero = [
                item for item in row_items
                if abs(item.x - columns[10]) <= x_tolerance
                and abs(item.y - anchor.y) <= y_tolerance
                and re.search(r"<\s*0", item.text)
            ]
            if relational_zero:
                liquidity = 0.0
        density = _nearest_value(row_items, columns[11], anchor.y, x_tolerance, y_tolerance)
        porosity = _nearest_value(row_items, columns[14], anchor.y, x_tolerance, y_tolerance)
        phi = _nearest_value(row_items, columns[18], anchor.y, x_tolerance, y_tolerance)
        source_e = _nearest_value(row_items, columns[16], anchor.y, x_tolerance, y_tolerance)
        e_candidates = []
        c_candidates = []
        for item in row_items:
            value = parse_decimal(item.text)
            if value is None:
                continue
            if abs(item.x - columns[16]) <= x_tolerance and value > 100:
                e_candidates.append(value)
            if abs(item.x - columns[17]) <= x_tolerance and 0 < value < 15:
                c_candidates.append(value)
        deformation = max(e_candidates) if e_candidates else (round(source_e * TONNES_PER_M2_PER_MPA) if source_e is not None else None)
        source_c = _nearest_value(row_items, columns[17], anchor.y, x_tolerance, y_tolerance)
        converted_c = round(source_c / KPA_PER_TONNE_M2, 2) if source_c is not None else None
        cohesion = min(c_candidates, key=lambda value: abs(value - converted_c)) if c_candidates and converted_c is not None else converted_c
        symbol_candidates = [item for item in row_items if columns[1] < item.x < col3_left and not item.text.strip().isdigit()]
        symbol = min(symbol_candidates, key=lambda item: abs(item.x - columns[2]), default=None)
        fallback = seed.get(ige)
        code = _soil_code(name, liquidity, fallback.code if fallback else "")
        k_value = _subgrade_k(code, liquidity)
        record = SoilRecord(
            ige=ige, symbol=symbol.text.strip() if symbol else "", name=name,
            deformation_modulus=deformation, poisson=_poisson(name),
            unit_weight_1=density, unit_weight_2=density, transition_factor=5,
            moisture=None if moisture_pct is None else round(moisture_pct / 100.0, 2),
            liquidity_index=liquidity,
            plasticity_index=None if plasticity_pct is None else round(plasticity_pct / 100.0, 3),
            water_flag="W", porosity=porosity,
            cohesion_1=cohesion, cohesion_2=cohesion, friction_1=phi, friction_2=phi,
            tensile_strength=fallback.tensile_strength if fallback else None,
            svintsov=0, subgrade_k=k_value, code=code,
            description=description or SOIL_CODE_NAMES.get(code, name),
        )
        if record.deformation_modulus is None or record.unit_weight_1 is None or record.cohesion_1 is None or record.friction_1 is None:
            issues.append(Issue("warning", f"ИГЭ {ige}: распознаны не все механические характеристики."))
        if not code:
            issues.append(Issue("warning", f"ИГЭ {ige}: не определён код грунта для расчёта K."))
        soils.append(record)
    return soils, issues
