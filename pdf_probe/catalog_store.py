"""Local symbol catalogue store (SQLite).

Curation layer only: entries are keyed by exact visual identity
(catalog_rules.exact_visual_key) of already-frozen candidate crops.
No detection, no fuzzy matching, no OCR at import time. User names are
authoritative and are never overwritten by imports or OCR updates.
"""

import base64
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image

import analyze_label as al
import catalog_rules as rules

BASE_DIR = Path(__file__).resolve().parent
ARTIFACTS_DIR = BASE_DIR / "artifacts"
DEFAULT_DB = BASE_DIR / "catalog.sqlite3"
MAX_NAME_LEN = 200
ENTRY_STATES = ("proposed", "approved", "ignored")


class StaleVersionError(Exception):
    """Optimistic-lock conflict: entry was edited concurrently."""


class ValidationError(Exception):
    """Invalid input data for the store."""


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def document_id(source):
    return hashlib.sha256(
        (os.path.normcase(str(source["path"])) + "\n" + source["sha256"]).encode("utf-8")
    ).hexdigest()


def occurrence_id(doc_id, template_id, bbox):
    return hashlib.sha256(
        (doc_id + "\n" + template_id + "\n" + json.dumps(bbox, separators=(",", ":"))).encode(
            "utf-8"
        )
    ).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    report_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    key TEXT PRIMARY KEY,
    image BLOB NOT NULL,
    suggested_name TEXT,
    suggestion_source TEXT,
    suggestion_kind TEXT,
    observed_text TEXT,
    material_number INTEGER,
    material_abbreviation TEXT,
    confirmed_name TEXT,
    state TEXT NOT NULL DEFAULT 'proposed',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    raw_ocr TEXT
);
CREATE TABLE IF NOT EXISTS occurrences (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    entry_key TEXT NOT NULL REFERENCES entries(key),
    template_id TEXT NOT NULL,
    bbox TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_key TEXT NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    created_at TEXT NOT NULL
);
"""


class CatalogStore:
    def __init__(self, db_path=DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def _session(self):
        conn = self._conn()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _audit(self, conn, key, field, old, new):
        conn.execute(
            "INSERT INTO audit(entry_key, field, old_value, new_value, created_at) "
            "VALUES (?,?,?,?,?)",
            (key, field, None if old is None else str(old), None if new is None else str(new), _utcnow()),
        )

    def _validate_result(self, result, report_path):
        if not isinstance(result, dict) or result.get("schema") != al.RESULT_SCHEMA:
            raise ValidationError("Ergebnis hat nicht das erwartete Schema.")
        src = result.get("source") or {}
        if not src.get("path") or not src.get("sha256") or not src.get("name"):
            raise ValidationError("Ergebnis ohne gültige Quellangaben.")
        resolved = Path(report_path).resolve()
        if not resolved.is_relative_to(ARTIFACTS_DIR.resolve()):
            raise ValidationError(f"Berichtspfad außerhalb von artifacts: {resolved}")
        return resolved

    def import_result(self, result, report_path):
        resolved = self._validate_result(result, report_path)
        src = result["source"]
        doc_id = document_id(src)
        now = _utcnow()
        inserted_entries = 0
        inserted_occ = 0
        with self._session() as conn:
            conn.execute(
                "INSERT INTO documents(id, source_path, source_name, source_sha256, "
                "report_path, created_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET report_path = excluded.report_path",
                (doc_id, str(src["path"]), src["name"], src["sha256"], str(resolved), now),
            )
            if rules.is_legacy_template_result(result):
                conn.commit()
                return {
                    "document_id": doc_id,
                    "new_entries": 0,
                    "new_occurrences": 0,
                    "symbols_import_blocked": True,
                    "reason": "legacy_template_result",
                }
            for sym in result.get("symbols", []):
                bbox = sym.get("bbox_pixels")
                template_id = sym.get("id")
                if not bbox or not template_id:
                    continue
                asset = (result.get("assets") or {}).get(f"cand_{template_id}.png")
                if not asset:
                    continue
                image_bytes = base64.b64decode(asset)
                try:
                    key = rules.exact_visual_key(Image.open(BytesIO(image_bytes)))
                except ValueError:
                    continue
                exists = conn.execute(
                    "SELECT key FROM entries WHERE key = ?", (key,)
                ).fetchone()
                if exists is None:
                    if sym.get("candidate_kind") == "graphic_region" and sym.get("proposal"):
                        proposal = sym["proposal"]
                        raw_ocr = json.dumps(sym.get("crop_ocr"), ensure_ascii=False)
                    else:
                        texts = rules.context_texts(result.get("ocr_lines", []), bbox)
                        proposal = rules.proposal_from_text(texts, template_id)
                        raw_ocr = None
                    conn.execute(
                        "INSERT INTO entries(key, image, suggested_name, suggestion_source, "
                        "suggestion_kind, observed_text, material_number, "
                        "material_abbreviation, confirmed_name, state, version, "
                        "created_at, updated_at, raw_ocr) "
                        "VALUES (?,?,?,?,?,?,?,?,NULL,'proposed',1,?,?,?)",
                        (
                            key,
                            image_bytes,
                            proposal.get("suggested_name"),
                            proposal.get("suggestion_source"),
                            proposal.get("suggestion_kind"),
                            proposal.get("observed_text"),
                            proposal.get("material_number"),
                            proposal.get("material_abbreviation"),
                            now,
                            now,
                            raw_ocr,
                        ),
                    )
                    inserted_entries += 1
                occ_id = occurrence_id(doc_id, template_id, bbox)
                before = conn.total_changes
                conn.execute(
                    "INSERT OR IGNORE INTO occurrences(id, document_id, entry_key, "
                    "template_id, bbox) VALUES (?,?,?,?,?)",
                    (occ_id, doc_id, key, template_id, json.dumps(bbox)),
                )
                if conn.total_changes > before:
                    inserted_occ += 1
            conn.commit()
        return {
            "document_id": doc_id,
            "new_entries": inserted_entries,
            "new_occurrences": inserted_occ,
        }

    def _iter_result_paths(self):
        roots = [
            ARTIFACTS_DIR / "result" / "result.json",
            *sorted(ARTIFACTS_DIR.glob("result-*/result.json")),
            *sorted(ARTIFACTS_DIR.glob("result/latest100-20261105/pdf_*/result.json")),
            *sorted(ARTIFACTS_DIR.glob("samples-20261105/*/result.json")),
            *sorted(ARTIFACTS_DIR.glob("graphics-pilot-v3/*/result.json")),
        ]
        return [p for p in roots if p.is_file()]

    def import_existing(self):
        summary = {"files": 0, "imported": 0, "skipped": [], "errors": []}
        for path in self._iter_result_paths():
            summary["files"] += 1
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
                info = self.import_result(result, path)
                summary["imported"] += 1
                if info.get("symbols_import_blocked"):
                    summary.setdefault("legacy_blocked", []).append(str(path))
                summary.setdefault("details", []).append(
                    {"file": path.name, **info}
                )
            except (ValidationError, json.JSONDecodeError, KeyError) as exc:
                summary["skipped"].append({"file": str(path), "reason": str(exc)})
            except Exception as exc:
                summary["errors"].append({"file": str(path), "error": str(exc)})
        return summary

    def list_entries(self, state=None, search=None):
        sql = (
            "SELECT e.*, (SELECT COUNT(*) FROM occurrences o WHERE o.entry_key = e.key) "
            "AS occurrence_count FROM entries e"
        )
        params = []
        clauses = []
        if state in ENTRY_STATES:
            clauses.append("e.state = ?")
            params.append(state)
        if search:
            clauses.append(
                "(e.suggested_name LIKE ? OR e.confirmed_name LIKE ? OR e.observed_text LIKE ?)"
            )
            like = f"%{search}%"
            params += [like, like, like]
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY e.created_at, e.key"
        with self._session() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows if r["key"]]

    def get_entry(self, key):
        with self._session() as conn:
            row = conn.execute("SELECT * FROM entries WHERE key = ?", (key,)).fetchone()
            if row is None:
                return None
            entry = dict(row)
            entry.pop("image", None)
            occ = conn.execute(
                "SELECT o.id, o.template_id, o.bbox, o.document_id, d.source_name, "
                "d.source_path FROM occurrences o JOIN documents d ON d.id = o.document_id "
                "WHERE o.entry_key = ? ORDER BY d.source_name",
                (key,),
            ).fetchall()
            entry["occurrences"] = [dict(o) for o in occ]
            audits = conn.execute(
                "SELECT field, old_value, new_value, created_at FROM audit "
                "WHERE entry_key = ? ORDER BY id",
                (key,),
            ).fetchall()
            entry["audit"] = [dict(a) for a in audits]
            return entry

    def image_bytes(self, key):
        with self._session() as conn:
            row = conn.execute("SELECT image FROM entries WHERE key = ?", (key,)).fetchone()
            return row["image"] if row else None

    def first_template_id(self, key):
        with self._session() as conn:
            row = conn.execute(
                "SELECT template_id FROM occurrences WHERE entry_key = ? LIMIT 1", (key,)
            ).fetchone()
            return row["template_id"] if row else None

    def get_document(self, doc_id):
        with self._session() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_documents(self):
        with self._session() as conn:
            rows = conn.execute("SELECT * FROM documents ORDER BY source_name").fetchall()
            return [dict(r) for r in rows]

    def update_entry(self, key, name, state, expected_version):
        if state not in ENTRY_STATES:
            raise ValidationError(f"Ungültiger Status: {state}")
        if name is not None and not isinstance(name, str):
            raise ValidationError("Name muss ein Text oder null sein.")
        if not isinstance(expected_version, int) or isinstance(expected_version, bool):
            raise ValidationError("version muss eine Ganzzahl sein.")
        if name is not None and len(name) > MAX_NAME_LEN:
            raise ValidationError(f"Name zu lang (max. {MAX_NAME_LEN} Zeichen).")
        if state == "approved" and not (name and name.strip()):
            raise ValidationError("Bestätigung erfordert einen nicht-leeren Namen.")
        with self._session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM entries WHERE key = ?", (key,)).fetchone()
            if row is None:
                raise ValidationError("Eintrag nicht gefunden.")
            if row["version"] != expected_version:
                raise StaleVersionError(
                    f"Eintrag wurde zwischenzeitlich geändert (Version {row['version']})."
                )
            updates = {}
            if state == "approved" or name is not None:
                updates["confirmed_name"] = name
            updates["state"] = state
            updates["version"] = row["version"] + 1
            updates["updated_at"] = _utcnow()
            for field, new_value in updates.items():
                old_value = row[field] if field in row.keys() else None
                if field in ("version", "updated_at"):
                    continue
                if old_value != new_value:
                    self._audit(conn, key, field, old_value, new_value)
            conn.execute(
                "UPDATE entries SET confirmed_name = ?, state = ?, version = ?, "
                "updated_at = ? WHERE key = ?",
                (
                    updates["confirmed_name"] if "confirmed_name" in updates else row["confirmed_name"],
                    state,
                    updates["version"],
                    updates["updated_at"],
                    key,
                ),
            )
            conn.commit()
        return self.get_entry(key)

    def update_proposal(self, key, proposal, raw_ocr=None):
        with self._session() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM entries WHERE key = ?", (key,)).fetchone()
            if row is None:
                raise ValidationError("Eintrag nicht gefunden.")
            self._audit(conn, key, "proposal", row["suggested_name"], proposal.get("suggested_name"))
            conn.execute(
                "UPDATE entries SET suggested_name = ?, suggestion_source = ?, "
                "suggestion_kind = ?, observed_text = ?, material_number = ?, "
                "material_abbreviation = ?, raw_ocr = ?, version = version + 1, "
                "updated_at = ? WHERE key = ?",
                (
                    proposal.get("suggested_name"),
                    proposal.get("suggestion_source"),
                    proposal.get("suggestion_kind"),
                    proposal.get("observed_text"),
                    proposal.get("material_number"),
                    proposal.get("material_abbreviation"),
                    raw_ocr,
                    _utcnow(),
                    key,
                ),
            )
            conn.commit()
        return self.get_entry(key)

    def matrix_rows(self):
        rows = []
        with self._session() as conn:
            docs = conn.execute("SELECT * FROM documents ORDER BY source_name").fetchall()
            for doc in docs:
                approved = conn.execute(
                    "SELECT DISTINCT e.confirmed_name FROM occurrences o "
                    "JOIN entries e ON e.key = o.entry_key "
                    "WHERE o.document_id = ? AND e.state = 'approved' "
                    "AND e.confirmed_name IS NOT NULL AND e.confirmed_name != ''",
                    (doc["id"],),
                ).fetchall()
                proposals = conn.execute(
                    "SELECT DISTINCT e.suggested_name FROM occurrences o "
                    "JOIN entries e ON e.key = o.entry_key "
                    "WHERE o.document_id = ? AND e.state = 'proposed' "
                    "AND e.suggested_name IS NOT NULL",
                    (doc["id"],),
                ).fetchall()
                rows.append(
                    {
                        "document_id": doc["id"],
                        "filename": doc["source_name"],
                        "source_path": doc["source_path"],
                        "source_sha256": doc["source_sha256"],
                        "approved_names": sorted({r[0] for r in approved}),
                        "proposals": sorted({r[0] for r in proposals}),
                        "report_link": f"/reports/{doc['id']}/",
                    }
                )
        return rows
