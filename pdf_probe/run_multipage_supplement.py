import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import analyze_label as analysis
from run_latest_batch import now, save_state


def combine_pages(source_path, source_hash, page_results):
    fields = {}
    for key in analysis.CONTACT_FIELDS:
        values = list(dict.fromkeys(
            result["contacts"]["fields"][key]
            for result in page_results if result["contacts"]["fields"].get(key)
        ))
        if values:
            fields[key] = " | ".join(values)
    symbols = []
    ids = [symbol["id"] for symbol in page_results[0]["symbols"]]
    for symbol_id in ids:
        matches = []
        label = None
        for page_number, result in enumerate(page_results, 1):
            symbol = next(item for item in result["symbols"] if item["id"] == symbol_id)
            label = symbol["label"]
            if symbol["status"] == "Kandidat – prüfen":
                matches.append({"page": page_number, "bbox_pixels": symbol["bbox_pixels"]})
        symbols.append({
            "id": symbol_id, "label": label,
            "status": "Kandidat – prüfen" if matches else analysis.UNKNOWN,
            "bbox_pixels": None, "page_evidence": matches,
        })
    originals = [
        f"S. {number}: {result['contacts']['original']}"
        for number, result in enumerate(page_results, 1) if result["contacts"]["original"]
    ]
    return {
        "schema": "pdf-probe-multipage-result/1",
        "source": {"path": str(source_path), "sha256": source_hash, "pages": len(page_results)},
        "contacts": {
            "fields": fields, "original": "\n".join(originals),
            "status": "Seitenweise Parserausgabe – prüfen",
        },
        "symbols": symbols,
        "native_lines": [
            {**line, "page": number}
            for number, result in enumerate(page_results, 1) for line in result["native_lines"]
        ],
        "ocr_lines": [
            {**line, "page": number}
            for number, result in enumerate(page_results, 1) for line in result["ocr_lines"]
        ],
        "interpretation": "Symbol-Kandidaten werden je Originaldokument nur einmal gezählt, auch bei mehreren Seiten. Kontaktwerte aus mehreren Seiten bleiben bei Abweichungen mit | getrennt; dies ist keine verifizierte gemeinsame Herstelleradresse.",
    }


def write_document_index(folder, job, combined, page_count):
    esc = analysis.esc
    parts = [
        '<!doctype html><html lang="de"><meta charset="utf-8">',
        f"<title>{esc(job['filename'])} – seitenweise Auswertung</title>",
        f"<style>{analysis.CSS}</style><main><h1>{esc(job['filename'])}</h1>",
        f"<p>Original: {esc(job['path'])}</p><p>SHA-256: {esc(combined['source']['sha256'])}</p>",
        "<p>Die Original-PDF wurde nur gelesen. Jede Seite wurde lokal in eine Ein-Seiten-PDF "
        "exportiert und ihr 300-dpi-Rendering pixelidentisch mit dem Original verglichen. "
        "Die Einzelberichte nennen deshalb die lokale Exportdatei als technische Eingabe.</p>",
        f"<p>{esc(combined['interpretation'])}</p>",
        "<p>Unveränderter Prototyp: neun Vorlagen, bekannte Kontakt-/Materialnummernfehler. Keine Konformitätsaussage.</p><ul>",
    ]
    for number in range(1, page_count + 1):
        parts.append(f'<li><a href="page-{number:03d}/index.html">Seite {number}</a></li>')
    parts.append('</ul><a href="document-result.json">Zusammengeführtes Ergebnis JSON</a></main></html>')
    (folder / "index.html").write_text("".join(parts), encoding="utf-8")


def main(argv=None):
    import pymupdf
    import analysis_rules as rules

    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    args = parser.parse_args(argv)
    root = Path(args.run)
    baseline = json.loads((root / "batch-state.json").read_text(encoding="utf-8"))
    if not baseline["finished_at_utc"]:
        raise ValueError("Der Hauptlauf muss zuerst vollständig abgeschlossen sein.")
    selected = [
        job for job in baseline["jobs"]
        if job["state"] == "failed" and "Prototyp verarbeitet nur einseitige PDFs" in job.get("error", "")
    ]
    state_path = root / "multipage-state.json"
    script_hash = analysis.sha256_file(Path(__file__))
    rules_hash = analysis.sha256_file(analysis.BASE_DIR / "analysis_rules.py")
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state["script_sha256"] != script_hash or state["rules_sha256"] != rules_hash:
            raise ValueError("Supplement-Code geändert; Resume nicht zulässig.")
    else:
        state = {
            "schema": "pdf-probe-multipage-state/1", "started_at_utc": now(),
            "finished_at_utc": None, "script_sha256": script_hash,
            "rules_sha256": rules_hash, "jobs": [],
        }
    existing = {job["id"]: job for job in state["jobs"]}
    for job in selected:
        if job["id"] in existing and existing[job["id"]]["state"] in {"success", "failed"}:
            continue
        folder = root / "multipage" / job["id"]
        folder.mkdir(parents=True, exist_ok=True)
        record = existing.get(job["id"])
        if record is None:
            record = {"id": job["id"], "rank": job["rank"], "path": job["path"], "state": "running", "pages": []}
            state["jobs"].append(record)
        save_state(state_path, state)
        try:
            source = Path(job["path"])
            stat = source.stat()
            if stat.st_size != job["size_bytes"] or abs(stat.st_birthtime - job["created_timestamp"]) > 0.001:
                raise ValueError("Quelle seit Auswahl verändert.")
            from datetime import datetime, timezone
            if datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat() != job["modified_utc"]:
                raise ValueError("Änderungszeit der Quelle stimmt nicht mit Auswahl überein.")
            raw = source.read_bytes()
            source_hash = hashlib.sha256(raw).hexdigest()
            if record.get("source_sha256", source_hash) != source_hash:
                raise ValueError("Quelle seit Supplement-Beginn verändert.")
            record["source_sha256"] = source_hash
            results = []
            with pymupdf.open(stream=raw, filetype="pdf") as doc:
                if doc.needs_pass or doc.page_count <= 1:
                    raise ValueError("Unerwarteter Seiten-/Verschlüsselungsstatus.")
                record["page_count"] = doc.page_count
                for index in range(doc.page_count):
                    page_number = index + 1
                    exported_path = folder / f"input-page-{page_number:03d}.pdf"
                    target = folder / f"page-{page_number:03d}"
                    if not exported_path.exists():
                        with pymupdf.open() as exported:
                            exported.insert_pdf(doc, from_page=index, to_page=index)
                            exported.save(exported_path)
                    original_pixels = doc[index].get_pixmap(dpi=rules.RENDER_DPI)
                    with pymupdf.open(exported_path) as exported:
                        exported_pixels = exported[0].get_pixmap(dpi=rules.RENDER_DPI)
                    if (
                        original_pixels.width != exported_pixels.width
                        or original_pixels.height != exported_pixels.height
                        or original_pixels.samples != exported_pixels.samples
                    ):
                        raise ValueError(f"Seitenexport verändert Rendering: Seite {page_number}")
                    pixel_hash = hashlib.sha256(original_pixels.samples).hexdigest()
                    result_path = target / "result.json"
                    if not result_path.is_file():
                        if target.exists() and any(target.iterdir()):
                            raise ValueError(f"Unvollständige Seitenausgabe: {target}")
                        with (folder / f"page-{page_number:03d}.log").open("w", encoding="utf-8") as log:
                            completed = subprocess.run(
                                [
                                    sys.executable, "-X", "utf8", str(analysis.BASE_DIR / "offline_guard.py"),
                                    "--pdf", str(exported_path), "--output", str(target), "--no-open",
                                ],
                                cwd=analysis.BASE_DIR, stdout=log, stderr=subprocess.STDOUT,
                                env={**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"},
                                check=False,
                            )
                        if completed.returncode != 0:
                            raise ValueError(f"Seitenanalyse fehlgeschlagen: Seite {page_number}; Log siehe {folder}")
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    if result["source"]["sha256"] != analysis.sha256_file(exported_path):
                        raise ValueError(f"Ergebnis/Seitenexport stimmen nicht überein: Seite {page_number}")
                    results.append(result)
                    record["pages"] = [page for page in record["pages"] if page["page"] != page_number]
                    record["pages"].append({
                        "page": page_number, "state": "success",
                        "pixel_equivalence_sha256": pixel_hash,
                        "export_sha256": result["source"]["sha256"],
                        "result": str(result_path.relative_to(root)),
                    })
                    save_state(state_path, state)
                    print(f"Originalrang {job['rank']}: Seite {page_number}/{doc.page_count} abgeschlossen", flush=True)
            if analysis.sha256_file(source) != source_hash:
                raise ValueError("Originalquelle während Supplement verändert.")
            combined = combine_pages(source, source_hash, results)
            (folder / "document-result.json").write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
            write_document_index(folder, job, combined, len(results))
            record.update(state="success", finished_at_utc=now())
        except Exception as error:
            record.update(state="failed", error=f"{type(error).__name__}: {error}", finished_at_utc=now())
        save_state(state_path, state)
    state["finished_at_utc"] = now()
    save_state(state_path, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
