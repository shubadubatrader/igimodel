from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from .models import DetectionProfile


def load_profile(path: str | Path | None) -> DetectionProfile:
    if not path:
        return DetectionProfile()
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return DetectionProfile.from_dict(raw)


def save_profile(profile: DetectionProfile, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(profile), ensure_ascii=False, indent=2), encoding="utf-8")
    return output
