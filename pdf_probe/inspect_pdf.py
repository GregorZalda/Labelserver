"""Inspect a single PDF: render page 1 at 300 dpi and dump text/drawing structure to JSON.

Read-only with respect to the source PDF.
"""

import json
import sys
from pathlib import Path

import pymupdf

PDF_PATH = Path(r"\\duramet02\Label\8000\8241\824101_1_70x100_112026.pdf")
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
RENDER_DPI = 300


def to_jsonable(obj):
    if isinstance(obj, pymupdf.Rect):
        return list(obj)
    if isinstance(obj, pymupdf.Point):
        return [obj.x, obj.y]
    if isinstance(obj, pymupdf.Matrix):
        return list(obj)
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except UnicodeDecodeError:
            return obj.hex()
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(PDF_PATH)
    if doc.is_encrypted:
        print("PDF is encrypted; cannot inspect.", file=sys.stderr)
        return 1

    page = doc[0]

    pix = page.get_pixmap(dpi=RENDER_DPI)
    png_path = ARTIFACTS_DIR / "page-1.png"
    pix.save(png_path)

    images = []
    for img in page.get_images(full=True):
        xref = img[0]
        images.append(
            {
                "xref": xref,
                "smask": img[1],
                "width": img[2],
                "height": img[3],
                "bpc": img[4],
                "colorspace": img[5],
                "name": img[7],
                "rects": to_jsonable(page.get_image_rects(xref)),
            }
        )

    inspection = {
        "source_pdf": str(PDF_PATH),
        "pymupdf_version": pymupdf.VersionBind,
        "document": {
            "page_count": doc.page_count,
            "metadata": doc.metadata,
            "is_pdf": doc.is_pdf,
            "is_encrypted": doc.is_encrypted,
            "needs_pass": doc.needs_pass,
        },
        "page": {
            "number": page.number,
            "rect": to_jsonable(page.rect),
            "mediabox": to_jsonable(page.mediabox),
            "cropbox": to_jsonable(page.cropbox),
            "rotation": page.rotation,
            "transformation_matrix": to_jsonable(page.transformation_matrix),
        },
        "render": {
            "dpi": RENDER_DPI,
            "width_px": pix.width,
            "height_px": pix.height,
            "png_path": str(png_path),
        },
        "text_dict": to_jsonable(page.get_text("dict")),
        "text_rawdict": to_jsonable(page.get_text("rawdict")),
        "drawings": to_jsonable(page.get_drawings()),
        "images": images,
    }

    json_path = ARTIFACTS_DIR / "pdf-inspection.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(inspection, f, indent=1, ensure_ascii=False)

    print(f"pages={doc.page_count} page_rect={list(page.rect)}")
    print(f"render: {pix.width}x{pix.height}px -> {png_path}")
    print(f"inspection json -> {json_path}")

    doc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
