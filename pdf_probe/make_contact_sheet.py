"""Render a review contact-sheet PNG from a frozen result.json.

Pure presentation: composites embedded base64 assets (reference crops,
candidate crops, footer) with labels/scores. Performs no matching/OCR and
does not read the source PDF.
"""

import argparse
import base64
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_RESULT = BASE_DIR / "artifacts" / "result" / "result.json"
DEFAULT_OUT = BASE_DIR / "artifacts" / "review-contact-sheet.png"

ROW_H = 130
PAD = 14
TEXT_W = 430
IMG_H = ROW_H - 2 * PAD
BG = (255, 255, 255)
LINE = (210, 215, 220)
INK = (31, 41, 51)
MUTED = (91, 101, 112)


def load_font(size):
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def thumb(b64, max_h, max_w):
    if not b64:
        return None
    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    ratio = min(max_h / img.height, max_w / img.width, 1.0)
    if ratio < 1.0:
        img = img.resize((round(img.width * ratio), round(img.height * ratio)), Image.LANCZOS)
    return img


def build_sheet(result):
    assets = result.get("assets") or {}
    symbols = result.get("symbols") or []
    contacts = result.get("contacts") or {}
    font = load_font(15)
    font_b = load_font(17)

    rows = len(symbols) + 1  # + footer block
    width = PAD * 2 + 300 + 12 + 300 + 12 + TEXT_W
    height = 60 + rows * ROW_H + PAD
    sheet = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(sheet)

    src = result.get("source") or {}
    draw.text(
        (PAD, PAD),
        f"Review-Kontaktabzug — {src.get('name', '?')} (eingefrorene Daten, kein erneutes Matching)",
        fill=INK,
        font=font_b,
    )
    y = 60

    for sym in symbols:
        sid = sym.get("id", "")
        ref = thumb(assets.get(f"ref_{sid}.png"), IMG_H, 300)
        cand = thumb(assets.get(f"cand_{sid}.png"), IMG_H, 300)
        draw.rectangle((0, y, width - 1, y + ROW_H - 1), outline=LINE)
        draw.text((PAD + 150, y + 6), "Referenz", fill=MUTED, font=font)
        draw.text((PAD + 312 + 150, y + 6), "Kandidat", fill=MUTED, font=font)
        if ref:
            sheet.paste(ref, (PAD + (300 - ref.width) // 2, y + PAD + 10))
        if cand:
            sheet.paste(cand, (PAD + 312 + (300 - cand.width) // 2, y + PAD + 10))
        else:
            draw.rectangle((PAD + 312 + 90, y + 40, PAD + 312 + 210, y + 90), outline=LINE)
            draw.text((PAD + 312 + 98, y + 58), "kein Kandidat", fill=MUTED, font=font)
        tx = PAD + 312 + 300 + 12
        draw.text((tx, y + 18), sym.get("label", sid), fill=INK, font=font_b)
        score = sym.get("score")
        score_text = f"{score:.4f}" if isinstance(score, (int, float)) else "–"
        draw.text(
            (tx, y + 46),
            f"Ähnlichkeit (0–1, keine Wahrscheinlichkeit): {score_text}",
            fill=INK,
            font=font,
        )
        draw.text((tx, y + 70), f"Status: {sym.get('status', '?')}  ·  ID: {sid}", fill=MUTED, font=font)
        bbox = sym.get("bbox_pixels")
        if bbox:
            draw.text((tx, y + 94), f"Position px: {bbox}", fill=MUTED, font=font)
        y += ROW_H

    draw.rectangle((0, y, width - 1, y + ROW_H - 1), outline=LINE)
    footer = thumb(assets.get("footer.png"), IMG_H, 612)
    draw.text((PAD, y + 6), "Fußzeile / Kontakt", fill=MUTED, font=font)
    if footer:
        sheet.paste(footer, (PAD, y + PAD + 10))
    tx = PAD + 312 + 300 + 12
    draw.text((tx, y + 18), f"Status: {contacts.get('status', '?')}", fill=INK, font=font_b)
    original = contacts.get("original", "")
    draw.text((tx, y + 46), original[:90], fill=MUTED, font=font)
    if len(original) > 90:
        draw.text((tx, y + 68), original[90:180], fill=MUTED, font=font)
    return sheet


def main(argv=None):
    parser = argparse.ArgumentParser(description="Kontaktabzug aus eingefrorenem result.json")
    parser.add_argument("--result", default=str(DEFAULT_RESULT))
    parser.add_argument("--output", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)

    result = json.loads(Path(args.result).read_text(encoding="utf-8"))
    sheet = build_sheet(result)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "PNG")
    print(f"Kontaktabzug -> {out} ({sheet.width}x{sheet.height})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
