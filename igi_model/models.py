from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class TextItem:
    text: str
    x: float
    y: float
    layer: str = ""
    height: float = 1.0
    page: int = 0


@dataclass(slots=True)
class Polyline:
    points: list[tuple[float, float]]
    layer: str = ""
    page: int = 0


@dataclass(slots=True)
class DrawingData:
    texts: list[TextItem] = field(default_factory=list)
    polylines: list[Polyline] = field(default_factory=list)
    marker_points: list[tuple[float, float, str, int]] = field(default_factory=list)
    source: Path | None = None


@dataclass(slots=True)
class LayerRecord:
    ige: str
    thickness: float
    depth: float | None = None


@dataclass(slots=True)
class Borehole:
    source_id: str
    display_id: str
    x_global: float | None = None
    y_global: float | None = None
    x: float | None = None
    y: float | None = None
    elevation: float | None = None
    layers: list[LayerRecord] = field(default_factory=list)
    confidence: float = 0.0
    coordinate_source: str = "plan"


@dataclass(slots=True)
class CoordinateRecord:
    """One row read from a coordinate/elevation statement.

    ``x`` and ``y`` retain the survey table's own column order.  They are
    converted to the CAD axis order only after the plan has been inspected,
    because Russian survey tables commonly use X=northing and Y=easting.
    """

    source_id: str
    x: float
    y: float
    elevation: float | None = None
    confidence: float = 0.0


@dataclass(slots=True)
class SoilRecord:
    ige: str
    symbol: str = ""
    name: str = ""
    color: str = ""
    deformation_modulus: float | None = None
    poisson: float | None = None
    unit_weight_1: float | None = None
    unit_weight_2: float | None = None
    transition_factor: float | None = None
    moisture: float | None = None
    liquidity_index: float | None = None
    plasticity_index: float | None = None
    water_flag: str = ""
    porosity: float | None = None
    plant_content: float | None = None
    cohesion_1: float | None = None
    cohesion_2: float | None = None
    friction_1: float | None = None
    friction_2: float | None = None
    tensile_strength: float | None = None
    svintsov: float | None = None
    rock_strength: float | None = None
    strength_reduction: float | None = None
    subgrade_k: float | None = None
    code: str = ""
    description: str = ""


@dataclass(slots=True)
class CoordinateConfig:
    angle_deg: float | None = None
    origin_x: float | None = None
    origin_y: float | None = None
    scale: float = 1.0
    grid_round: float = 10.0


@dataclass(slots=True)
class DetectionProfile:
    name: str = "Автоматический"
    borehole_patterns: list[str] = field(default_factory=lambda: [
        r"(?:\b(?:скважина|скв\.?)\s*(?:№\s*)?|\bс\s*[-№]\s*)([a-zа-я]*\d{1,12})",
        r"(?:bh|borehole)[\s\-№_]*([a-zа-я]*\d{1,12})",
    ])
    heading_patterns: list[str] = field(default_factory=lambda: [
        r"(?:скважина|скв\.?|буровая|bh|borehole)\s*(?:№\s*)?([a-zа-я\-]*\d{1,12})",
    ])
    layer_hints: list[str] = field(default_factory=lambda: ["скваж", "well", "borehole"])
    coordinate: CoordinateConfig = field(default_factory=CoordinateConfig)
    strip_common_id_prefix: bool = True
    max_label_distance: float = 80.0
    statement_swap_xy: bool = True
    coordinate_control_tolerance: float = 0.5

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DetectionProfile":
        coord = CoordinateConfig(**raw.get("coordinate", {}))
        fields = {k: v for k, v in raw.items() if k != "coordinate"}
        return cls(coordinate=coord, **fields)


@dataclass(slots=True)
class Issue:
    level: str
    message: str


@dataclass(slots=True)
class AnalysisResult:
    boreholes: list[Borehole]
    soils: list[SoilRecord]
    coordinate: CoordinateConfig
    issues: list[Issue] = field(default_factory=list)
