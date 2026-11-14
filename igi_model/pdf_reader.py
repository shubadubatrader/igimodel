from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import fitz

from .cad_reader import resource_root
from .models import DrawingData, Polyline, TextItem


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def _vector_page(page, page_no: int, data: DrawingData) -> None:
    words = page.get_text("words", sort=True)
    grouped: dict[tuple[int, int], list[tuple]] = defaultdict(list)
    for word in words:
        grouped[(int(word[5]), int(word[6]))].append(word)
    for line_words in grouped.values():
        line_words.sort(key=lambda item: item[0])
        text = " ".join(str(item[4]) for item in line_words).strip()
        if text:
            x = min(float(item[0]) for item in line_words)
            y = -min(float(item[1]) for item in line_words)
            height = max(float(item[3] - item[1]) for item in line_words)
            data.texts.append(TextItem(text, x, y, "PDF_TEXT", height, page_no))
    for drawing in page.get_drawings():
        for item in drawing.get("items", []):
            if item[0] == "l":
                a, b = item[1], item[2]
                data.polylines.append(Polyline([(float(a.x), -float(a.y)), (float(b.x), -float(b.y))], "PDF_VECTOR", page_no))
            elif item[0] == "re":
                rect = item[1]
                pts = [(rect.x0, -rect.y0), (rect.x1, -rect.y0), (rect.x1, -rect.y1), (rect.x0, -rect.y1), (rect.x0, -rect.y0)]
                data.polylines.append(Polyline([(float(x), float(y)) for x, y in pts], "PDF_VECTOR", page_no))


def tesseract_path() -> Path | None:
    candidates = [
        resource_root() / "vendor" / "tesseract" / "tesseract.exe",
        Path(sys.executable).resolve().parent / "vendor" / "tesseract" / "tesseract.exe",
    ]
    found = shutil.which("tesseract")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _ocr_page(page, page_no: int, data: DrawingData) -> None:
    tool = tesseract_path()
    if tool is None:
        raise RuntimeError("Файл не содержит текстового слоя. Для скана или изображения нужен локальный Tesseract OCR с русским языком.")
    with tempfile.TemporaryDirectory(prefix="igi_ocr_") as temp:
        image = Path(temp) / "page.png"
        output = Path(temp) / "page"
        page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False).save(image)
        env = None
        tessdata = tool.parent / "tessdata"
        if tessdata.exists():
            import os
            env = os.environ.copy()
            env["TESSDATA_PREFIX"] = str(tessdata)
        proc = subprocess.run(
            [str(tool), str(image), str(output), "-l", "rus+eng", "tsv"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=180,
        )
        tsv = output.with_suffix(".tsv")
        if proc.returncode != 0 or not tsv.exists():
            raise RuntimeError(f"Ошибка локального OCR: {(proc.stderr or proc.stdout)[-500:]}")
        rows: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
        lines = tsv.read_text(encoding="utf-8", errors="replace").splitlines()
        if not lines:
            return
        headers = lines[0].split("\t")
        for line in lines[1:]:
            values = line.split("\t")
            if len(values) != len(headers):
                continue
            row = dict(zip(headers, values))
            if row.get("text", "").strip():
                rows[(row["block_num"], row["par_num"], row["line_num"])].append(row)
        for words in rows.values():
            words.sort(key=lambda row: int(row["left"]))
            text = " ".join(row["text"].strip() for row in words).strip()
            x = min(int(row["left"]) for row in words) / 3.0
            y = -min(int(row["top"]) for row in words) / 3.0
            data.texts.append(TextItem(text, x, y, "PDF_OCR", 4.0, page_no))


def read_visual(path: str | Path) -> DrawingData:
    source = Path(path)
    document = fitz.open(source)
    data = DrawingData(source=source)
    for page_no, page in enumerate(document):
        before = len(data.texts)
        _vector_page(page, page_no, data)
        if len(data.texts) == before:
            _ocr_page(page, page_no, data)
    return data


def read_pdf(path: str | Path) -> DrawingData:
    return read_visual(path)


def read_source(path: str | Path) -> DrawingData:
    source = Path(path)
    if source.suffix.lower() == ".pdf" or source.suffix.lower() in IMAGE_SUFFIXES:
        return read_visual(source)
    from .cad_reader import read_cad
    return read_cad(source)
