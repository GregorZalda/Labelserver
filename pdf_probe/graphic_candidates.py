import math
import re

import cv2
import numpy as np

import catalog_rules


ALGORITHM = "graphic-regions/1"
INK_THRESHOLD = 210
OCR_MASK_MIN_SCORE = 0.7
MIN_SIDE_MM = 1.3
MIN_COMPONENT_INK_MM2 = 0.025
TEXT_PADDING_MM = 0.12
CROP_PADDING_MM = 0.25
LABEL_GAP_MM = 0.8
DISCLAIMER = (
    "Vorlagenfreie Suche nach grafischen Bereichen, keine bestätigte Symbolklassifikation. "
    "Auch Logos, Mengenzeichen, Abbildungen oder Textreste können Kandidaten sein; "
    "fehlende Kandidaten beweisen keine Abwesenheit."
)


def _clamp_box(box, width, height, padding=0):
    return [
        max(0, math.floor(box[0] - padding)),
        max(0, math.floor(box[1] - padding)),
        min(width, math.ceil(box[2] + padding)),
        min(height, math.ceil(box[3] + padding)),
    ]


def _text_boxes(native_lines, ocr_lines, width, height, dpi):
    boxes = []
    for line in native_lines:
        if line.get("text", "").strip() and line.get("bbox"):
            boxes.append(_clamp_box([value * dpi / 72 for value in line["bbox"]], width, height))
    for line in ocr_lines:
        if line.get("score", 0) < OCR_MASK_MIN_SCORE:
            continue
        if len(re.findall(r"\w", line.get("text", ""))) < 2:
            continue
        polygon = line.get("polygon_pixels") or []
        if polygon:
            boxes.append(_clamp_box([
                min(point[0] for point in polygon), min(point[1] for point in polygon),
                max(point[0] for point in polygon), max(point[1] for point in polygon),
            ], width, height))
    return [box for box in boxes if box[2] > box[0] and box[3] > box[1]]


def _barcode_like(ink):
    height, width = ink.shape
    if min(width, height) == 0:
        return False
    for view in (ink, ink.T):
        filled = view.mean(axis=0) >= 0.85
        starts = int(np.count_nonzero(filled & ~np.r_[False, filled[:-1]]))
        middle = view[max(1, view.shape[0] // 10):max(2, view.shape[0] * 9 // 10)]
        row_ink = middle.sum(axis=1)
        variation = float(row_ink.std() / row_ink.mean()) if row_ink.size and row_ink.mean() else 1.0
        if starts >= 2 and 0.2 <= filled.mean() <= 0.9 and max(view.shape) >= 50 and variation <= 0.08:
            return True
    return False


def _protected_text(box, components):
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    for x, y, w, h, area in components:
        if area / (w * h) < 0.38 or (w < width * 1.2 and h < height * 1.2):
            continue
        overlap_x = max(0, min(x + w, x1) - max(x, x0))
        overlap_y = max(0, min(y + h, y1) - max(y, y0))
        if overlap_x >= width * 0.9 and overlap_y >= height * 0.9:
            return True
    return False


def _include_labels(box, text_boxes, max_gap):
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    included = [box]
    for text in text_boxes:
        tw, th = text[2] - text[0], text[3] - text[1]
        if tw > width * 0.9 or th > height * 0.7:
            continue
        center = (text[0] + text[2]) / 2
        vertical_gap = max(0, text[1] - y1, y0 - text[3])
        overlap = min(x1, text[2]) - max(x0, text[0])
        if x0 - max_gap <= center <= x1 + max_gap and vertical_gap <= max_gap:
            if overlap >= min(width, tw) * 0.4:
                included.append(text)
    return [
        min(item[0] for item in included), min(item[1] for item in included),
        max(item[2] for item in included), max(item[3] for item in included),
    ]


def find_graphic_candidates(image, native_lines=(), ocr_lines=(), dpi=300):
    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    pixels_mm = dpi / 25.4
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    original = gray < INK_THRESHOLD
    ink = original.astype("uint8") * 255
    text_boxes = _text_boxes(native_lines, ocr_lines, width, height, dpi)
    _, _, original_stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    solid_components = [
        [int(value) for value in row]
        for row in original_stats[1:] if row[4] >= 50 and min(row[2:4]) >= MIN_SIDE_MM * pixels_mm
    ]
    for box in text_boxes:
        if _protected_text(box, solid_components):
            continue
        x0, y0, x1, y1 = _clamp_box(box, width, height, TEXT_PADDING_MM * pixels_mm)
        ink[y0:y1, x0:x1] = 0
    horizontal = cv2.morphologyEx(
        ink, cv2.MORPH_OPEN, np.ones((1, max(round(15 * pixels_mm), round(width * 0.5))), "uint8")
    )
    vertical = cv2.morphologyEx(
        ink, cv2.MORPH_OPEN, np.ones((max(round(15 * pixels_mm), round(height * 0.4)), 1), "uint8")
    )
    ink[(horizontal > 0) | (vertical > 0)] = 0
    connected = cv2.dilate(ink, np.ones((3, 3), "uint8"))
    count, _, stats, _ = cv2.connectedComponentsWithStats(connected, connectivity=8)
    pieces = []
    for i in range(1, count):
        x, y, w, h, area = [int(value) for value in stats[i]]
        if area >= max(4, MIN_COMPONENT_INK_MM2 * pixels_mm ** 2):
            pieces.append([x, y, x + w, y + h])
    eligible = []
    for box in pieces:
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        if min(w, h) < MIN_SIDE_MM * pixels_mm or max(w / h, h / w) > 15:
            continue
        if w >= width * 0.95 and h >= height * 0.95:
            continue
        if _barcode_like(original[y0:y1, x0:x1]):
            continue
        eligible.append(box)
    regions = []
    for box in eligible:
        if any(
            other != box and other[0] <= box[0] and other[1] <= box[1]
            and other[2] >= box[2] and other[3] >= box[3]
            for other in eligible
        ):
            continue
        with_text = _include_labels(box, text_boxes, LABEL_GAP_MM * pixels_mm)
        bbox = _clamp_box(with_text, width, height, CROP_PADDING_MM * pixels_mm)
        regions.append(bbox)
    regions.sort(key=lambda box: (box[1], box[0]))
    return [
        {
            "id": f"region_{i:03d}",
            "label": "Unbenannter Grafikkandidat",
            "status": "Grafikkandidat – prüfen",
            "candidate_kind": "graphic_region",
            "bbox_pixels": box,
            "score": None,
            "method": ALGORITHM,
            "review_required": True,
        }
        for i, box in enumerate(regions, 1)
    ]


def add_ocr_proposals(candidates, image, engine, ocr_lines=()):
    enriched = []
    for candidate in candidates:
        crop = image.crop(candidate["bbox_pixels"])
        raw_ocr = catalog_rules.read_crop(engine, crop)
        texts = [line["text"] for line in raw_ocr["lines"]]
        if not texts:
            texts = catalog_rules.context_texts(ocr_lines, candidate["bbox_pixels"])
            source = "Gespeicherte Seiten-OCR"
        else:
            source = "Lokale Ausschnitt-OCR"
        proposal = catalog_rules.proposal_from_text(texts, source=source)
        enriched.append({**candidate, "proposal": proposal, "crop_ocr": raw_ocr})
    return enriched
