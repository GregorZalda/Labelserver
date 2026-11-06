"""Build the Stichproben dashboard from frozen per-ID results + manual review.

Presentation only: copies already-generated artifacts byte-identically and
renders the lead's manual annotations verbatim. No re-analysis, no metrics.
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

import analyze_label as al

BASE_DIR = Path(__file__).resolve().parent
SAMPLES_DIR = BASE_DIR / "artifacts" / "samples-20261105"
MANUAL_PATH = SAMPLES_DIR / "manual-review.json"
OUT_DIR = BASE_DIR / "artifacts" / "result" / "stichproben"
LOG_PATH = SAMPLES_DIR / "dashboard-verification.log"
ID_RE = re.compile(r"^[a-z0-9_]+$")
COPY_FILES = ("index.html", "result.json", "matrix.csv", "overview.png", "review.png")
EXTRA_CSV_COLUMNS = ("Prüfung Symbole", "Prüfung Kontaktdaten")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def load_manual(path):
    if not path.is_file():
        raise al.FriendlyError(f"Manuelle Prüfung fehlt: {path}")
    manual = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manual.get("findings"), list):
        raise al.FriendlyError("manual-review.json enthält keine findings-Liste.")
    return manual


def validate_ids(findings):
    for f in findings:
        sid = f.get("id", "")
        if not ID_RE.fullmatch(sid):
            raise al.FriendlyError(f"Ungültige ID in manual-review.json: {sid!r}")


def copy_artifacts(findings, samples_dir, out_dir, log):
    for f in findings:
        sid = f["id"]
        src_dir = samples_dir / sid
        dst_dir = out_dir / sid
        if not src_dir.is_dir():
            raise al.FriendlyError(f"Sample-Verzeichnis fehlt: {src_dir}")
        dst_dir.mkdir(parents=True, exist_ok=True)
        for name in COPY_FILES:
            src = src_dir / name
            dst = dst_dir / name
            if not src.is_file():
                raise al.FriendlyError(f"Eingefrorenes Artefakt fehlt: {src}")
            data = src.read_bytes()
            dst.write_bytes(data)
            ok = sha256_bytes(data) == sha256_bytes(dst.read_bytes())
            log.append(f"copy {sid}/{name}: {len(data)} bytes, identical={ok}")
            if not ok:
                raise al.FriendlyError(f"Kopie weicht vom Original ab: {dst}")


def build_combined_csv(findings, samples_dir, out_dir, log):
    header_ref = None
    rows = []
    for f in findings:
        sid = f["id"]
        csv_path = samples_dir / sid / "matrix.csv"
        with open(csv_path, encoding="utf-8-sig", newline="") as handle:
            parsed = list(csv.reader(handle, delimiter=";"))
        if len(parsed) != 2:
            raise al.FriendlyError(
                f"Unerwartete Zeilenzahl in {csv_path}: {len(parsed)} (erwartet: 2)"
            )
        if header_ref is None:
            header_ref = parsed[0]
        elif parsed[0] != header_ref:
            raise al.FriendlyError(f"CSV-Header abweichend in {csv_path} – Abbruch.")
        rows.append((f, parsed[1]))
        log.append(f"csv {sid}: header match, 1 data row")
    out_csv = out_dir / "matrix.csv"
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";", lineterminator="\r\n")
        writer.writerow([al.csv_safe(c) for c in header_ref + list(EXTRA_CSV_COLUMNS)])
        for f, row in rows:
            writer.writerow(
                [al.csv_safe(c) for c in row]
                + [al.csv_safe(f.get("symbol_review", "")), al.csv_safe(f.get("contact_review", ""))]
            )
    log.append(f"csv combined: {len(rows)} rows + {len(EXTRA_CSV_COLUMNS)} review columns -> {out_csv}")


def _ul(items):
    return "".join(f"<li>{al.esc(str(i))}</li>" for i in items)


def build_dashboard(manual, samples_dir, out_dir, log):
    findings = manual["findings"]
    parts = []
    add = parts.append
    add('<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">')
    add('<meta name="viewport" content="width=device-width, initial-scale=1">')
    add("<title>Stichproben-Prüfung (manuelle Auswertung)</title>")
    add(f"<style>{al.CSS}"
        ".finding{margin-bottom:18px}.kv td:first-child{background:#eef2f6;font-weight:600;width:230px}"
        ".auto{border-left:4px solid #c3cad2;padding-left:12px;margin-top:10px}"
        ".manual{border-left:4px solid #175cd3;padding-left:12px}"
        "h3.tag{font-size:12px;text-transform:uppercase;color:var(--muted);margin:10px 0 4px;letter-spacing:.04em}"
        "</style></head><body><main>")
    add("<h1>Stichproben-Prüfung — manuelle Auswertung</h1>")
    add('<p class="muted">Darstellung eingefrorener Ergebnisse plus visueller Prüfung. '
        "Keine erneute Analyse, keine Metriken.</p>")

    add("<h2>Umfang &amp; Gesamteinschätzung (manuell geprüft)</h2><div class=\"card\">")
    add(f"<p><strong>Umfang:</strong> {al.esc(manual.get('scope', ''))}</p>")
    add(f"<p><strong>Gesamt:</strong> {al.esc(manual.get('overall', ''))}</p>")
    add('<p class="muted">Downloads: <a href="matrix.csv" download>matrix.csv (kombiniert)</a> · '
        '<a href="manual-review.json" download>manual-review.json (Prüfprotokoll, unverändert)</a></p>')
    add("</div>")

    add("<h2>Befunde je Datei</h2>")
    for f in findings:
        sid = f["id"]
        add(f'<div class="card finding" id="{al.esc(sid)}">')
        add(f"<h3 class=\"tag\">Datei</h3><p class=\"mono\">{al.esc(f.get('file', ''))}</p>")
        add('<div class="manual"><h3 class="tag">Visuelle Prüfung dieser Stichprobe</h3>')
        add('<table class="fields kv"><tbody>')
        add(f"<tr><th>Symbol-Prüfung</th><td>{al.esc(f.get('symbol_review', ''))}</td></tr>")
        add(f"<tr><th>Kontakt-Prüfung</th><td>{al.esc(f.get('contact_review', ''))}</td></tr>")
        add(f"<tr><th>Kontakt-Beleg</th><td>{al.esc(f.get('contact_evidence', ''))}</td></tr>")
        add(f"<tr><th>Begründung</th><td>{al.esc(f.get('reason', ''))}</td></tr>")
        if f.get("expected_reference_symbols"):
            add(f"<tr><th>Erwartete Referenzsymbole</th><td><ul class=\"scope\">{_ul(f['expected_reference_symbols'])}</ul></td></tr>")
        if f.get("outside_reference"):
            add(f"<tr><th>Außerhalb der Referenz</th><td><ul class=\"scope\">{_ul(f['outside_reference'])}</ul></td></tr>")
        add("</tbody></table></div>")

        frozen = json.loads((samples_dir / sid / "result.json").read_text(encoding="utf-8"))
        contacts = frozen.get("contacts") or {}
        symbols = frozen.get("symbols") or []
        add('<div class="auto"><h3 class="tag">Automatisches Ergebnis (ungeprüft, eingefroren)</h3>')
        add(f'<p>Kontaktstatus: <strong>{al.esc(contacts.get("status", al.UNKNOWN))}</strong> '
            f'(Quelle: {al.esc(frozen.get("contact_source") or "–")})</p>')
        add('<ul class="scope">')
        for s in symbols:
            add(f"<li>{al.esc(s.get('label', s.get('id', '')))}: {al.esc(s.get('status', al.UNKNOWN))}"
                + (f" — Ähnlichkeit {s['score']:.4f}" if isinstance(s.get("score"), (int, float)) else "")
                + "</li>")
        add("</ul>")
        add(f'<p>Links: <a href="{al.esc(sid)}/index.html">Bericht</a> · '
            f'<a href="{al.esc(sid)}/overview.png">Seitenbild</a> · '
            f'<a href="{al.esc(sid)}/review.png">Kontaktabzug</a> · '
            f'<a href="{al.esc(sid)}/result.json">result.json</a> · '
            f'<a href="{al.esc(sid)}/matrix.csv">matrix.csv</a></p>')
        add("</div></div>")

    add("<footer>Die visuellen Prüfanmerkungen beziehen sich nur auf diese Stichprobe; automatische "
        "Statusangaben sind eingefrorene Kandidatenergebnisse. Ähnlichkeit ist keine Wahrscheinlichkeit.</footer>")
    add("</main></body></html>")
    html_out = "".join(parts)
    (out_dir / "index.html").write_text(html_out, encoding="utf-8")
    log.append(f"dashboard: {len(findings)} findings -> {out_dir / 'index.html'}")


def verify_links(out_dir, findings, log):
    rel = ["index.html", "matrix.csv", "manual-review.json"]
    ok = True
    for r in rel:
        exists = (out_dir / r).is_file()
        log.append(f"link {r}: exists={exists}")
        ok &= exists
    for f in findings:
        sid = f["id"]
        for name in COPY_FILES:
            exists = (out_dir / sid / name).is_file()
            log.append(f"link {sid}/{name}: exists={exists}")
            ok &= exists
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stichproben-Dashboard aus eingefrorenen Ergebnissen")
    parser.add_argument("--manual", default=str(MANUAL_PATH))
    parser.add_argument("--samples", default=str(SAMPLES_DIR))
    parser.add_argument("--output", default=str(OUT_DIR))
    parser.add_argument("--log", default=str(LOG_PATH))
    args = parser.parse_args(argv)

    try:
        manual_path = Path(args.manual)
        samples_dir = Path(args.samples)
        out_dir = Path(args.output)
        log_path = Path(args.log)

        manual = load_manual(manual_path)
        validate_ids(manual["findings"])

        out_dir.mkdir(parents=True, exist_ok=True)
        log = ["# dashboard-verification.log (UTF-8)", f"manual: {manual_path}"]

        copy_artifacts(manual["findings"], samples_dir, out_dir, log)
        manual_bytes = manual_path.read_bytes()
        (out_dir / "manual-review.json").write_bytes(manual_bytes)
        log.append(f"copy manual-review.json: {len(manual_bytes)} bytes, "
                   f"identical={sha256_bytes(manual_bytes) == sha256_bytes((out_dir / 'manual-review.json').read_bytes())}")

        build_combined_csv(manual["findings"], samples_dir, out_dir, log)
        build_dashboard(manual, samples_dir, out_dir, log)
        all_ok = verify_links(out_dir, manual["findings"], log)
        log.append(f"ALL_LINKS_OK={all_ok}")

        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("\n".join(log) + "\n", encoding="utf-8")
        print(f"Dashboard -> {out_dir / 'index.html'}")
        print(f"Log -> {log_path} (ALL_LINKS_OK={all_ok})")
        return 0 if all_ok else 1
    except al.FriendlyError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
