from __future__ import annotations

import argparse
from pathlib import Path
import sys

from igi_model.exporter import export_xlsx
from igi_model.profile_io import load_profile
from igi_model.service import analyze_files


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Локальный конвертер ИГИ в таблицы ЛИРА-САПР")
    result.add_argument("--cli", action="store_true", help="работать без графического интерфейса")
    result.add_argument("--plan", help="план скважин DWG/DXF/PDF (необязателен при наличии ведомости)")
    result.add_argument("--columns", help="инженерно-геологические колонки DWG/DXF/PDF")
    result.add_argument("--soils", help="таблица характеристик грунтов DWG/DXF/PDF")
    result.add_argument("--coordinates", help="ведомость координат и отметок PDF/PNG/JPG/TIFF/BMP")
    result.add_argument("--output", help="выходной XLSX")
    result.add_argument("--profile", help="JSON-профиль распознавания")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.cli:
        from igi_model.gui import run_gui
        run_gui()
        return 0
    if (not args.plan and not args.coordinates) or not args.columns or not args.output:
        parser().error("для --cli требуются --columns, --output и хотя бы один источник координат: --plan или --coordinates")
    profile = load_profile(args.profile)
    result = analyze_files(args.plan, args.columns, profile, args.soils, args.coordinates)
    output = export_xlsx(result, args.output)
    print(f"Создано: {output.resolve()}")
    print(f"Скважин: {len(result.boreholes)}")
    for issue in result.issues:
        print(f"{issue.level.upper()}: {issue.message}")
    return 2 if any(issue.level == "error" for issue in result.issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
