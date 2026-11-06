"""Loopback-only HTTP UI/API for the local symbol catalogue.

Binds 127.0.0.1 only. No CORS, no directory listing, static allowlist,
Host/Origin/CSRF-token checks on state-changing routes. Presentation and
curation only — no detection logic here.
"""

import argparse
import base64
import html
import io
import json
import re
import secrets
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import analyze_label as al
import catalog_store

BASE_DIR = Path(__file__).resolve().parent
APP_ID = "local-symbol-catalog"
APP_VERSION = "1.0"
DEFAULT_PORT = 8766
MAX_BODY = 64 * 1024
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
STATIC_FILES = {
    "/": ("catalog.html", "text/html; charset=utf-8"),
    "/catalog.html": ("catalog.html", "text/html; charset=utf-8"),
    "/catalog.js": ("catalog.js", "text/javascript; charset=utf-8"),
    "/catalog.css": ("catalog.css", "text/css; charset=utf-8"),
    "/report-toggle.js": ("report-toggle.js", "text/javascript; charset=utf-8"),
}
CSP_STRICT = "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'"
CSP_REPORT = "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'"
INLINE_SCRIPT_RE = re.compile(r"<script>.*?</script>", re.DOTALL)


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


class JobManager:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        self.jobs = {}
        self.lock = threading.Lock()

    def start_ocr(self, key):
        with self.lock:
            for job in self.jobs.values():
                if job["state"] == "running":
                    return None
            job_id = uuid.uuid4().hex[:12]
            self.jobs[job_id] = {
                "id": job_id,
                "state": "running",
                "entry_key": key,
                "result": None,
                "error": None,
            }
            threading.Thread(target=self._run, args=(job_id, key), daemon=True).start()
            return job_id

    def _run(self, job_id, key):
        worker = BASE_DIR / "catalog_ocr_worker.py"
        try:
            proc = subprocess.run(
                [sys.executable, "-X", "utf8", str(worker),
                 "--db", self.db_path, "--key", key],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=900,
            )
            if proc.returncode == 0:
                self.jobs[job_id]["state"] = "done"
                self.jobs[job_id]["result"] = proc.stdout.strip()[-2000:]
            else:
                self.jobs[job_id]["state"] = "error"
                self.jobs[job_id]["error"] = (proc.stderr or proc.stdout).strip()[-1000:]
        except Exception as exc:
            self.jobs[job_id]["state"] = "error"
            self.jobs[job_id]["error"] = str(exc)

    def get(self, job_id):
        return self.jobs.get(job_id)


class CatalogServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, store, csrf_token, port):
        super().__init__(address, handler)
        self.store = store
        self.csrf_token = csrf_token
        self.port = self.server_address[1]
        self.jobs = JobManager(store.db_path)


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalSymbolCatalog/1.0"
    protocol_version = "HTTP/1.1"

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        self.close_connection = True
        self._json(status, {"error": message})

    def _bytes(self, data, content_type, extra_headers=None):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        host = self.headers.get("Host", "")
        name, _, port = host.partition(":")
        return name in ("127.0.0.1", "localhost") and port == str(self.server.port)

    def _guard_write(self):
        if not self._host_ok():
            self._error(403, "Ungültiger Host.")
            return None
        origin = self.headers.get("Origin")
        if origin is not None:
            allowed = {
                f"http://127.0.0.1:{self.server.port}",
                f"http://localhost:{self.server.port}",
            }
            if origin not in allowed:
                self._error(403, "Origin abgelehnt.")
                return None
        if self.headers.get("X-Catalog-Token") != self.server.csrf_token:
            self._error(403, "CSRF-Token fehlt oder ungültig.")
            return None
        if "application/json" not in (self.headers.get("Content-Type") or ""):
            self._error(415, "Content-Type muss application/json sein.")
            return None
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._error(400, "Ungültige Content-Length.")
            return None
        if length <= 0 or length > MAX_BODY:
            self._error(413, "Body zu groß oder fehlt.")
            return None
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(400, "Ungültiges JSON.")
            return None
        if not isinstance(payload, dict):
            self._error(400, "Body muss ein JSON-Objekt sein.")
            return None
        return payload

    def log_message(self, fmt, *args):
        sys.stderr.write("catalog %s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self):
        if not self._host_ok():
            self._error(403, "Ungültiger Host.")
            return
        path, _, query = self.path.partition("?")
        try:
            if path == "/api/health":
                entries = len(self.server.store.list_entries())
                docs = len(self.server.store.list_documents())
                self._json(200, {"app": APP_ID, "version": APP_VERSION,
                                 "entries": entries, "documents": docs})
            elif path == "/api/bootstrap":
                self._json(200, {"csrf": self.server.csrf_token, "states": list(catalog_store.ENTRY_STATES)})
            elif path == "/api/entries":
                params = urllib.parse.parse_qs(query)
                state = (params.get("state") or [None])[0]
                search = (params.get("search") or [None])[0]
                if state and state not in catalog_store.ENTRY_STATES:
                    self._error(400, "Ungültiger state-Filter.")
                    return
                entries = self.server.store.list_entries(state, search)
                for e in entries:
                    e.pop("image", None)
                self._json(200, {"entries": entries})
            elif re.fullmatch(r"/api/entries/[0-9a-f]{64}", path):
                key = path.rsplit("/", 1)[-1]
                entry = self.server.store.get_entry(key)
                if entry is None:
                    self._error(404, "Eintrag nicht gefunden.")
                else:
                    self._json(200, {"entry": entry})
            elif re.fullmatch(r"/api/entries/[0-9a-f]{64}/image", path):
                key = path.split("/")[-2]
                blob = self.server.store.image_bytes(key)
                if blob is None:
                    self._error(404, "Kein Bild.")
                else:
                    self._bytes(bytes(blob), "image/png", {"Cache-Control": "no-store"})
            elif path == "/api/documents":
                self._json(200, {"documents": self.server.store.list_documents()})
            elif path == "/api/matrix.csv":
                self._serve_matrix_csv()
            elif re.fullmatch(r"/api/jobs/[0-9a-f]{12}", path):
                job = self.server.jobs.get(path.rsplit("/", 1)[-1])
                if job is None:
                    self._error(404, "Job nicht gefunden.")
                else:
                    self._json(200, {"job": job})
            elif re.fullmatch(r"/reports/[0-9a-f]{64}/", path):
                self._serve_report(path.split("/")[2])
            elif re.fullmatch(r"/reports/[0-9a-f]{64}/(matrix\.csv|result\.json)", path):
                self._serve_report_file(path.split("/")[2], path.rsplit("/", 1)[-1])
            elif path == "/pilot/":
                self._serve_pilot()
            elif path in STATIC_FILES:
                self._serve_static(path)
            else:
                self._error(404, "Nicht gefunden.")
        except al.FriendlyError as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, f"Interner Fehler: {exc}")

    def _serve_static(self, path):
        name, ctype = STATIC_FILES[path]
        target = (BASE_DIR / name).resolve()
        if target.parent != BASE_DIR.resolve() or not target.is_file():
            self._error(404, "Nicht gefunden.")
            return
        self._bytes(
            target.read_bytes(), ctype,
            {"Content-Security-Policy": CSP_STRICT, "Cache-Control": "no-store"},
        )

    def _serve_pilot(self):
        page = pilot_page()
        self._bytes(
            page.encode("utf-8"), "text/html; charset=utf-8",
            {"Content-Security-Policy": CSP_STRICT, "Cache-Control": "no-store"},
        )

    def _serve_matrix_csv(self):
        import csv

        header = ["Datei", "Quellpfad", "SHA-256", "Bestätigte Namen",
                  "Unbestätigte Vorschläge", "Bericht"]
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
        writer.writerow([al.csv_safe(c) for c in header])
        for row in self.server.store.matrix_rows():
            writer.writerow(
                [
                    al.csv_safe(row["filename"]),
                    al.csv_safe(row["source_path"]),
                    al.csv_safe(row["source_sha256"]),
                    al.csv_safe(" | ".join(row["approved_names"])),
                    al.csv_safe(" | ".join(row["proposals"])),
                    al.csv_safe(row["report_link"]),
                ]
            )
        self._bytes(
            buf.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8",
            {"Content-Disposition": 'attachment; filename="catalog-matrix.csv"'},
        )

    def _load_report_result(self, doc_id):
        doc = self.server.store.get_document(doc_id)
        if doc is None:
            return None, None
        report_path = Path(doc["report_path"]).resolve()
        artifacts = catalog_store.ARTIFACTS_DIR.resolve()
        if not report_path.is_relative_to(artifacts) or not report_path.is_file():
            return doc, None
        try:
            return doc, json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return doc, None

    def _catalog_panel(self, doc_id):
        return catalog_panel(self.server.store, doc_id)

    def _serve_report(self, doc_id):
        doc, result = self._load_report_result(doc_id)
        if doc is None or result is None:
            self._error(404, "Bericht nicht gefunden.")
            return
        page = al.build_html(result)
        page = INLINE_SCRIPT_RE.sub("", page, count=1)
        page = page.replace('href="matrix.csv"', f'href="/reports/{doc_id}/matrix.csv"')
        page = page.replace('href="result.json"', f'href="/reports/{doc_id}/result.json"')
        page = page.replace("<main>", "<main>" + self._catalog_panel(doc_id), 1)
        page = page.replace("</body>", '<script src="/report-toggle.js"></script></body>')
        self._bytes(
            page.encode("utf-8"), "text/html; charset=utf-8",
            {"Content-Security-Policy": CSP_REPORT, "Cache-Control": "no-store"},
        )

    def _serve_report_file(self, doc_id, name):
        doc, _ = self._load_report_result(doc_id)
        if doc is None:
            self._error(404, "Nicht gefunden.")
            return
        target = (Path(doc["report_path"]).resolve().parent / name)
        artifacts = catalog_store.ARTIFACTS_DIR.resolve()
        if not target.resolve().is_relative_to(artifacts) or not target.is_file():
            self._error(404, "Nicht gefunden.")
            return
        ctype = "text/csv; charset=utf-8" if name.endswith(".csv") else "application/json"
        self._bytes(target.read_bytes(), ctype, {"Cache-Control": "no-store"})

    def do_POST(self):
        path, _, _query = self.path.partition("?")
        if not re.fullmatch(r"/api/entries/[0-9a-f]{64}(/ocr)?|/api/import", path):
            if not self._host_ok():
                self._error(403, "Ungültiger Host.")
                return
            self._error(404, "Nicht gefunden.")
            return
        payload = self._guard_write()
        if payload is None:
            return
        try:
            if path == "/api/import":
                summary = self.server.store.import_existing()
                self._json(200, {"summary": summary})
            elif path.endswith("/ocr"):
                key = path.split("/")[-2]
                if self.server.store.get_entry(key) is None:
                    self._error(404, "Eintrag nicht gefunden.")
                    return
                job_id = self.server.jobs.start_ocr(key)
                if job_id is None:
                    self._error(409, "Es läuft bereits ein OCR-Job.")
                else:
                    self._json(202, {"job": job_id})
            else:
                key = path.rsplit("/", 1)[-1]
                version = payload.get("version")
                if not isinstance(version, int) or isinstance(version, bool):
                    self._error(400, "version fehlt oder ungültig.")
                    return
                entry = self.server.store.update_entry(
                    key, payload.get("name"), payload.get("state"), version
                )
                self._json(200, {"entry": entry})
        except catalog_store.StaleVersionError as exc:
            self._error(409, str(exc))
        except catalog_store.ValidationError as exc:
            self._error(400, str(exc))
        except al.FriendlyError as exc:
            self._error(400, str(exc))
        except Exception as exc:
            self._error(500, f"Interner Fehler: {exc}")


def pilot_page():
    pilot_dir = catalog_store.ARTIFACTS_DIR / "graphics-pilot-v3"
    review_path = pilot_dir / "manual-review.json"
    if not review_path.is_file():
        raise al.FriendlyError("Pilot-Übersicht nicht gefunden.")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    parts = [
        "<!DOCTYPE html><html lang=\"de\"><head><meta charset=\"utf-8\">",
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>Dreier-Test – Grafikkandidaten</title>",
        '<link rel="stylesheet" href="/catalog.css"></head><body><main>',
        "<h1>Dreier-Test: vorlagenfreie Grafikkandidaten</h1>",
        '<p class="muted">Drei gespeicherte Seitenbefunde; neue Grafiksuche und Ausschnitt-OCR, '
        'keine erneute PDF-/Seiten-OCR-Analyse. '
        "Alle Werte stammen aus dem eingefrorenen manual-review.json – "
        "keine Konformitätsprüfung.</p>",
    ]
    for field in ("review_scope", "source", "algorithm"):
        if review.get(field):
            parts.append(f'<p class="muted">{al.esc(field)}: {al.esc(str(review[field]))}</p>')
    parts.append(
        f'<p class="muted">Legacy-Vorlagen verwendet: {al.esc(str(review.get("uses_legacy_templates")))} · '
        f'Leitfaden importiert: {al.esc(str(review.get("guide_imported")))} · '
        f'alle Einträge unbestätigt: {al.esc(str(review.get("all_entries_unapproved")))}</p>'
    )
    for doc in review.get("documents", []):
        parts.append('<div class="card">')
        parts.append(f'<h2>{al.esc(str(doc.get("file")))}</h2>')
        parts.append(f'<p>Kandidaten (eingefroren): {al.esc(str(doc.get("candidate_count")))}</p>')
        result_path = pilot_dir / str(doc.get("id")) / "result.json"
        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            src = result.get("source") or {}
            if src.get("path") and src.get("sha256"):
                doc_id = catalog_store.document_id(src)
                parts.append(
                    f'<p><a href="/reports/{al.esc(doc_id)}/">Bericht</a> · '
                    f'<a href="/reports/{al.esc(doc_id)}/result.json">result.json</a></p>'
                )
        props = doc.get("reviewed_material_proposals") or []
        if props:
            parts.append("<p><strong>Materialvorschläge (manuell geprüft):</strong></p><ul>")
            for prop in props:
                parts.append(
                    f'<li>{al.esc(str(prop.get("id")))}: {al.esc(str(prop.get("ocr_proposal")))} '
                    f'(visuell passend: {al.esc(str(prop.get("visually_matches")))})</li>'
                )
            parts.append("</ul>")
        for label, key in (
            ("Sortiertext-Vorschläge", "sorting_text_proposals"),
            ("Nicht klassifizierte sichtbare Symbole", "unclassified_visible_symbols"),
            ("Grenzen / Anmerkungen", "limits"),
        ):
            items = doc.get(key) or []
            if items:
                parts.append(f"<p><strong>{label}:</strong></p><ul>")
                for item in items:
                    parts.append(f"<li>{al.esc(str(item))}</li>")
                parts.append("</ul>")
        parts.append("</div>")
    parts.append('<p><a href="/">Zum Katalog</a></p></main></body></html>')
    return "".join(parts)


def catalog_panel(store, doc_id):
    with store._session() as conn:
        rows = conn.execute(
            "SELECT o.template_id, e.key, e.suggested_name, e.confirmed_name, e.state "
            "FROM occurrences o JOIN entries e ON e.key = o.entry_key "
            "WHERE o.document_id = ? AND e.state != 'ignored' ORDER BY o.template_id",
            (doc_id,),
        ).fetchall()
    if not rows:
        return ""
    parts = [
        '<div class="card"><h2>Katalog-Annotationen</h2>'
        '<p class="muted">Namen aus dem lokalen Katalog (bestätigt bzw. unbestätigter Vorschlag). '
        'Die eingefrorenen Rohdaten bleiben unverändert.</p><table class="fields"><tbody>'
    ]
    for r in rows:
        if r["state"] == "approved" and r["confirmed_name"]:
            shown = f"{r['confirmed_name']} (bestätigt)"
        else:
            shown = f"{r['suggested_name'] or '–'} (unbestätigter Vorschlag, Status: {r['state']})"
        parts.append(
            f"<tr><th>{esc(r['template_id'])}</th><td>{esc(shown)}</td></tr>"
        )
    parts.append("</tbody></table></div>")
    return "".join(parts)


def _open_browser(port):
    try:
        import os

        os.startfile(f"http://127.0.0.1:{port}/")
    except OSError:
        pass


def check_existing(port):
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health", timeout=3
        ) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("app") == APP_ID
    except Exception:
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lokaler Symbol-Katalog (127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", default=str(catalog_store.DEFAULT_DB))
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args(argv)

    existing = check_existing(args.port)
    if existing:
        print(f"Katalog läuft bereits auf 127.0.0.1:{args.port} – wiederverwendet.")
        if args.open_browser:
            _open_browser(args.port)
        return 0
    if existing is False:
        print(f"Fehler: Port {args.port} ist durch einen anderen Dienst belegt.", file=sys.stderr)
        return 1

    store = catalog_store.CatalogStore(args.db)
    csrf = secrets.token_hex(16)
    try:
        server = CatalogServer(("127.0.0.1", args.port), Handler, store, csrf, args.port)
    except OSError as exc:
        print(f"Fehler: Port {args.port} kann nicht gebunden werden: {exc}", file=sys.stderr)
        return 1
    print(f"Symbol-Katalog auf http://127.0.0.1:{args.port}/ (lokal, DB: {store.db_path})")
    if args.open_browser:
        _open_browser(server.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
