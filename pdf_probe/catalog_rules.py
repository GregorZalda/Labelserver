import hashlib
import re
import unicodedata

from PIL import Image, ImageChops, ImageOps


MATERIAL_PAIRS = {
    (1, "PET"), (2, "HDPE"), (3, "PVC"), (4, "LDPE"),
    (5, "PP"), (6, "PS"), (7, "ABS"), (20, "PAP"),
    (21, "PAP"), (22, "PAP"), (40, "FE"), (41, "ALU"),
}
ABBREVIATIONS = {"PET", "HDPE", "PVC", "LDPE", "PP", "PS", "ABS", "PAP", "FE", "ALU"}


def canonical_crop(image):
    rgba = image.convert("RGBA")
    rgb = Image.alpha_composite(Image.new("RGBA", rgba.size, "white"), rgba).convert("RGB")
    box = ImageChops.difference(rgb, Image.new("RGB", rgb.size, "white")).getbbox()
    if box is None:
        raise ValueError("Der Ausschnitt enthält nur weiße Pixel.")
    return rgb.crop(box)


def exact_visual_key(image):
    cropped = canonical_crop(image)
    identity = f"rgb-exact-v1:{cropped.width}:{cropped.height}:".encode("ascii")
    return hashlib.sha256(identity + cropped.tobytes()).hexdigest()


def proposal_from_text(texts, template_id=None, source="Gespeicherte Seiten-OCR"):
    joined = " ".join(str(text) for text in texts).strip()
    separated = re.sub(r"(?<=\d)(?=[A-Za-z])|(?<=[A-Za-z])(?=\d)", " ", joined).upper()
    tokens = set(re.findall(r"[A-Z]+", separated))
    abbreviations = tokens & ABBREVIATIONS
    numbers = {int(value) for value in re.findall(r"(?<!\d)\d{1,2}(?!\d)", separated)}
    pairs = {(number, abbreviation) for number in numbers for abbreviation in abbreviations} & MATERIAL_PAIRS
    if len(pairs) == 1 and len(numbers) == 1 and len(abbreviations) == 1:
        number, abbreviation = next(iter(pairs))
        return {
            "suggested_name": f"{number:02d} {abbreviation}",
            "suggestion_source": source,
            "suggestion_kind": "ocr_material_pair",
            "observed_text": joined,
            "material_number": number,
            "material_abbreviation": abbreviation,
            "warning": "Unbestätigter OCR-Vorschlag; keine Material- oder Normkonformität nachgewiesen.",
        }
    plain = "".join(
        char for char in unicodedata.normalize("NFKD", joined.upper())
        if not unicodedata.combining(char)
    )
    words = set(re.findall(r"[A-Z]+", plain))
    if not abbreviations and not numbers:
        spanish = re.search(r"\bA[LI]\s+(AZUL|AMARILLO|MARRON|VERDE)\b", plain)
        if spanish:
            return {
                "suggested_name": f"Spanischer Sortierhinweis – {spanish[1].title()}",
                "suggestion_source": source,
                "suggestion_kind": "ocr_sorting_text",
                "observed_text": joined,
                "material_number": None,
                "material_abbreviation": None,
                "warning": "Vorschlag aus Beschriftung; Piktogramm und Sortierzuordnung nicht bestätigt.",
            }
        if {"BAC", "DE", "TRI"} <= words:
            components = [
                word.title() for word in ("BOITE", "PAPIER", "FILM", "SACHET", "FLACON")
                if word in words
            ]
            suffix = f" – {', '.join(components)}" if components else ""
            return {
                "suggested_name": f"Französische Sortierinformation{suffix}",
                "suggestion_source": source,
                "suggestion_kind": "ocr_sorting_text",
                "observed_text": joined,
                "material_number": None,
                "material_abbreviation": None,
                "warning": "Vorschlag aus Sortiertext; Triman-Grafik und vollständiger Aufbau nicht bestätigt.",
            }
    quantity = re.fullmatch(r"\s*(\d{1,4})\s*[x×]\s*", joined, re.IGNORECASE)
    if quantity:
        return {
            "suggested_name": f"Mengenzeichen {quantity[1]}×",
            "suggestion_source": source,
            "suggestion_kind": "ocr_quantity",
            "observed_text": joined,
            "material_number": None,
            "material_abbreviation": None,
            "warning": "Unbestätigter Textvorschlag; keine Materialkennzeichnung.",
        }
    if abbreviations:
        family = "/".join(sorted(abbreviations))
        name = f"{family} – Nummer offen/mehrdeutig"
    else:
        name = "Unbekanntes Symbol"
    return {
        "suggested_name": name,
        "suggestion_source": source if joined else "Keine Textgrundlage",
        "suggestion_kind": "unverified_hint",
        "observed_text": joined,
        "material_number": None,
        "material_abbreviation": next(iter(abbreviations)) if len(abbreviations) == 1 else None,
        "warning": "Nur Vorschlag. Eine Kennung ist keine gelesene Materialnummer.",
    }


def context_texts(ocr_lines, bbox):
    x0, y0, x1, y1 = bbox
    texts = []
    for line in ocr_lines:
        polygon = line.get("polygon_pixels") or []
        if not polygon:
            continue
        center_x = sum(point[0] for point in polygon) / len(polygon)
        center_y = sum(point[1] for point in polygon) / len(polygon)
        if x0 <= center_x <= x1 and y0 <= center_y <= y1:
            texts.append(line["text"])
    return texts


def read_crop(engine, image):
    import numpy as np

    cropped = canonical_crop(image)
    factor = min(4.0, max(1.0, 480 / cropped.height), 2048 / max(cropped.size))
    enlarged = cropped.resize(
        (max(1, round(cropped.width * factor)), max(1, round(cropped.height * factor))),
        Image.Resampling.LANCZOS,
    )
    padded = ImageOps.expand(enlarged, border=24, fill="white")
    result, _ = engine(np.asarray(padded))
    return {
        "method": "Lokale Ausschnitt-OCR, getrennt von Symbolklassifikation",
        "scale": factor,
        "lines": [
            {
                "text": str(text), "score": float(score),
                "polygon": [[float(x), float(y)] for x, y in polygon],
            }
            for polygon, text, score in (result or [])
        ],
    }


def material_safe_display(symbol):
    if symbol.get("candidate_kind") == "graphic_region":
        name = (symbol.get("proposal") or {}).get("suggested_name", "Unbenannter Grafikkandidat")
        return f"{name} – Vorschlag"
    label = symbol.get("label") or symbol.get("id") or "Symbol"
    if str(label).casefold().startswith("materialzeichen") or re.fullmatch(
        r"(?:pet|abs|fe|pap)_\d+", str(symbol.get("id", ""))
    ):
        return "Materialzeichen – Code unbestätigt"
    return f"{label} – Vorschlag"


def is_legacy_template_result(result):
    tool = result.get("tool") or {}
    if tool.get("name") == "pdf_probe analyze_label" and tool.get("version") == "0.1.0":
        return True
    reference = result.get("reference") or {}
    name = str(reference.get("path", "")).replace("\\", "/").rsplit("/", 1)[-1]
    return name.casefold() == "symbol-reference.png" and bool(reference.get("symbols"))
