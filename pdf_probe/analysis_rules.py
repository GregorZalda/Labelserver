import re

import numpy as np


RENDER_DPI = 300
SYMBOL_DETECTION_STATUS = "graphic_candidates"
SYMBOL_DETECTION_MESSAGE = (
    "Vorlagenfreie Suche nach grafischen Bereichen mit lokaler Ausschnitt-OCR. "
    "Grafikkandidaten und Textvorschläge sind keine bestätigte Symbolklassifikation; "
    "fehlende Kandidaten beweisen keine Abwesenheit."
)


def extract_contacts(lines):
    selected = [line for line in lines if "@" in line["text"]]
    if not selected:
        return {
            "original": "", "fields": {}, "bbox_points": None,
            "status": "Nicht sicher erkannt", "method": None,
        }
    line = max(selected, key=lambda item: len(item["text"]))
    text = line["text"].strip()
    fields = {}
    email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    if email:
        fields["E-Mail"] = email.group()
    parts = [part.strip() for part in re.split(r"\s*[·•]\s*", text) if part.strip()]
    postal_index = next(
        (i for i, part in enumerate(parts) if re.fullmatch(r"\d{5}\s+\S.*", part)), None
    )
    if postal_index is not None and postal_index >= 2:
        fields["Firma / Marke"] = " · ".join(parts[:postal_index - 1])
        fields["Straße / Hausnummer"] = parts[postal_index - 1]
        postal, town = parts[postal_index].split(None, 1)
        fields["PLZ"] = postal
        fields["Ort"] = town
        if postal_index + 1 < len(parts) and not re.search(r"[@:/]", parts[postal_index + 1]):
            fields["Land"] = parts[postal_index + 1]
    for part in reversed(parts):
        if "@" not in part and re.fullmatch(r"(?:https?://)?(?:www\.)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?", part):
            fields["Website"] = part
            break
    return {
        "original": text,
        "fields": fields,
        "bbox_points": line.get("bbox"),
        "status": "Aus PDF-Text gelesen" if line.get("method") == "PDF-Text" else "OCR – prüfen",
        "method": line.get("method"),
    }


def create_ocr():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


def run_ocr(engine, image):
    result, _ = engine(np.asarray(image.convert("RGB")))
    return [
        {
            "text": str(text),
            "score": float(score),
            "polygon_pixels": [[float(x), float(y)] for x, y in polygon],
            "bbox": [
                min(float(p[0]) for p in polygon) * 72 / RENDER_DPI,
                min(float(p[1]) for p in polygon) * 72 / RENDER_DPI,
                max(float(p[0]) for p in polygon) * 72 / RENDER_DPI,
                max(float(p[1]) for p in polygon) * 72 / RENDER_DPI,
            ],
            "method": "Lokale OCR",
        }
        for polygon, text, score in (result or [])
    ]
