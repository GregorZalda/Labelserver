import argparse
import heapq
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def iso_utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)
    output = Path(args.output)
    if not root.is_dir():
        raise ValueError(f"Verzeichnis nicht erreichbar: {root}")
    if output.exists():
        raise ValueError(f"Auswahl existiert bereits: {output}")
    if not output.parent.is_dir():
        raise ValueError(f"Ausgabeordner fehlt: {output.parent}")
    errors = []
    newest = []
    pdf_count = 0
    directory_count = 0
    started_at = datetime.now(timezone.utc).isoformat()

    def walk_error(error):
        errors.append({"path": str(error.filename), "error": str(error)})

    for parent, directories, filenames in os.walk(root, onerror=walk_error, followlinks=False):
        directory_count += 1
        directories.sort(key=str.casefold)
        for name in filenames:
            if not name.lower().endswith(".pdf"):
                continue
            path = Path(parent) / name
            try:
                stat = path.stat()
                if not path.is_file():
                    continue
                created = stat.st_birthtime
            except (OSError, AttributeError) as error:
                errors.append({"path": str(path), "error": str(error)})
                continue
            pdf_count += 1
            path_text = str(path)
            item = (created, path_text.casefold(), path_text, stat.st_mtime, stat.st_size)
            if len(newest) < 100:
                heapq.heappush(newest, item)
            elif item > newest[0]:
                heapq.heapreplace(newest, item)
        if directory_count % 500 == 0:
            print(f"Verzeichnisse: {directory_count}; PDFs: {pdf_count}; Fehler: {len(errors)}", flush=True)
    selected = [
        {
            "rank": index,
            "id": f"pdf_{index:03d}",
            "path": path,
            "filename": Path(path).name,
            "created_utc": iso_utc(created),
            "created_timestamp": created,
            "modified_utc": iso_utc(modified),
            "size_bytes": size,
        }
        for index, (created, _, path, modified, size)
        in enumerate(sorted(newest, reverse=True), 1)
    ]
    manifest = {
        "schema": "pdf-probe-latest-selection/1",
        "root": str(root),
        "selection": "100 neueste erreichbare PDF-Dateien nach Dateisystem-Erstellungszeit (st_birthtime); absteigend, bei Gleichstand Pfad lexikografisch absteigend; keine Auswahl nach Dateiname oder Änderungsdatum",
        "limitations": "Erstellungszeiten können durch Kopieren/Migration verändert worden sein. Dateiliste und Metadaten sind kein atomarer Server-Snapshot. Verzeichnislinks werden nicht verfolgt.",
        "started_at_utc": started_at,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "directories_scanned": directory_count,
        "pdfs_with_readable_metadata": pdf_count,
        "scan_errors": errors,
        "complete_accessible_scan": not errors,
        "selected": selected,
    }
    with output.open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    print(json.dumps({
        "manifest": str(output), "selected": len(selected),
        "pdfs_scanned": pdf_count, "scan_errors": len(errors),
        "newest": selected[0]["created_utc"] if selected else None,
        "oldest_selected": selected[-1]["created_utc"] if selected else None,
    }, ensure_ascii=True), flush=True)
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
