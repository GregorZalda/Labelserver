import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import analyze_label as analysis


SYMBOL_IDS = (
    "pet_01", "abs_07", "fe_40", "recycling", "tidyman",
    "pap_21", "triman", "info_tri", "al_azul",
)
TERMINAL = {"success", "reused", "success_multipage", "failed", "changed_since_selection"}
AVAILABLE = {"success", "reused", "success_multipage"}


def build_snapshot(root):
    selection = json.loads((root / "selection.json").read_text(encoding="utf-8"))
    state = json.loads((root / "batch-state.json").read_text(encoding="utf-8"))
    if analysis.sha256_file(root / "selection.json") != state["selection_sha256"]:
        raise ValueError("Auswahlmanifest passt nicht zum Lauf.")
    if len(selection["selected"]) != 100 or len(state["jobs"]) != 100:
        raise ValueError("Auswertung erwartet genau 100 ausgewählte Dateien.")
    if [item["path"] for item in selection["selected"]] != [job["path"] for job in state["jobs"]]:
        raise ValueError("Auswahl und Lauf haben unterschiedliche Reihenfolgen/Pfade.")
    supplement_path = root / "multipage-state.json"
    supplement = json.loads(supplement_path.read_text(encoding="utf-8")) if supplement_path.exists() else None
    supplement_jobs = {job["id"]: job for job in supplement["jobs"]} if supplement else {}
    manual_path = root / "manual-review.json"
    manual = json.loads(manual_path.read_text(encoding="utf-8")) if manual_path.exists() else None
    states = Counter()
    symbols = Counter({key: 0 for key in SYMBOL_IDS})
    field_histogram = Counter({str(i): 0 for i in range(8)})
    rows = []
    for baseline_job in state["jobs"]:
        job = dict(baseline_job)
        result_path = root / job["id"] / "result.json"
        report_href = f"{job['id']}/index.html"
        supplementary = supplement_jobs.get(job["id"])
        if supplementary:
            if job["state"] != "failed" or supplementary["path"] != job["path"]:
                raise ValueError("Mehrseitenergänzung passt nicht zur ursprünglichen Fehlerdatei.")
            if supplementary["state"] == "success":
                job.update(
                    state="success_multipage", source_sha256=supplementary["source_sha256"],
                    baseline_error=job.pop("error", ""),
                )
                result_path = root / "multipage" / job["id"] / "document-result.json"
                report_href = f"multipage/{job['id']}/index.html"
            elif supplementary["state"] == "running":
                job["state"] = "multipage_running"
            else:
                job["error"] = supplementary.get("error", job.get("error", ""))
        states[job["state"]] += 1
        row = {
            **job, "report_href": report_href, "symbols": {}, "contact_fields": {},
            "contact_original": "", "contact_status": "",
        }
        if job["state"] in AVAILABLE:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if (
                result["source"]["path"].casefold() != job["path"].casefold()
                or result["source"]["sha256"] != job["source_sha256"]
                or tuple(symbol["id"] for symbol in result["symbols"]) != SYMBOL_IDS
            ):
                raise ValueError(f"Unerwartete Ergebniszuordnung: {job['id']}")
            fields = result["contacts"]["fields"]
            field_count = sum(bool(fields.get(key)) for key in analysis.CONTACT_FIELDS)
            field_histogram[str(field_count)] += 1
            row.update(
                contact_fields=fields,
                contact_field_count=field_count,
                contact_original=result["contacts"]["original"],
                contact_status=result["contacts"]["status"],
                native_line_count=len(result["native_lines"]),
                ocr_line_count=len(result["ocr_lines"]),
                page_count=result["source"]["pages"],
                symbols={symbol["id"]: symbol["status"] for symbol in result["symbols"]},
                symbol_labels={symbol["id"]: symbol["label"] for symbol in result["symbols"]},
                source_sha256=result["source"]["sha256"],
            )
            for symbol in result["symbols"]:
                if symbol["status"] == "Kandidat – prüfen":
                    if not symbol.get("bbox_pixels") and not symbol.get("page_evidence"):
                        raise ValueError(f"Kandidat ohne Fundstelle: {job['id']}/{symbol['id']}")
                    symbols[symbol["id"]] += 1
                elif symbol["status"] != analysis.UNKNOWN:
                    raise ValueError(f"Unbekannter Symbolstatus: {job['id']}/{symbol['id']}")
        rows.append(row)
    analyzed = sum(states[key] for key in AVAILABLE)
    if sum(field_histogram.values()) != analyzed:
        raise ValueError("Kontaktfeldzählung inkonsistent.")
    return {
        "schema": "pdf-probe-latest-summary/1",
        "selection": selection,
        "run_started_at_utc": state["started_at_utc"],
        "run_finished_at_utc": supplement["finished_at_utc"] if supplement else state["finished_at_utc"],
        "baseline_state_counts": dict(Counter(job["state"] for job in state["jobs"])),
        "multipage_supplement": supplement,
        "manual_review": manual,
        "state_counts": dict(states),
        "analyzed_result_count": analyzed,
        "terminal_count": sum(states[key] for key in TERMINAL),
        "candidate_document_counts": dict(symbols),
        "contact_field_count_histogram": dict(field_histogram),
        "scope": state["recognition_scope"] + (
            " Ergänzung: Im Hauptlauf wegen Mehrseitigkeit abgewiesene Originale werden seitenweise mit derselben Erkennung verarbeitet und je Originaldokument zusammengeführt."
            if supplement else ""
        ),
        "interpretation": "Gezählt werden Dokumente mit automatisch ausgegebenem Symbol-Kandidaten, nicht bestätigte Vorkommen. Nicht sicher erkannt bedeutet nicht abwesend. Kontaktfeldanzahl ist keine Vollständigkeits- oder Richtigkeitsprüfung. Bei mehreren Seiten werden unterschiedliche Kontaktwerte mit | erhalten; sie bilden nicht automatisch eine gemeinsame Adresse. Fehler/Pending gehen nicht als negative Erkennung in die Zählung ein. Keine PPWR-/DIN-/ISO-Konformitätsaussage.",
        "rows": rows,
    }


def write_csv(snapshot, root):
    header = [
        "Rang", "PDF", "Quellpfad", "Angelegt UTC", "Geändert UTC", "Bytes",
        "Laufstatus", "Kontaktstatus", "Strukturierte Kontaktfelder von 7",
        *analysis.CONTACT_FIELDS, "Original-Kontaktzeile",
        *SYMBOL_IDS, "SHA-256", "Fehler", "Sichtprüfung Symbole", "Sichtprüfung Kontakt",
    ]
    reviewed = {
        finding["id"]: finding
        for finding in (snapshot.get("manual_review") or {}).get("findings", [])
    }
    with (root / "matrix.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";", lineterminator="\r\n")
        writer.writerow(header)
        for row in snapshot["rows"]:
            manual = reviewed.get(row["id"], {})
            values = [
                row["rank"], row["filename"], row["path"], row["created_utc"],
                row["modified_utc"], row["size_bytes"], row["state"],
                row["contact_status"], row.get("contact_field_count", ""),
                *[row["contact_fields"].get(key, "") for key in analysis.CONTACT_FIELDS],
                row["contact_original"],
                *[row["symbols"].get(key, "Nicht ausgewertet") for key in SYMBOL_IDS],
                row.get("source_sha256", ""), row.get("error", ""),
                manual.get("symbol_review", ""), manual.get("contact_review", ""),
            ]
            if len(values) != len(header):
                raise ValueError("CSV-Spaltenzahl inkonsistent.")
            writer.writerow([analysis.csv_safe(value) for value in values])


def write_html(snapshot, root):
    esc = analysis.esc
    selected = snapshot["selection"]
    counts = snapshot["state_counts"]
    parts = [
        '<!doctype html><html lang="de"><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>100 neueste PDFs – lokale Auswertung</title>",
        f"<style>{analysis.CSS}td,th{{vertical-align:top}}.wrap{{overflow:auto}}"
        "button{padding:6px 12px}input{padding:8px;width:min(100%,500px)}"
        ".failed{background:#fff0ed}.pending{color:#666}</style><main>",
        "<h1>100 neueste PDFs – lokale Auswertung</h1>",
        f"<p>{esc(selected['selection'])}</p>",
        f"<p class='muted'>{esc(selected['limitations'])}</p>",
        f"<div class='card'><strong>Abgeschlossen: {snapshot['terminal_count']}/100</strong>"
        f" · auswertbare Ergebnisse: {snapshot['analyzed_result_count']}"
        f" · Fehler: {counts.get('failed', 0)}"
        f" · seit Auswahl geändert: {counts.get('changed_since_selection', 0)}"
        f"<br>Serverinventur: {selected['pdfs_with_readable_metadata']} PDFs mit lesbaren Metadaten"
        f" · Inventurfehler: {len(selected['scan_errors'])}</div>",
        f"<p>{esc(snapshot['scope'])}</p><p><strong>{esc(snapshot['interpretation'])}</strong></p>",
        '<p><a href="matrix.csv" download>Matrix CSV</a> · '
        '<a href="summary.json" download>Auswertung JSON</a> · '
        '<a href="selection.json" download>Auswahlliste</a> · '
        '<a href="batch-state.json" download>Laufprotokoll</a></p>',
    ]
    if not snapshot["run_finished_at_utc"]:
        parts.append("<p><strong>Der Lauf ist noch nicht abgeschlossen. Diese Ansicht ist ein Zwischenstand.</strong></p>")
    if selected["scan_errors"]:
        parts.append("<details><summary>Inventurfehler – Auswahl möglicherweise unvollständig</summary><pre>")
        parts.append(esc(json.dumps(selected["scan_errors"], ensure_ascii=False, indent=2)) + "</pre></details>")
    manual = snapshot.get("manual_review")
    if manual:
        parts.append("<h2>Visuelle Stichprobenprüfung</h2>")
        parts.append(f"<p><strong>{esc(manual['scope'])}</strong></p>")
        parts.append('<p><a href="manual-review.json">Prüfanmerkungen JSON</a></p>')
        for finding in manual["findings"]:
            parts.append(
                f"<div class='card'><h3>Rang {finding['rank']}: {esc(finding['file'])}</h3>"
                f"<p><strong>Symbole:</strong> {esc(finding['symbol_review'])}</p>"
                f"<p><strong>Kontakt:</strong> {esc(finding['contact_review'])}</p>"
                f"<p>{esc(finding.get('interpretation', ''))}</p></div>"
            )
    parts.append("<h2>Automatische Symbol-Kandidaten je Dokument</h2><table class='fields'>")
    parts.append("<tr><th>Referenztyp</th><th>Dokumente mit Kandidat</th></tr>")
    for key in SYMBOL_IDS:
        parts.append(f"<tr><td>{esc(key)}</td><td>{snapshot['candidate_document_counts'][key]}</td></tr>")
    parts.append("</table><h2>Kontaktfelder – nur Parserausgabe</h2><table class='fields'>")
    parts.append("<tr><th>Ausgegebene Felder von 7</th><th>Dokumente</th></tr>")
    for number in range(8):
        parts.append(f"<tr><td>{number}</td><td>{snapshot['contact_field_count_histogram'][str(number)]}</td></tr>")
    parts.append("</table><h2>Dateiliste</h2><input id='search' placeholder='Dateiname oder Status filtern'>"
                 "<div class='wrap'><table class='fields' id='files'><thead><tr>"
                 "<th>Rang</th><th>PDF / Einzelbericht</th><th>Angelegt UTC</th>"
                 "<th>Laufstatus</th><th>Kontaktfelder</th><th>Symbol-Kandidaten</th><th>Fehler</th>"
                 "</tr></thead><tbody>")
    for row in snapshot["rows"]:
        available = row["state"] in AVAILABLE
        name = esc(row["filename"])
        if available:
            name = f'<a href="{esc(row["report_href"])}">{name}</a>'
        candidates = ", ".join(key for key, value in row["symbols"].items() if value == "Kandidat – prüfen")
        cls = "failed" if row["state"] in {"failed", "changed_since_selection"} else ""
        parts.append(
            f"<tr class='{cls}'><td>{row['rank']}</td><td>{name}</td>"
            f"<td>{esc(row['created_utc'])}</td><td>{esc(row['state'])}</td>"
            f"<td>{row.get('contact_field_count', '–')}</td><td>{esc(candidates) if available else 'Nicht ausgewertet'}</td>"
            f"<td>{esc(row.get('error', ''))}</td></tr>"
        )
    parts.append("</tbody></table></div><script>"
                 "document.getElementById('search').addEventListener('input',function(){"
                 "var q=this.value.toLowerCase();document.querySelectorAll('#files tbody tr').forEach(function(r){"
                 "r.hidden=!r.textContent.toLowerCase().includes(q);});});"
                 "</script></main></html>")
    (root / "index.html").write_text("".join(parts), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    args = parser.parse_args(argv)
    root = Path(args.run)
    snapshot = build_snapshot(root)
    write_csv(snapshot, root)
    write_html(snapshot, root)
    (root / "summary.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "processed": snapshot["terminal_count"],
        "analyzed": snapshot["analyzed_result_count"],
        "states": snapshot["state_counts"],
        "symbol_candidates": snapshot["candidate_document_counts"],
        "contact_fields": snapshot["contact_field_count_histogram"],
    }, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
