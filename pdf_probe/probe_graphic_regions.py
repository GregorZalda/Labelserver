import argparse
import base64
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

import analyze_label as al
import catalog_rules
import graphic_candidates as graphics


BASE_DIR = Path(__file__).resolve().parent
INPUTS = (
    ("lanyard", BASE_DIR / "artifacts/result/result.json"),
    ("tape", BASE_DIR / "artifacts/result/latest100-20261105/pdf_001/result.json"),
    ("badge", BASE_DIR / "artifacts/samples-20261105/strip_new_800319/result.json"),
)


def load_image(result):
    return Image.open(io.BytesIO(base64.b64decode(result["assets"]["page.png"]))).convert("RGB")


def capture(output):
    if output.exists():
        raise ValueError("Für eine neue Kandidatensuche ein neues Ausgabeverzeichnis verwenden.")
    output.mkdir(parents=True)
    for name, path in INPUTS:
        source = json.loads(path.read_text(encoding="utf-8"))
        image = load_image(source)
        regions = graphics.find_graphic_candidates(
            image, source["native_lines"], source["ocr_lines"], source["render"]["dpi"]
        )
        result = {
            "schema": al.RESULT_SCHEMA,
            "tool": {"name": al.TOOL_NAME, "version": "0.3.0-graphics-pilot"},
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": source["source"],
            "input_capture": {"path": str(path), "generated_at": source["generated_at"]},
            "environment": al.collect_versions(),
            "method": {
                "render_dpi": source["render"]["dpi"],
                "symbol_detection": "graphic_candidates",
                "matching": graphics.DISCLAIMER,
                "native_text": "Aus gespeichertem Seitenbefund übernommen",
                "ocr": "Gespeicherte Seiten-OCR; Ausschnitt-OCR separat",
                "graphics_algorithm": graphics.ALGORITHM,
            },
            "render": source["render"],
            "reference": None,
            "contact_source": source.get("contact_source"),
            "contacts": source["contacts"],
            "native_lines": source["native_lines"],
            "ocr_lines": source["ocr_lines"],
            "symbols": regions,
            "disclaimers": [
                graphics.DISCLAIMER,
                "Seitenbild und Seitentexte aus gespeichertem Ergebnis übernommen; Quelldatei nicht neu geöffnet.",
                "Alle Vorschläge sind unbestätigt; keine Konformitätsprüfung.",
            ],
            "assets": {"page.png": source["assets"]["page.png"]},
        }
        for region in regions:
            result["assets"][f"cand_{region['id']}.png"] = al.pil_to_b64(image.crop(region["bbox_pixels"]))
        if source["assets"].get("footer.png"):
            result["assets"]["footer.png"] = source["assets"]["footer.png"]
        folder = output / name
        folder.mkdir()
        (folder / "snapshot.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(name, "Grafik-Kandidaten", len(regions), flush=True)


def run_ocr(output):
    import socket

    def blocked(*args, **kwargs):
        raise RuntimeError("Netzwerkzugriff im Grafiktest blockiert.")

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    socket.getaddrinfo = blocked
    import analysis_rules

    engine = analysis_rules.create_ocr()
    for name, _ in INPUTS:
        folder = output / name
        target = folder / "result.json"
        if target.exists():
            raise ValueError(f"Ergebnis existiert bereits: {target}")
        result = json.loads((folder / "snapshot.json").read_text(encoding="utf-8"))
        result["symbols"] = graphics.add_ocr_proposals(
            result["symbols"], load_image(result), engine, result["ocr_lines"]
        )
        target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        for region in result["symbols"]:
            print(name, region["id"], region["proposal"]["suggested_name"],
                  repr(region["proposal"]["observed_text"]), flush=True)


def render(output):
    for name, _ in INPUTS:
        folder = output / name
        path = folder / "result.json"
        if not path.exists():
            path = folder / "snapshot.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        image = load_image(result)
        marked = image.copy()
        drawing = ImageDraw.Draw(marked)
        count = len(result["symbols"])
        sheet = Image.new("RGB", (660, max(1, (count + 2) // 3) * 180), "white")
        pen = ImageDraw.Draw(sheet)
        for index, region in enumerate(result["symbols"]):
            box = region["bbox_pixels"]
            drawing.rectangle(box, outline="red", width=2)
            drawing.text((box[0], max(0, box[1] - 12)), str(index + 1), fill="red", stroke_width=0)
            crop = image.crop(box)
            thumbnail = ImageOps.contain(crop, (204, 120))
            x, y = (index % 3) * 220, (index // 3) * 180
            sheet.paste(thumbnail, (x + 8, y + 4))
            label = (region.get("proposal") or {}).get("suggested_name", "")
            pen.text((x + 8, y + 128), f"{index + 1}. {label[:29]}", fill="black")
            pen.text((x + 8, y + 147), str(box), fill="black")
        marked.save(folder / "regions.png")
        sheet.save(folder / "candidates.png")


def refresh_proposals(output):
    for name, _ in INPUTS:
        path = output / name / "result.json"
        result = json.loads(path.read_text(encoding="utf-8"))
        for region in result["symbols"]:
            raw = region["crop_ocr"]
            texts = [line["text"] for line in raw["lines"]]
            source = "Lokale Ausschnitt-OCR"
            if not texts:
                texts = catalog_rules.context_texts(result["ocr_lines"], region["bbox_pixels"])
                source = "Gespeicherte Seiten-OCR"
            region["proposal"] = catalog_rules.proposal_from_text(texts, source=source)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--detect", action="store_true")
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--refresh-proposals", action="store_true")
    args = parser.parse_args()
    if args.detect:
        capture(args.output)
    if args.ocr:
        run_ocr(args.output)
    if args.refresh_proposals:
        refresh_proposals(args.output)
    if args.render:
        render(args.output)


if __name__ == "__main__":
    main()
