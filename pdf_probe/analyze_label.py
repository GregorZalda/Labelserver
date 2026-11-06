"""Standalone one-PDF label analysis prototype.

Reads exactly one explicitly passed PDF read-only, renders it locally,
extracts native text and local OCR without symbol templates,
freezes everything into result.json and writes a static HTML report.
OCR/contact rules live in analysis_rules.py and are only executed,
never re-implemented, here.

Usage:
    analyze_label.py [--pdf PATH] [--output DIR] [--no-open]
    analyze_label.py --render-json PATH [--output DIR] [--no-open]
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import io
import json
import os
import sys
import traceback
import uuid
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PDF = r"\\duramet02\Label\8000\8241\824101_1_70x100_112026.pdf"
DEFAULT_OUTPUT = BASE_DIR / "artifacts" / "result"
RESULT_SCHEMA = "pdf-probe-result/1"
TOOL_NAME = "pdf_probe analyze_label"
TOOL_VERSION = "0.3.0"

CONTACT_FIELDS = (
    "Firma / Marke",
    "Straße / Hausnummer",
    "PLZ",
    "Ort",
    "Land",
    "E-Mail",
    "Website",
)
UNKNOWN = "Nicht sicher erkannt"
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")

DISCLAIMERS = [
    "Kontaktdaten werden aus eingebettetem PDF-Text bzw. lokaler OCR gelesen; "
    "manuelle Prüfung erforderlich.",
    "Vorlagenfreie Suche nach grafischen Bereichen, keine bestätigte "
    "Symbolklassifikation. Auch Logos, Mengenzeichen, Abbildungen oder "
    "Textreste können Kandidaten sein; fehlende Kandidaten beweisen keine "
    "Abwesenheit.",
    "Sprachklassifikation und Barcode-/QR-Erkennung sind nicht Teil dieses Tests.",
    "Die Analyse läuft vollständig lokal; die Quelldatei bleibt unverändert "
    "(SHA-256 vor und nach der Analyse identisch).",
]

CSS = """
:root { --border:#d7dce2; --bg:#f5f7fa; --card:#ffffff; --ink:#1f2933; --muted:#5b6570;
--ok:#b45309; --unknown:#6b7280; --box:#d92d20; --footer:#175cd3; }
* { box-sizing: border-box; }
body { margin:0; font-family:"Segoe UI", Arial, sans-serif; background:var(--bg); color:var(--ink);
font-size:15px; line-height:1.45; }
main { max-width:1400px; margin:0 auto; padding:24px; }
h1 { font-size:22px; margin:0 0 4px; }
h2 { font-size:17px; margin:28px 0 10px; border-bottom:1px solid var(--border); padding-bottom:5px; }
.card { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:14px 16px; }
.muted { color:var(--muted); }
.mono { font-family:Consolas, "Courier New", monospace; font-size:12.5px; word-break:break-all; }
.badges { display:flex; gap:8px; flex-wrap:wrap; margin:10px 0 0; }
.badge { display:inline-block; padding:2px 10px; border-radius:999px; font-size:12.5px;
border:1px solid var(--border); background:#eef2f6; }
.badge.cand { background:#fff3e6; border-color:#f2c48d; color:var(--ok); }
.badge.unknown { background:#eceff3; color:var(--unknown); }
.badge.ok { background:#e7f4ea; border-color:#9ed3a8; color:#1e6b34; }
ul.scope { margin:8px 0 0; padding-left:20px; }
ul.scope li { margin:3px 0; }
.matrix-wrap { overflow-x:auto; }
table.matrix { border-collapse:collapse; width:100%; white-space:nowrap; }
table.matrix th, table.matrix td { border:1px solid var(--border); padding:6px 10px;
text-align:left; font-size:13px; }
table.matrix th { background:#eef2f6; }
.fields { border-collapse:collapse; }
.fields th, .fields td { border:1px solid var(--border); padding:5px 10px; text-align:left; font-size:13.5px; }
.fields th { background:#eef2f6; }
.crop-row { display:flex; gap:18px; flex-wrap:wrap; align-items:flex-start; margin-top:10px; }
.crop-row img, .sym-card img { image-rendering:auto; border:1px solid var(--border);
background:#fff; max-width:100%; }
.sym-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:14px; }
.sym-card { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:12px; }
.sym-card h3 { margin:0 0 8px; font-size:14.5px; }
.sym-imgs { display:flex; gap:10px; align-items:flex-start; }
.sym-imgs figure { margin:0; text-align:center; }
.sym-imgs figcaption { font-size:11.5px; color:var(--muted); margin-top:3px; }
.sym-imgs img { max-width:140px; max-height:110px; }
.no-cand { width:140px; height:80px; display:flex; align-items:center; justify-content:center;
border:1px dashed var(--border); color:var(--muted); font-size:12px; background:#fafbfc; }
.page-wrap { position:relative; display:block; width:min(100%,827px); max-width:100%; }
.page-wrap img { display:block; width:100%; max-width:100%; height:auto; outline:1px solid var(--border); }
.ovl { position:absolute; border:2px solid var(--box); background:rgba(217,45,32,0.08); }
.ovl.footer { border-color:var(--footer); background:rgba(23,92,211,0.08); }
.ovl span { position:absolute; top:-18px; left:-2px; font-size:10.5px; white-space:nowrap;
background:var(--box); color:#fff; padding:0 4px; border-radius:2px; }
.ovl.footer span { background:var(--footer); }
details { background:var(--card); border:1px solid var(--border); border-radius:8px;
padding:10px 14px; margin-top:10px; }
summary { cursor:pointer; font-weight:600; }
pre.raw { white-space:pre-wrap; word-break:break-word; font-size:12.5px; max-height:340px;
overflow:auto; margin:8px 0 0; }
footer { margin-top:34px; font-size:12px; color:var(--muted); }
"""


class FriendlyError(Exception):
    """An expected, user-facing failure."""


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def pil_to_b64(image):
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def b64_to_bytes(data):
    return base64.b64decode(data.encode("ascii"))


def decode_asset_b64(data):
    """Strictly validate embedded image data from a (possibly untrusted) JSON."""
    if not isinstance(data, str):
        raise FriendlyError("Asset-Eintrag in result.json ist kein Base64-String.")
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception as exc:
        raise FriendlyError(f"Ungültige Base64-Bilddaten in result.json: {exc}") from exc
    return base64.b64encode(raw).decode("ascii")


def csv_safe(value):
    text = "" if value is None else str(value)
    if text and text[0] in FORMULA_PREFIXES:
        return "'" + text
    return text


def collect_versions():
    versions = {"python": sys.version.split()[0]}

    def add(key, *dist_names):
        for name in dist_names:
            try:
                versions[key] = importlib_metadata.version(name)
                return
            except importlib_metadata.PackageNotFoundError:
                continue
        versions[key] = None

    add("pymupdf", "PyMuPDF", "pymupdf")
    add("pillow", "Pillow")
    add("opencv", "opencv-python")
    add("numpy", "numpy")
    add("onnxruntime", "onnxruntime")
    add("rapidocr_onnxruntime", "rapidocr_onnxruntime", "rapidocr-onnxruntime")
    return versions


def extract_native_lines(page):
    lines = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if not text:
                continue
            lines.append(
                {
                    "text": text,
                    "bbox": [float(v) for v in line["bbox"]],
                    "method": "PDF-Text",
                }
            )
    return lines


def resolve_input(pdf_arg):
    path = Path(pdf_arg)
    if not path.exists():
        raise FriendlyError(f"Eingabedatei nicht gefunden: {path}")
    if not path.is_file():
        raise FriendlyError(f"Eingabe ist keine Datei: {path}")
    return path


def prepare_output_dir(out_dir):
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FriendlyError(
            f"Ausgabeverzeichnis ist nicht leer (kein Überschreiben): {out_dir}\n"
            "Bitte ein neues/leeres Verzeichnis mit --output wählen."
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def resolve_analysis_output(requested, default_output=DEFAULT_OUTPUT):
    if requested is not None:
        return Path(requested)
    default_output = Path(default_output)
    if not default_output.exists() or not any(default_output.iterdir()):
        return default_output
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return default_output.parent / f"{default_output.name}-{stamp}-{uuid.uuid4().hex[:8]}"


def run_analysis(pdf_path, out_dir):
    import pymupdf
    from PIL import Image

    import analysis_rules as rules
    import graphic_candidates

    pdf_path = resolve_input(pdf_path)
    out_dir = prepare_output_dir(out_dir)
    if pdf_path.resolve() == out_dir.resolve():
        raise FriendlyError("Eingabe und Ausgabe dürfen nicht identisch sein.")
    if pdf_path.resolve().is_relative_to(out_dir.resolve()):
        raise FriendlyError("Eingabedatei darf nicht im Ausgabeverzeichnis liegen.")

    hash_before = sha256_file(pdf_path)
    raw = pdf_path.read_bytes()
    if b"%PDF-" not in raw[:1024]:
        raise FriendlyError("Eingabedatei ist keine gültige PDF-Datei (fehlender %PDF-Header).")

    try:
        doc = pymupdf.open(stream=raw, filetype="pdf")
    except Exception as exc:
        raise FriendlyError(f"PDF konnte nicht geöffnet werden: {exc}") from exc
    if not doc.is_pdf:
        raise FriendlyError("Eingabedatei ist keine reguläre PDF-Datei.")
    if doc.is_encrypted or doc.needs_pass:
        raise FriendlyError("PDF ist verschlüsselt; der Prototyp kann sie nicht lesen.")
    if doc.page_count != 1:
        raise FriendlyError(
            f"Prototyp verarbeitet nur einseitige PDFs; diese Datei hat {doc.page_count} Seiten."
        )

    page = doc[0]
    pix = page.get_pixmap(dpi=rules.RENDER_DPI)
    page_image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")

    native_lines = extract_native_lines(page)

    ocr_engine = rules.create_ocr()
    ocr_lines = rules.run_ocr(ocr_engine, page_image)

    contacts = rules.extract_contacts(native_lines)
    contact_source = "PDF-Text"
    if not contacts["original"]:
        contacts = rules.extract_contacts(ocr_lines)
        contact_source = "Lokale OCR (Fallback)" if contacts["original"] else None

    candidates = graphic_candidates.find_graphic_candidates(
        page_image, native_lines, ocr_lines, rules.RENDER_DPI
    )
    symbols = graphic_candidates.add_ocr_proposals(
        candidates, page_image, ocr_engine, ocr_lines
    )

    hash_after = sha256_file(pdf_path)
    if hash_before != hash_after:
        raise FriendlyError("Quelldatei hat sich während der Analyse verändert – Abbruch.")

    scale = rules.RENDER_DPI / 72.0
    assets = {"page.png": pil_to_b64(page_image)}
    assets_dir = out_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    for sym in symbols:
        bbox = sym.get("bbox_pixels")
        if not bbox:
            continue
        cand = page_image.crop(tuple(int(v) for v in bbox))
        assets[f"cand_{sym['id']}.png"] = pil_to_b64(cand)
        (assets_dir / f"cand_{sym['id']}.png").write_bytes(
            b64_to_bytes(assets[f"cand_{sym['id']}.png"])
        )

    if contacts.get("bbox_points"):
        x0, y0, x1, y1 = contacts["bbox_points"]
        pad = 4
        footer_box = (
            max(0, int(x0 * scale) - pad),
            max(0, int(y0 * scale) - pad),
            min(page_image.width, int(x1 * scale) + pad),
            min(page_image.height, int(y1 * scale) + pad),
        )
        assets["footer.png"] = pil_to_b64(page_image.crop(footer_box))
        (assets_dir / "footer.png").write_bytes(b64_to_bytes(assets["footer.png"]))

    (out_dir / "page.png").write_bytes(b64_to_bytes(assets["page.png"]))
    (out_dir / "native_text.txt").write_text(
        "\n".join(line["text"] for line in native_lines) + "\n", encoding="utf-8"
    )
    (out_dir / "ocr_text.txt").write_text(
        "\n".join(f"{line['score']:.4f}\t{line['text']}" for line in ocr_lines) + "\n",
        encoding="utf-8",
    )

    result = {
        "schema": RESULT_SCHEMA,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "path": str(pdf_path),
            "name": pdf_path.name,
            "size_bytes": pdf_path.stat().st_size,
            "sha256": hash_before,
            "pages": doc.page_count,
            "page_rect_pts": [float(v) for v in page.rect],
            "pdf_metadata": {k: v for k, v in doc.metadata.items() if v},
        },
        "environment": collect_versions(),
        "method": {
            "render_dpi": rules.RENDER_DPI,
            "symbol_detection": rules.SYMBOL_DETECTION_STATUS,
            "native_text": "Eingebetteter PDF-Text (PyMuPDF get_text 'dict')",
            "ocr": "RapidOCR (ONNX Runtime), vollständig lokal, ein Lauf",
            "matching": rules.SYMBOL_DETECTION_MESSAGE,
            "graphics_algorithm": graphic_candidates.ALGORITHM,
        },
        "render": {
            "dpi": rules.RENDER_DPI,
            "width_px": page_image.width,
            "height_px": page_image.height,
        },
        "reference": None,
        "contact_source": contact_source,
        "contacts": contacts,
        "native_lines": native_lines,
        "ocr_lines": ocr_lines,
        "symbols": symbols,
        "disclaimers": list(DISCLAIMERS),
        "assets": assets,
    }
    doc.close()

    (out_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    render_report(result, out_dir)
    try:
        import catalog_store

        catalog_store.CatalogStore().import_result(result, out_dir / "result.json")
    except Exception as exc:
        print(f"Hinweis: Katalog-Import fehlgeschlagen: {exc}", file=sys.stderr)
    return result


def _matrix_symbol_label(sym):
    if sym.get("candidate_kind") == "graphic_region":
        return (sym.get("proposal") or {}).get("suggested_name") or sym.get("label", "")
    return sym.get("label", "")


def build_matrix(result):
    src = result.get("source") or {}
    contacts = result.get("contacts") or {}
    fields = contacts.get("fields") or {}
    header = (
        ["PDF", "Quellpfad", "SHA-256"]
        + list(CONTACT_FIELDS)
        + ["Kontaktstatus", "Original-Kontaktzeile"]
        + [_matrix_symbol_label(s) for s in result.get("symbols", [])]
    )
    row = [src.get("name", ""), src.get("path", ""), src.get("sha256", "")]
    row += [fields.get(name, UNKNOWN) for name in CONTACT_FIELDS]
    row += [contacts.get("status", UNKNOWN), contacts.get("original", "")]
    row += [s.get("status", UNKNOWN) for s in result.get("symbols", [])]
    return header, row


def write_matrix_csv(result, path):
    header, row = build_matrix(result)
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";", lineterminator="\r\n")
        writer.writerow([csv_safe(cell) for cell in header])
        writer.writerow([csv_safe(cell) for cell in row])


def _img_tag(b64, alt):
    if not b64:
        return ""
    clean = decode_asset_b64(b64)
    return f'<img src="data:image/png;base64,{clean}" alt="{esc(alt)}">'


def _status_badge(status):
    cls = "unknown"
    if status == "Kandidat – prüfen":
        cls = "cand"
    elif status in ("Aus PDF-Text gelesen",):
        cls = "ok"
    return f'<span class="badge {cls}">{esc(status)}</span>'


def build_html(result):
    from catalog_rules import is_legacy_template_result, material_safe_display

    src = result.get("source") or {}
    env = result.get("environment") or {}
    method = result.get("method") or {}
    render = result.get("render") or {}
    contacts = result.get("contacts") or {}
    fields = contacts.get("fields") or {}
    symbols = result.get("symbols", [])
    captured = bool(result.get("input_capture"))
    assets = result.get("assets") or {}
    disclaimers = result.get("disclaimers") or list(DISCLAIMERS)
    legacy = is_legacy_template_result(result)
    detection_disabled = method.get("symbol_detection") == "disabled"
    graphics_mode = method.get("symbol_detection") == "graphic_candidates" or any(
        s.get("candidate_kind") == "graphic_region" for s in symbols
    )
    page_w = render.get("width_px") or 1
    page_h = render.get("height_px") or 1
    parts = []
    add = parts.append

    add("<!DOCTYPE html><html lang=\"de\"><head><meta charset=\"utf-8\">")
    add('<meta name="viewport" content="width=device-width, initial-scale=1">')
    add(f"<title>Etiketten-Analyse – {esc(src.get('name'))}</title>")
    add(f"<style>{CSS}</style></head><body><main>")

    add("<h1>Etiketten-Analyse (Prototyp)</h1>")
    add(
        f'<div class="muted">Quelle: <span class="mono">{esc(src.get("path"))}</span><br>'
        f'SHA-256: <span class="mono">{esc(src.get("sha256"))}</span> · '
        f'{esc(src.get("size_bytes"))} Bytes · {esc(src.get("pages"))} Seite · '
        f'Stand: {esc(result.get("generated_at"))}</div>'
    )
    add(
        '<div class="badges">'
        '<span class="badge ok">Vollständig lokal / offline</span>'
        + ('<span class="badge">Gespeicherter Seitenbefund; PDF nicht erneut geöffnet</span>'
           if captured else '<span class="badge">Quelldatei unverändert (SHA-256 geprüft)</span>')
        +
        '<span class="badge">Alle Treffer sind Kandidaten – Prüfung erforderlich</span>'
        "</div>"
    )
    if legacy:
        add(
            '<div class="card"><p><strong>Historischer Bericht:</strong> '
            "altes Vorlagenverfahren entfernt; diese Symboltreffer werden nicht "
            "in den Katalog übernommen.</p></div>"
        )

    add("<h2>Methode &amp; Umfang</h2><div class=\"card\">")
    add("<ul class=\"scope\">")
    add(f"<li>Seitenbild: {esc(method.get('render_dpi'))} dpi; "
        + ("aus gespeichertem Befund übernommen." if captured else "aus dieser PDF gerendert.")
        + "</li>")
    add(f"<li>Kontaktzeile: {esc(method.get('native_text'))}; Fallback: {esc(method.get('ocr'))}.</li>")
    add(f"<li>Symbole: {esc(method.get('matching'))}.</li>")
    if method.get("graphics_algorithm"):
        add(f"<li>Grafiksuche: {esc(method.get('graphics_algorithm'))}.</li>")
    if result.get("input_capture"):
        add("<li>Quelle: gespeichertes Seitenbild und Seitentexte aus dem Pilot-Einzug "
            "– keine neue PDF-/Seiten-OCR-Analyse.</li>")
    if method.get("match_threshold") is not None:
        add(f"<li>Schwellenwert Vorlagenabgleich (historisch): {esc(method.get('match_threshold'))}.</li>")
    if detection_disabled:
        add("<li><strong>Symbolerkennung noch nicht eingerichtet.</strong></li>")
    for note in disclaimers:
        add(f"<li>{esc(note)}</li>")
    add("</ul></div>")

    header, row = build_matrix(result)
    n_sym = len(symbols)
    prefix = "Vorlage: "
    if n_sym:
        if graphics_mode:
            add("<h2>Grafikkandidatenmatrix – keine bestätigten Symbole "
                "(eine Zeile)</h2><div class=\"card matrix-wrap\">")
            prefix = "Kandidat: "
        else:
            add("<h2>Vorlagen-Kandidatenmatrix – keine bestätigten Materialcodes "
                "(eine Zeile)</h2><div class=\"card matrix-wrap\">")
    else:
        add("<h2>Ergebnis-Matrix (eine Zeile)</h2><div class=\"card matrix-wrap\">")
    add('<table class="matrix"><thead><tr>')
    for i, cell in enumerate(header):
        shown = f"{prefix}{cell}" if n_sym and i >= len(header) - n_sym else cell
        add(f"<th>{esc(shown)}</th>")
    add("</tr></thead><tbody><tr>")
    for cell in row:
        add(f"<td>{esc(cell)}</td>")
    add("</tr></tbody></table>")
    add('<p class="muted">Downloads: <a href="matrix.csv" download>matrix.csv</a> · '
        '<a href="result.json" download>result.json</a></p></div>')

    add("<h2>Hersteller / Fußzeile</h2><div class=\"card\">")
    add(f'<p>Status: {_status_badge(contacts.get("status", UNKNOWN))} '
        f'<span class="muted">Quelle: {esc(result.get("contact_source") or "–")}</span></p>')
    if contacts.get("original"):
        add(f'<p class="mono">Originalzeile: {esc(contacts.get("original"))}</p>')
    if assets.get("footer.png"):
        add('<div class="crop-row"><figure>' + _img_tag(assets["footer.png"], "Fußzeile")
            + "<figcaption class=\"muted\">Bildausschnitt Fußzeile</figcaption></figure></div>")
    if fields:
        add('<table class="fields"><tbody>')
        for name in CONTACT_FIELDS:
            if name in fields:
                add(f"<tr><th>{esc(name)}</th><td>{esc(fields[name])}</td></tr>")
        add("</tbody></table>")
    else:
        add('<p class="muted">Keine Kontaktfelder extrahiert.</p>')
    add("</div>")

    if detection_disabled:
        add("<h2>Symbole</h2><div class=\"card\">")
        add("<p><strong>Symbolerkennung noch nicht eingerichtet.</strong></p>")
        add(f'<p class="muted">{esc(method.get("matching") or "")}</p>')
        add('<p class="muted">Leere Symbolergebnisse sind keine Aussage über vorhandene '
            "oder fehlende Symbole.</p></div>")
    elif graphics_mode:
        add("<h2>Grafikkandidaten – keine bestätigten Symbole</h2>")
        add('<p class="muted">Vorlagenfreie Bereichssuche + lokale Ausschnitt-OCR. '
            "Kein trainiertes Symbolmodell; auch Logos, Mengenzeichen, Abbildungen "
            "oder Textreste können Kandidaten sein. Textvorschläge sind unbestätigt.</p>")
        add('<div class="sym-grid">')
        for idx, sym in enumerate(symbols, 1):
            sid = sym.get("id", "")
            proposal = sym.get("proposal") or {}
            crop_ocr = sym.get("crop_ocr") or {}
            add('<div class="sym-card">')
            add(f"<h3>{idx}. {esc(material_safe_display(sym))}</h3>")
            add(
                f'<p>{_status_badge(sym.get("status", UNKNOWN))} '
                f'<span class="muted">Quelle Vorschlag: '
                f'{esc(proposal.get("suggestion_source") or "–")}</span></p>'
            )
            if proposal.get("observed_text"):
                add(f'<p class="muted">Gelesener Text: {esc(proposal["observed_text"])}</p>')
            if proposal.get("warning"):
                add(f'<p class="muted">{esc(proposal["warning"])}</p>')
            add('<div class="sym-imgs"><figure>')
            add(_img_tag(assets.get(f"cand_{sid}.png"), f"Kandidat {sid}"))
            add("<figcaption>Kandidat</figcaption></figure></div>")
            raw_lines = crop_ocr.get("lines") or []
            if raw_lines:
                add("<details><summary>Rohe Ausschnitt-OCR</summary><pre class=\"raw\">")
                add(esc("\n".join(
                    f"{line.get('score', 0):.3f}\t{line.get('text', '')}"
                    for line in raw_lines
                )))
                add("</pre></details>")
            bbox = sym.get("bbox_pixels")
            if bbox:
                add(
                    f'<p class="muted">Position im Seitenbild: x={bbox[0]}, y={bbox[1]}, '
                    f'w={bbox[2] - bbox[0]}, h={bbox[3] - bbox[1]} px @ {esc(render.get("dpi"))} dpi</p>'
                )
            add("</div>")
        add("</div>")
    elif symbols:
        add("<h2>Symbol-Vorlagen-Kandidaten (Referenz-Vorlagenabgleich)</h2>")
        add('<p class="muted">Links: Ausschnitt aus der Nutzerreferenz. Rechts: bester Kandidat im '
            "gerenderten Etikett. Der Wert ist eine Ähnlichkeit (0–1), keine Wahrscheinlichkeit.</p>")
        add('<div class="sym-grid">')
        for idx, sym in enumerate(symbols, 1):
            sid = sym.get("id", "")
            add('<div class="sym-card">')
            add(f"<h3>{idx}. {esc(material_safe_display(sym))}</h3>")
            add(f'<p class="muted">Referenzvorlage: {esc(sym.get("label"))}</p>')
            score = sym.get("score")
            score_text = f"{score:.4f}" if isinstance(score, (int, float)) else UNKNOWN
            add(
                f'<p>{_status_badge(sym.get("status", UNKNOWN))} '
                f"Ähnlichkeit: <strong>{esc(score_text)}</strong></p>"
            )
            add('<div class="sym-imgs"><figure>')
            add(_img_tag(assets.get(f"ref_{sid}.png"), f"Referenz {sid}"))
            add("<figcaption>Referenz</figcaption></figure><figure>")
            if assets.get(f"cand_{sid}.png"):
                add(_img_tag(assets[f"cand_{sid}.png"], f"Kandidat {sid}"))
                add("<figcaption>Kandidat</figcaption>")
            else:
                add('<div class="no-cand">kein Kandidat</div><figcaption>Kandidat</figcaption>')
            add("</figure></div>")
            bbox = sym.get("bbox_pixels")
            if bbox:
                add(
                    f'<p class="muted">Position im Seitenbild: x={bbox[0]}, y={bbox[1]}, '
                    f'w={bbox[2] - bbox[0]}, h={bbox[3] - bbox[1]} px @ {esc(render.get("dpi"))} dpi</p>'
                )
            else:
                add('<p class="muted">Keine Position über dem Schwellenwert gefunden.</p>')
            add("</div>")
        add("</div>")

    add("<h2>Seitenvorschau mit Markierungen</h2><div class=\"card\">")
    add('<label><input type="checkbox" id="ovl-toggle" checked> Markierungen anzeigen</label><br>')
    if symbols:
        if graphics_mode:
            add('<p class="muted">Nummern entsprechen den Grafikkandidaten-Karten; '
                "Vorschläge sind unbestätigt.</p>")
        else:
            add('<p class="muted">Nummern entsprechen den Symbol-Karten; Materialcodes sind unbestätigt.</p>')
    add('<div class="page-wrap" id="page-wrap">')
    add(_img_tag(assets.get("page.png"), "Seite 1"))
    for idx, sym in enumerate(symbols, 1):
        bbox = sym.get("bbox_pixels")
        if not bbox:
            continue
        left, top = bbox[0] / page_w * 100, bbox[1] / page_h * 100
        width, height = (bbox[2] - bbox[0]) / page_w * 100, (bbox[3] - bbox[1]) / page_h * 100
        add(
            f'<div class="ovl" data-symbol-id="{esc(sym.get("id"))}" '
            f'title="{esc(material_safe_display(sym))}" '
            f'style="left:{left:.3f}%;top:{top:.3f}%;'
            f'width:{width:.3f}%;height:{height:.3f}%">'
            f'<span>{idx}</span></div>'
        )
    if contacts.get("bbox_points"):
        fx0, fy0, fx1, fy1 = contacts["bbox_points"]
        dpi_scale = (render.get("dpi") or 300) / 72.0
        left, top = fx0 * dpi_scale / page_w * 100, fy0 * dpi_scale / page_h * 100
        width = (fx1 - fx0) * dpi_scale / page_w * 100
        height = (fy1 - fy0) * dpi_scale / page_h * 100
        add(
            f'<div class="ovl footer" style="left:{left:.3f}%;top:{top:.3f}%;'
            f'width:{width:.3f}%;height:{height:.3f}%"><span>Fußzeile</span></div>'
        )
    add("</div></div>")

    add("<h2>Rohdaten</h2>")
    add("<details><summary>Nativer PDF-Text (Zeilen)</summary><pre class=\"raw\">")
    add(esc("\n".join(line.get("text", "") for line in result.get("native_lines", []))))
    add("</pre></details>")
    add("<details><summary>OCR-Text (Lokale OCR, Zeile mit Score)</summary><pre class=\"raw\">")
    add(
        esc(
            "\n".join(
                f"{line.get('score', 0):.4f}\t{line.get('text', '')}"
                for line in result.get("ocr_lines", [])
            )
        )
    )
    add("</pre></details>")
    add("<details><summary>Technische Umgebung</summary><pre class=\"raw\">")
    add(esc(json.dumps(env, ensure_ascii=False, indent=1)))
    add("</pre></details>")

    add(
        "<footer>Lokaler Prototyp; keine Konformitätsprüfung. Grafische Kandidaten "
        "sind keine bestätigten Symbole. Unbekannte Zeichen werden nicht automatisch "
        "klassifiziert; Sprachklassifikation und Barcode-Erkennung sind nicht implementiert.</footer>"
    )
    add(
        "<script>document.getElementById('ovl-toggle').addEventListener('change',function(e){"
        "document.querySelectorAll('.ovl').forEach(function(b){"
        "b.style.display=e.target.checked?'block':'none';});});</script>"
    )
    add("</main></body></html>")
    return "".join(parts)


def render_report(result, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(build_html(result), encoding="utf-8")
    write_matrix_csv(result, out_dir / "matrix.csv")
    assets = result.get("assets") or {}
    if assets:
        assets_dir = out_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        for name, b64 in assets.items():
            safe_name = Path(name).name
            if not safe_name.endswith(".png"):
                continue
            target = assets_dir / safe_name if safe_name != "page.png" else out_dir / "page.png"
            target.write_bytes(base64.b64decode(decode_asset_b64(b64)))


def load_frozen_result(path):
    path = Path(path)
    if not path.is_file():
        raise FriendlyError(f"Frozen result nicht gefunden: {path}")
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FriendlyError(f"Frozen result ist nicht lesbar: {exc}") from exc
    if not isinstance(result, dict) or result.get("schema") != RESULT_SCHEMA:
        raise FriendlyError(f"Datei ist kein {RESULT_SCHEMA}-Ergebnis: {path}")
    return result, path


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Eigenständiger Ein-PDF-Etiketten-Prototyp (nur lokale Verarbeitung)."
    )
    parser.add_argument(
        "--pdf",
        default=DEFAULT_PDF,
        help="Pfad zu genau einer einseitigen PDF (Standard: Beispiel-UNC-Pfad).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Ausgabeverzeichnis (explizit: muss leer sein; ohne Angabe: "
        "artifacts/result falls leer, sonst eindeutiges Schwester-Verzeichnis).",
    )
    parser.add_argument(
        "--render-json",
        metavar="RESULT_JSON",
        help="Bericht aus eingefrorenem result.json neu rendern "
        "(ohne PDF-/OCR-/Matching-Zugriff).",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Bericht nach Erstellung nicht automatisch öffnen.",
    )
    return parser.parse_args(argv)


def maybe_open(out_dir, no_open):
    if no_open:
        return
    index = Path(out_dir) / "index.html"
    if index.is_file():
        try:
            os.startfile(str(index))  # noqa: S606 - lokaler Bericht, gewollt
        except OSError as exc:
            print(f"Hinweis: Bericht konnte nicht geöffnet werden: {exc}", file=sys.stderr)


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.render_json:
            result, json_path = load_frozen_result(args.render_json)
            out_dir = Path(args.output) if args.output is not None else json_path.parent
            render_report(result, out_dir)
            if out_dir.resolve() != json_path.parent.resolve():
                (out_dir / "result.json").write_bytes(json_path.read_bytes())
            print(f"Bericht neu gerendert aus {json_path} -> {out_dir}")
        else:
            out_dir = resolve_analysis_output(args.output)
            result = run_analysis(Path(args.pdf), out_dir)
            print(f"Analyse abgeschlossen: {out_dir}")
            print(f"Kontaktstatus: {result['contacts']['status']}")
        maybe_open(out_dir, args.no_open)
        return 0
    except FriendlyError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
