from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import math
import re
from statistics import median

from .cad_reader import polyline_length
from .models import (
    AnalysisResult, Borehole, CoordinateConfig, CoordinateRecord, DetectionProfile, DrawingData,
    Issue, LayerRecord, Polyline, SoilRecord, TextItem,
)


DECIMAL_RE = re.compile(r"^[\s+\-]*(\d+(?:[.,]\d+)?)\s*$")

SOIL_CODE_NAMES = {
    "Z4": "Плотный песок гравелистый", "Z3": "Плотный песок крупный",
    "Z2": "Плотный песок средний", "Z1": "Плотный песок мелкий", "Z0": "Плотный песок пылеватый",
    "S4": "Песок гравелистый", "S3": "Песок крупный", "S2": "Песок средний",
    "S1": "Песок мелкий", "S0": "Песок пылеватый",
    "Sh": "Супесь твердая", "Sp": "Супесь пластичная",
    "Lh": "Суглинок твердый", "Ls": "Суглинок тугопластичный или полутвердый",
    "Lp": "Суглинок мягкопластичный", "Lf": "Суглинок текучепластичный",
    "Ch": "Глина твердая", "Cs": "Глина тугопластичная или полутвердая",
    "Cp": "Глина мягкопластичная", "Cf": "Глина текучепластичная",
}


def parse_decimal(value: str) -> float | None:
    match = DECIMAL_RE.match(value.replace("\u00a0", " "))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def normalize_id(value: str) -> str:
    return re.sub(r"[^0-9a-zа-я]", "", value.lower(), flags=re.I)


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _segment_intersection(a, b, c, d):
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-10:
        return None
    qx, qy = c[0] - a[0], c[1] - a[1]
    t = (qx * sy - qy * sx) / den
    u = (qx * ry - qy * rx) / den
    if -1e-7 <= t <= 1.0000001 and -1e-7 <= u <= 1.0000001:
        return a[0] + t * rx, a[1] + t * ry
    return None


def _intersection_candidates(polylines: list[Polyline]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    useful = [p for p in polylines if len(p.points) >= 2 and polyline_length(p) > 3]
    for index, first in enumerate(useful):
        for second in useful[index + 1:]:
            if first.page != second.page:
                continue
            for a, b in zip(first.points, first.points[1:]):
                for c, d in zip(second.points, second.points[1:]):
                    hit = _segment_intersection(a, b, c, d)
                    if hit is not None and not any(_distance(hit, old) < 0.15 for old in result):
                        result.append(hit)
    return result


def _compile(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(pattern, re.I) for pattern in patterns]


def _find_labels(data: DrawingData, profile: DetectionProfile) -> list[tuple[str, TextItem]]:
    patterns = _compile(profile.borehole_patterns)
    labels: list[tuple[str, TextItem]] = []
    for item in data.texts:
        for pattern in patterns:
            match = pattern.search(item.text)
            if match:
                labels.append((normalize_id(match.group(1)), item))
                break
    unique: dict[str, TextItem] = {}
    for source_id, item in labels:
        if source_id and source_id not in unique:
            unique[source_id] = item
    return list(unique.items())


def _nearby_elevation(label: TextItem, texts: list[TextItem]) -> float | None:
    choices: list[tuple[float, float]] = []
    search_radius = max(15.0, label.height * 15)
    for item in texts:
        if item.page != label.page or item is label:
            continue
        value = parse_decimal(item.text)
        if value is None or not (-1000 < value < 10000):
            continue
        distance = _distance((label.x, label.y), (item.x, item.y))
        if distance <= search_radius:
            score = distance * (0.05 if item.layer == label.layer else 1.0)
            if 50 <= value <= 500:
                score *= 0.55
            choices.append((score, value))
    return min(choices, default=(0.0, None), key=lambda pair: pair[0])[1]


def detect_plan(data: DrawingData, profile: DetectionProfile) -> tuple[list[Borehole], list[Issue]]:
    issues: list[Issue] = []
    labels = _find_labels(data, profile)
    if not labels:
        return [], [Issue("error", "На плане не найдены подписи скважин. Проверьте шаблоны в профиле.")]

    relevant_layers = {item.layer for _, item in labels}
    relevant_polys = [poly for poly in data.polylines if poly.layer in relevant_layers]
    candidates = _intersection_candidates(relevant_polys)
    if len(candidates) < len(labels):
        for x, y, layer, _page in data.marker_points:
            if layer in relevant_layers and not any(_distance((x, y), old) < 0.15 for old in candidates):
                candidates.append((x, y))

    assignments: dict[str, tuple[float, float, float]] = {}
    pairs: list[tuple[float, str, tuple[float, float]]] = []
    for source_id, label in labels:
        for point in candidates:
            distance = _distance((label.x, label.y), point)
            if distance <= profile.max_label_distance:
                pairs.append((distance, source_id, point))
    used_points: set[tuple[float, float]] = set()
    for distance, source_id, point in sorted(pairs, key=lambda row: row[0]):
        if source_id not in assignments and point not in used_points:
            assignments[source_id] = (point[0], point[1], distance)
            used_points.add(point)

    boreholes: list[Borehole] = []
    for source_id, label in labels:
        assigned = assignments.get(source_id)
        if assigned is None:
            x, y, confidence = label.x, label.y, 0.35
            issues.append(Issue("warning", f"{source_id}: геометрический центр не найден, использована точка подписи."))
        else:
            x, y, distance = assigned
            confidence = max(0.45, min(0.98, 1.0 - distance / max(profile.max_label_distance, 1.0)))
        boreholes.append(Borehole(
            source_id=source_id, display_id=source_id, x_global=x, y_global=y,
            elevation=_nearby_elevation(label, data.texts), confidence=confidence,
        ))
    return boreholes, issues


def _cluster_x(items: list[TextItem], tolerance: float) -> list[list[TextItem]]:
    clusters: list[list[TextItem]] = []
    for item in sorted(items, key=lambda value: value.x):
        if not clusters or abs(item.x - sum(v.x for v in clusters[-1]) / len(clusters[-1])) > tolerance:
            clusters.append([item])
        else:
            clusters[-1].append(item)
    return clusters


def _numeric_sequence(cluster: list[TextItem], max_value: float = 1000.0) -> list[tuple[TextItem, float]]:
    values = [(item, parse_decimal(item.text)) for item in cluster]
    return [(item, value) for item, value in values if value is not None and 0 <= value <= max_value]


def _column_layers(items: list[TextItem], heading_x: float) -> tuple[list[LayerRecord], str | None]:
    if not items:
        return [], "нет данных под заголовком"
    typical_height = median([max(item.height, 0.1) for item in items])
    tolerance = max(1.0, typical_height * 0.75)
    clusters = [cluster for cluster in _cluster_x(items, tolerance) if len(cluster) >= 2]
    numeric = [(cluster, _numeric_sequence(cluster, 100.0)) for cluster in clusters]
    numeric = [(cluster, seq) for cluster, seq in numeric if 2 <= len(seq) <= 60]

    best: tuple[float, list[tuple[TextItem, float]], list[tuple[TextItem, float]]] | None = None
    for depth_cluster, depth_seq_raw in numeric:
        depth_seq = sorted(depth_seq_raw, key=lambda row: row[0].y, reverse=True)
        depths = [row[1] for row in depth_seq]
        if sum(b > a for a, b in zip(depths, depths[1:])) < max(1, len(depths) - 2):
            continue
        for thick_cluster, thick_seq_raw in numeric:
            if thick_cluster is depth_cluster or len(thick_seq_raw) != len(depth_seq):
                continue
            thick_seq = sorted(thick_seq_raw, key=lambda row: row[0].y, reverse=True)
            thicknesses = [row[1] for row in thick_seq]
            if any(value <= 0 for value in thicknesses):
                continue
            running = 0.0
            errors = []
            for depth, thickness in zip(depths, thicknesses):
                running += thickness
                errors.append(abs(depth - running))
            error = sum(errors) / len(errors)
            x_penalty = abs(sum(v.x for v in depth_cluster) / len(depth_cluster) - heading_x) * 0.002
            score = error + x_penalty
            if error <= 0.35 and (best is None or score < best[0]):
                best = score, depth_seq, thick_seq
    if best is None:
        return [], "не удалось определить столбцы глубин и мощностей"

    _score, depth_seq, thick_seq = best
    count = len(depth_seq)
    depth_items = {id(item) for item, _value in depth_seq}
    thick_items = {id(item) for item, _value in thick_seq}
    header_x = [
        item.x for item in items
        if "номер" in item.text.casefold() and "игэ" in item.text.casefold()
    ]
    layer_candidates: list[tuple[float, list[TextItem]]] = []
    for cluster in clusters:
        code_items = [
            item for item in cluster
            if re.fullmatch(r"\d{1,3}[a-zа-я]?(?:[.\-/]\d+)?", item.text.strip(), re.I)
        ]
        if len(code_items) < max(2, count // 2):
            continue
        if any(id(item) in depth_items or id(item) in thick_items for item in code_items):
            continue
        center_x = sum(item.x for item in code_items) / len(code_items)
        ordered = sorted(code_items, key=lambda item: item.y, reverse=True)
        tokens = [item.text.strip() for item in ordered]
        if all(0 < len(token) <= 16 for token in tokens):
            integer_like = sum(bool(re.fullmatch(r"[0-9a-zа-я._\-/]+", token, re.I)) for token in tokens)
            small_codes = sum(bool(re.fullmatch(r"\d{1,3}[a-zа-я]?(?:[.\-/]\d+)?", token, re.I)) for token in tokens)
            if integer_like >= count * 0.8 and small_codes >= count * 0.8:
                numeric_values = [parse_decimal(token) for token in tokens]
                monotonic_penalty = sum(
                    b is not None and a is not None and b > a
                    for a, b in zip(numeric_values, numeric_values[1:])
                )
                header_distance = min((abs(center_x - x) for x in header_x), default=abs(center_x - heading_x))
                count_penalty = abs(len(code_items) - count) * 20
                layer_candidates.append((header_distance + monotonic_penalty * 5 + count_penalty, ordered))
    if not layer_candidates:
        codes = ["?" for _ in range(count)]
        warning = "не найден столбец ИГЭ"
    else:
        code_items = min(layer_candidates, key=lambda row: row[0])[1]
        first_depth, last_depth = depth_seq[0][1], depth_seq[-1][1]
        first_y, last_y = depth_seq[0][0].y, depth_seq[-1][0].y
        scale_y = (first_y - last_y) / max(last_depth - first_depth, 1e-9)
        top_y = first_y + scale_y * first_depth
        codes = []
        previous_depth = 0.0
        for (_depth_item, depth), (_thick_item, thickness) in zip(depth_seq, thick_seq):
            middle_depth = previous_depth + thickness / 2
            expected_y = top_y - scale_y * middle_depth
            choice = min(code_items, key=lambda item: abs(item.y - expected_y))
            codes.append(choice.text.strip())
            previous_depth = depth
        warning = None
    layers = [
        LayerRecord(code, float(thick[1]), float(depth[1]))
        for code, depth, thick in zip(codes, depth_seq, thick_seq)
    ]
    return layers, warning


def _heading_matches(data: DrawingData, profile: DetectionProfile) -> list[tuple[str, TextItem]]:
    patterns = _compile(profile.heading_patterns)
    result: list[tuple[str, TextItem]] = []
    for item in data.texts:
        for pattern in patterns:
            match = pattern.search(item.text)
            if match:
                result.append((normalize_id(match.group(1)), item))
                break
    return result


def detect_columns(data: DrawingData, profile: DetectionProfile) -> tuple[list[Borehole], list[Issue]]:
    headings = _heading_matches(data, profile)
    if not headings:
        return [], [Issue("error", "В колонках не найдены заголовки скважин.")]
    issues: list[Issue] = []
    result: list[Borehole] = []
    pages: dict[int, list[tuple[str, TextItem]]] = defaultdict(list)
    for source_id, heading in headings:
        pages[heading.page].append((source_id, heading))
    for page, page_headings in pages.items():
        page_headings.sort(key=lambda row: row[1].y, reverse=True)
        bands: list[list[tuple[str, TextItem]]] = []
        for row in page_headings:
            tolerance = max(5.0, row[1].height * 4)
            if not bands or abs(row[1].y - median(item[1].y for item in bands[-1])) > tolerance:
                bands.append([row])
            else:
                bands[-1].append(row)
        band_y = [median(item[1].y for item in band) for band in bands]
        band_gaps = [abs(a - b) for a, b in zip(band_y, band_y[1:])]
        typical_y_gap = median(band_gaps) if band_gaps else 500.0
        for band_index, band in enumerate(bands):
            band.sort(key=lambda row: row[1].x)
            x_gaps = [b[1].x - a[1].x for a, b in zip(band, band[1:]) if b[1].x > a[1].x]
            typical_x_gap = median(x_gaps) if x_gaps else 210.0
            next_y = band_y[band_index + 1] if band_index + 1 < len(bands) else band_y[band_index] - typical_y_gap
            lower_y = next_y + max(2.0, min(15.0, typical_y_gap * 0.03))
            for index, (source_id, heading) in enumerate(band):
                gap = band[index + 1][1].x - heading.x if index + 1 < len(band) else typical_x_gap
                left_offset = gap * 0.30
                left = heading.x - left_offset
                right = heading.x + gap - left_offset
                items = [
                    item for item in data.texts
                    if item.page == page and left <= item.x < right and lower_y < item.y < heading.y
                ]
                layers, warning = _column_layers(items, heading.x)
                if warning:
                    issues.append(Issue("warning", f"Скважина {source_id}: {warning}."))
                elevation = None
                for item in items:
                    if re.search(r"усть|отмет", item.text, re.I):
                        numbers = re.findall(r"[-+]?\d+(?:[.,]\d+)?", item.text)
                        if numbers:
                            elevation = float(numbers[-1].replace(",", "."))
                            break
                result.append(Borehole(
                    source_id=source_id, display_id=source_id, elevation=elevation,
                    layers=layers, confidence=0.9 if layers else 0.45,
                ))
    return result, issues


def _ids_match(first: str, second: str) -> bool:
    a, b = normalize_id(first), normalize_id(second)
    if a == b:
        return True
    digits_a = "".join(re.findall(r"\d", a))
    digits_b = "".join(re.findall(r"\d", b))
    return bool(digits_a and digits_b and (digits_a.endswith(digits_b) or digits_b.endswith(digits_a)))


def apply_coordinate_statement(
    plan: list[Borehole],
    records: list[CoordinateRecord],
    profile: DetectionProfile,
) -> tuple[list[Borehole], list[Issue]]:
    """Use statement coordinates and retain plan geometry as a control source."""

    issues: list[Issue] = []
    matched_pairs: list[tuple[Borehole, CoordinateRecord]] = []
    for record in records:
        plan_item = next((item for item in plan if _ids_match(item.source_id, record.source_id)), None)
        if plan_item:
            matched_pairs.append((plan_item, record))

    swap_xy = profile.statement_swap_xy
    if matched_pairs:
        direct_errors = [
            _distance((item.x_global, item.y_global), (record.x, record.y))
            for item, record in matched_pairs
            if item.x_global is not None and item.y_global is not None
        ]
        swapped_errors = [
            _distance((item.x_global, item.y_global), (record.y, record.x))
            for item, record in matched_pairs
            if item.x_global is not None and item.y_global is not None
        ]
        if direct_errors and swapped_errors:
            swap_xy = median(swapped_errors) <= median(direct_errors)

    orientation = "X ведомости соответствует CAD Y, Y ведомости соответствует CAD X" if swap_xy else "оси X/Y ведомости совпадают с CAD"
    issues.append(Issue("info", f"Ведомость координат: {orientation}."))

    used_plan: set[int] = set()
    merged: list[Borehole] = []
    for record in records:
        x_global, y_global = (record.y, record.x) if swap_xy else (record.x, record.y)
        match_index = next((
            index for index, item in enumerate(plan)
            if index not in used_plan and _ids_match(item.source_id, record.source_id)
        ), None)
        if match_index is not None:
            used_plan.add(match_index)
            plan_item = plan[match_index]
            if plan_item.x_global is not None and plan_item.y_global is not None:
                difference = _distance((x_global, y_global), (plan_item.x_global, plan_item.y_global))
                if difference > profile.coordinate_control_tolerance:
                    issues.append(Issue(
                        "warning",
                        f"{record.source_id}: координаты ведомости и схемы различаются на {difference:.2f} м; взята ведомость.",
                    ))
            if record.elevation is not None and plan_item.elevation is not None and abs(record.elevation - plan_item.elevation) > 0.05:
                issues.append(Issue(
                    "warning",
                    f"{record.source_id}: отметки ведомости ({record.elevation:g}) и схемы ({plan_item.elevation:g}) различаются; взята ведомость.",
                ))
        elif plan:
            issues.append(Issue("warning", f"{record.source_id}: строка есть в ведомости, но точка на схеме не найдена."))
        merged.append(Borehole(
            source_id=record.source_id,
            display_id=record.source_id,
            x_global=x_global,
            y_global=y_global,
            elevation=record.elevation,
            confidence=record.confidence,
            coordinate_source="statement",
        ))

    for index, item in enumerate(plan):
        if index not in used_plan:
            issues.append(Issue("warning", f"{item.source_id}: строки в ведомости нет; координаты взяты со схемы."))
            merged.append(item)
    return merged, issues


def _display_ids(boreholes: list[Borehole], strip: bool) -> None:
    numeric = ["".join(re.findall(r"\d", item.source_id)) for item in boreholes]
    if not strip or not numeric or any(not value for value in numeric):
        return
    prefix = numeric[0]
    for value in numeric[1:]:
        while prefix and not value.startswith(prefix):
            prefix = prefix[:-1]
    if len(prefix) < 2 or any(len(value) == len(prefix) for value in numeric):
        return
    for item, value in zip(boreholes, numeric):
        suffix = value[len(prefix):]
        item.display_id = str(int(suffix)) if suffix.isdigit() else suffix


def _dominant_rotation(polylines: list[Polyline]) -> float:
    angles: list[tuple[float, float]] = []
    for poly in polylines:
        for a, b in zip(poly.points, poly.points[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            if length < 3:
                continue
            angle = math.degrees(math.atan2(dy, dx))
            while angle >= 45:
                angle -= 90
            while angle < -45:
                angle += 90
            angles.append((angle, length))
    if not angles:
        return 0.0
    expanded: list[float] = []
    max_length = max(length for _, length in angles)
    for angle, length in angles:
        expanded.extend([angle] * max(1, round(20 * length / max_length)))
    return -median(expanded)


def _rotation_from_points(points: list[tuple[float, float]]) -> float | None:
    if len(points) < 4:
        return None
    nearest_distances = []
    for index, point in enumerate(points):
        distances = sorted(_distance(point, other) for j, other in enumerate(points) if j != index)
        if distances:
            nearest_distances.append(distances[0])
    if not nearest_distances:
        return None
    limit = median(nearest_distances) * 1.45
    angles: list[float] = []
    for index, first in enumerate(points):
        for second in points[index + 1:]:
            dx, dy = second[0] - first[0], second[1] - first[1]
            length = math.hypot(dx, dy)
            if length > limit or length < 1e-6:
                continue
            angle = math.degrees(math.atan2(dy, dx))
            while angle >= 45:
                angle -= 90
            while angle < -45:
                angle += 90
            angles.append(angle)
    return -median(angles) if angles else None


def _point_segment_distance(point, a, b) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return _distance(point, a)
    t = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length_sq))
    projection = (a[0] + t * dx, a[1] + t * dy)
    return _distance(point, projection)


def configure_coordinates(
    boreholes: list[Borehole],
    data: DrawingData,
    requested: CoordinateConfig,
    layer_hints: list[str] | None = None,
) -> CoordinateConfig:
    config = replace(requested)
    if config.angle_deg is None:
        centers = [
            (item.x_global, item.y_global) for item in boreholes
            if item.x_global is not None and item.y_global is not None
        ]
        hints = [hint.casefold() for hint in (layer_hints or [])]
        candidate_polys = [
            poly for poly in data.polylines
            if any(hint in poly.layer.casefold() for hint in hints)
        ]
        long_curves = [
            poly for poly in candidate_polys
            if len(poly.points) >= 4 and polyline_length(poly) > 10
        ]
        if len(long_curves) >= 2:
            config.angle_deg = _dominant_rotation(long_curves)
        else:
            config.angle_deg = _rotation_from_points(centers)
        if not candidate_polys:
            candidate_polys = [
                poly for poly in data.polylines
                if any(
                    min((_point_segment_distance(point, a, b) for a, b in zip(poly.points, poly.points[1:])), default=math.inf) < 0.5
                    for point in centers
                )
            ]
        if config.angle_deg is None:
            config.angle_deg = _dominant_rotation(candidate_polys or data.polylines)
    angle = math.radians(config.angle_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    points = [(item.x_global, item.y_global) for item in boreholes if item.x_global is not None and item.y_global is not None]
    if points and (config.origin_x is None or config.origin_y is None):
        rotated = [(cos_a * x - sin_a * y, sin_a * x + cos_a * y) for x, y in points]
        step = max(config.grid_round, 0.001)
        offset_u = math.floor(min(x for x, _ in rotated) / step) * step
        offset_v = math.floor(min(y for _, y in rotated) / step) * step
        config.origin_x = cos_a * offset_u + sin_a * offset_v
        config.origin_y = -sin_a * offset_u + cos_a * offset_v
    return config


def apply_coordinates(boreholes: list[Borehole], config: CoordinateConfig) -> None:
    angle = math.radians(config.angle_deg or 0.0)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    origin_x, origin_y = config.origin_x or 0.0, config.origin_y or 0.0
    for item in boreholes:
        if item.x_global is None or item.y_global is None:
            continue
        dx, dy = item.x_global - origin_x, item.y_global - origin_y
        item.x = config.scale * (cos_a * dx - sin_a * dy)
        item.y = config.scale * (sin_a * dx + cos_a * dy)


def merge_boreholes(plan: list[Borehole], columns: list[Borehole]) -> tuple[list[Borehole], list[Issue]]:
    issues: list[Issue] = []
    merged: list[Borehole] = []
    used: set[int] = set()
    for plan_item in plan:
        match_index = next((i for i, col in enumerate(columns) if i not in used and _ids_match(plan_item.source_id, col.source_id)), None)
        if match_index is None:
            issues.append(Issue("warning", f"{plan_item.source_id}: инженерно-геологическая колонка не найдена."))
            merged.append(plan_item)
            continue
        used.add(match_index)
        column = columns[match_index]
        statement_priority = plan_item.coordinate_source == "statement" and plan_item.elevation is not None
        elevation = plan_item.elevation if statement_priority else (column.elevation if column.elevation is not None else plan_item.elevation)
        if column.elevation is not None and plan_item.elevation is not None and abs(column.elevation - plan_item.elevation) > 0.05:
            if statement_priority:
                issues.append(Issue("warning", f"{plan_item.source_id}: отметки ведомости ({plan_item.elevation:g}) и колонки ({column.elevation:g}) различаются; взята отметка ведомости."))
            else:
                issues.append(Issue("warning", f"{plan_item.source_id}: отметки плана ({plan_item.elevation:g}) и колонки ({column.elevation:g}) различаются; взята отметка колонки."))
        plan_item.elevation = elevation
        plan_item.layers = column.layers
        plan_item.confidence = min(plan_item.confidence, column.confidence)
        merged.append(plan_item)
    for index, column in enumerate(columns):
        if index not in used:
            issues.append(Issue("warning", f"{column.source_id}: есть колонка, но нет точки на плане."))
    return merged, issues


def default_soils() -> list[SoilRecord]:
    return [
        SoilRecord("1", "", "Техногенный (насыпной)", deformation_modulus=1427, poisson=0.30, unit_weight_1=1.80, unit_weight_2=1.80, transition_factor=5, moisture=0.25, liquidity_index=0.50, water_flag="W", porosity=0.85, cohesion_1=3.06, cohesion_2=3.06, friction_1=20, friction_2=20, tensile_strength=0.10, svintsov=0, subgrade_k=400, code="Ls", description="Суглинок тугопластичный или полутвердый IL=0...0.5"),
        SoilRecord("2", "", "Суглинок мягкопластичный", deformation_modulus=1529, poisson=0.35, unit_weight_1=1.89, unit_weight_2=1.89, transition_factor=5, moisture=0.25, liquidity_index=0.59, water_flag="W", porosity=0.77, cohesion_1=1.53, cohesion_2=1.53, friction_1=23, friction_2=23, tensile_strength=0.02, svintsov=0, subgrade_k=340.6, code="Lp", description="Суглинок мягкопластичный IL=0.5...0.75"),
        SoilRecord("3", "", "Суглинок полутвердый", deformation_modulus=1631, poisson=0.35, unit_weight_1=1.96, unit_weight_2=1.96, transition_factor=5, moisture=0.23, liquidity_index=0.12, water_flag="W", porosity=0.69, cohesion_1=3.26, cohesion_2=3.26, friction_1=22, friction_2=22, tensile_strength=0.16, svintsov=0, subgrade_k=552, code="Ls", description="Суглинок полутвердый"),
        SoilRecord("4", "", "Суглинок мягкопластичный", deformation_modulus=1529, poisson=0.35, unit_weight_1=1.91, unit_weight_2=1.91, transition_factor=5, moisture=0.25, liquidity_index=0.53, water_flag="W", porosity=0.76, cohesion_1=2.04, cohesion_2=2.04, friction_1=23, friction_2=23, tensile_strength=0.40, svintsov=0, subgrade_k=380.2, code="Lp", description="Суглинок мягкопластичный"),
        SoilRecord("5", "", "Глина твердая", deformation_modulus=2039, poisson=0.42, unit_weight_1=1.95, unit_weight_2=1.95, transition_factor=5, moisture=0.24, liquidity_index=0.0, water_flag="W", porosity=0.73, cohesion_1=7.3, cohesion_2=7.3, friction_1=16, friction_2=16, tensile_strength=1.0, svintsov=0, subgrade_k=600, code="Cs", description="Глина тугопластичная или полутвердая IL=0...0.5"),
    ]


def analyze(
    plan_data: DrawingData | None,
    columns_data: DrawingData,
    profile: DetectionProfile | None = None,
    soils: list[SoilRecord] | None = None,
    coordinate_records: list[CoordinateRecord] | None = None,
) -> AnalysisResult:
    profile = profile or DetectionProfile()
    plan, plan_issues = detect_plan(plan_data, profile) if plan_data is not None else ([], [])
    statement_issues: list[Issue] = []
    if coordinate_records:
        plan, statement_issues = apply_coordinate_statement(plan, coordinate_records, profile)
    columns, column_issues = detect_columns(columns_data, profile)
    boreholes, merge_issues = merge_boreholes(plan, columns)
    _display_ids(boreholes, profile.strip_common_id_prefix)
    coordinate_drawing = plan_data or DrawingData()
    config = configure_coordinates(boreholes, coordinate_drawing, profile.coordinate, profile.layer_hints)
    apply_coordinates(boreholes, config)
    boreholes.sort(key=lambda item: (int(item.display_id) if item.display_id.isdigit() else math.inf, item.display_id))
    issues = plan_issues + statement_issues + column_issues + merge_issues
    if any(item.x is None or item.y is None for item in boreholes):
        issues.append(Issue("error", "У части скважин не определены координаты."))
    if any(not item.layers for item in boreholes):
        issues.append(Issue("warning", "У части скважин не распознаны слои; проверьте таблицу перед экспортом."))
    return AnalysisResult(boreholes, soils or default_soils(), config, issues)
