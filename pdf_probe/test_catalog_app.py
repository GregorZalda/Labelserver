"""App tests for the local symbol catalogue (store + HTTP + browser).

Ordinary plumbing tests only — no recognition/eval/scoring assertions.
"""

import base64
import builtins
import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze_label as al
import catalog_server
import catalog_store

BASE_DIR = Path(__file__).resolve().parent
SHOT_DIR = BASE_DIR / "artifacts" / "catalog-verification"
BLOCKED = {"pymupdf", "fitz", "cv2", "numpy", "onnxruntime",
           "rapidocr_onnxruntime", "analysis_rules"}


def png_b64(image):
    buf = BytesIO()
    image.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def make_image(text):
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (90, 50), "white")
    ImageDraw.Draw(img).text((5, 5), text, fill="black")
    return img


def sym(symbol_id, bbox, score=0.9):
    return {
        "id": symbol_id,
        "label": f"tpl {symbol_id}",
        "status": "Kandidat – prüfen",
        "score": score,
        "bbox_pixels": bbox,
        "method": "x",
        "review_required": True,
    }


def make_result(name, sha, symbols, images):
    assets = {"page.png": png_b64(make_image("page"))}
    for s in symbols:
        assets[f"cand_{s['id']}.png"] = png_b64(images[s["id"]])
    return {
        "schema": al.RESULT_SCHEMA,
        "tool": {"name": "t", "version": "0"},
        "generated_at": "2026-01-01T00:00:00+00:00",
        "source": {
            "path": f"X:\\labels\\{name}", "name": name, "size_bytes": 1,
            "sha256": sha, "pages": 1, "page_rect_pts": [0, 0, 1, 1],
            "pdf_metadata": {},
        },
        "environment": {},
        "method": {"render_dpi": 300, "match_threshold": 0.82,
                   "match_scales": [0.65], "native_text": "x", "ocr": "x",
                   "matching": "x"},
        "render": {"dpi": 300, "width_px": 200, "height_px": 200},
        "reference": {"path": "r", "sha256": "0" * 64, "size": [307, 235], "symbols": []},
        "contact_source": None,
        "contacts": {"original": "", "fields": {}, "bbox_points": None,
                     "status": al.UNKNOWN, "method": None},
        "native_lines": [],
        "ocr_lines": [],
        "symbols": symbols,
        "disclaimers": [],
        "assets": assets,
    }


def fake_report_path(tag="r"):
    p = catalog_store.ARTIFACTS_DIR / "_cat_test" / tag / "result.json"
    return p


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "cat.sqlite3"
        self.store = catalog_store.CatalogStore(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _import(self, name, sha, symbols, images):
        return self.store.import_result(
            make_result(name, sha, symbols, images), fake_report_path(name)
        )

    def test_import_twice_idempotent(self):
        img = make_image("20 PAP")
        r = self._import("a.pdf", "0" * 64, [sym("pap_21", [1, 1, 50, 50])], {"pap_21": img})
        self.assertEqual(r["new_entries"], 1)
        r2 = self._import("a.pdf", "0" * 64, [sym("pap_21", [1, 1, 50, 50])], {"pap_21": img})
        self.assertEqual(r2["new_entries"], 0)
        self.assertEqual(r2["new_occurrences"], 0)
        self.assertEqual(len(self.store.list_entries()), 1)

    def test_approve_persists_across_store_recreate(self):
        img = make_image("20 PAP")
        self._import("a.pdf", "0" * 64, [sym("pap_21", [1, 1, 50, 50])], {"pap_21": img})
        key = self.store.list_entries()[0]["key"]
        self.store.update_entry(key, "20 PAP / Müll", "approved", 1)
        store2 = catalog_store.CatalogStore(self.db)
        entry = store2.get_entry(key)
        self.assertEqual(entry["confirmed_name"], "20 PAP / Müll")
        self.assertEqual(entry["state"], "approved")
        self.assertEqual(entry["version"], 2)
        self.assertTrue(any(a["field"] == "confirmed_name" for a in entry["audit"]))

    def test_same_crop_inherits_different_visual_stays_proposed(self):
        img = make_image("same")
        self._import("a.pdf", "0" * 64, [sym("pap_21", [1, 1, 50, 50])], {"pap_21": img})
        key = self.store.list_entries()[0]["key"]
        self.store.update_entry(key, "Bestätigter", "approved", 1)
        r = self._import("b.pdf", "1" * 64, [sym("pap_21", [1, 1, 50, 50])], {"pap_21": img})
        self.assertEqual(r["new_entries"], 0)
        self.assertEqual(r["new_occurrences"], 1)
        entry = self.store.get_entry(key)
        self.assertEqual(entry["confirmed_name"], "Bestätigter")
        self.assertEqual(len(entry["occurrences"]), 2)
        i20, i21 = make_image("20 PAP"), make_image("21 PAP")
        self._import("c.pdf", "2" * 64,
                     [sym("pap_21", [1, 1, 50, 50]), sym("pap_21", [60, 60, 110, 110])],
                     {"pap_21": i20})
        entries = {e["key"]: e for e in self.store.list_entries()}
        self.assertEqual(len(entries), 2)
        self._import("d.pdf", "3" * 64, [sym("x21", [1, 1, 50, 50])], {"x21": i21})
        entries = self.store.list_entries()
        self.assertEqual(len(entries), 3)
        others = [e for e in entries if e["key"] != key]
        self.assertTrue(all(e["state"] == "proposed" for e in others))

    def test_legacy_result_registers_doc_blocks_symbols(self):
        legacy = make_result("old.pdf", "3" * 64,
                             [sym("pap_21", [1, 1, 50, 50])],
                             {"pap_21": make_image("old")})
        legacy["tool"] = {"name": "pdf_probe analyze_label", "version": "0.1.0"}
        legacy["reference"] = {
            "path": "references/symbol-reference.png",
            "symbols": [{"id": "pap_21"}],
        }
        info = self.store.import_result(legacy, fake_report_path("old.pdf"))
        self.assertTrue(info["symbols_import_blocked"])
        self.assertEqual(info["reason"], "legacy_template_result")
        self.assertEqual(info["new_entries"], 0)
        self.assertEqual(info["new_occurrences"], 0)
        self.assertEqual(len(self.store.list_documents()), 1)
        self.assertEqual(len(self.store.list_entries()), 0)

    def test_import_existing_surfaces_legacy_block(self):
        fixture_dir = catalog_store.ARTIFACTS_DIR / "_cat_test" / "legacy01"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        fixture = fixture_dir / "result.json"
        legacy = make_result("old.pdf", "3" * 64,
                             [sym("pap_21", [1, 1, 50, 50])],
                             {"pap_21": make_image("old")})
        legacy["tool"] = {"name": "pdf_probe analyze_label", "version": "0.1.0"}
        try:
            fixture.write_text(json.dumps(legacy), encoding="utf-8")
            self.store._iter_result_paths = lambda: [fixture]
            summary = self.store.import_existing()
            self.assertEqual(summary["imported"], 1)
            self.assertEqual(len(summary["legacy_blocked"]), 1)
            self.assertEqual(len(self.store.list_entries()), 0)
            self.assertEqual(len(self.store.list_documents()), 1)
        finally:
            fixture.unlink(missing_ok=True)
            try:
                fixture_dir.rmdir()
            except OSError:
                pass

    def test_graphic_region_uses_frozen_proposal(self):
        region = sym("region_001", [1, 1, 50, 50])
        region["candidate_kind"] = "graphic_region"
        region["status"] = "Grafikkandidat – prüfen"
        region["proposal"] = {
            "suggested_name": "20 PAP",
            "suggestion_source": "Lokale Ausschnitt-OCR",
            "suggestion_kind": "ocr_material_pair",
            "observed_text": "20 PAP",
            "material_number": 20,
            "material_abbreviation": "PAP",
            "warning": "Unbestätigter OCR-Vorschlag.",
        }
        region["crop_ocr"] = {
            "method": "x", "scale": 4.0,
            "lines": [{"text": "20 PAP", "score": 0.9}],
        }
        result = make_result("g.pdf", "4" * 64, [region],
                             {"region_001": make_image("20 PAP")})
        result["ocr_lines"] = [{
            "text": "21 PAP", "score": 0.9,
            "polygon_pixels": [[1, 1], [50, 1], [50, 50], [1, 50]],
            "bbox": [1, 1, 50, 50], "method": "Lokale OCR",
        }]
        info = self.store.import_result(result, fake_report_path("g.pdf"))
        self.assertEqual(info["new_entries"], 1)
        self.assertFalse(info.get("symbols_import_blocked"))
        entry = self.store.list_entries()[0]
        self.assertEqual(entry["suggested_name"], "20 PAP")
        self.assertEqual(entry["material_number"], 20)
        self.assertEqual(entry["suggestion_kind"], "ocr_material_pair")
        full = self.store.get_entry(entry["key"])
        self.assertIn("20 PAP", full["raw_ocr"])
        self.store.update_entry(entry["key"], "Bestätigter", "approved", 1)
        self.store.import_result(result, fake_report_path("g.pdf"))
        self.assertEqual(
            self.store.get_entry(entry["key"])["confirmed_name"], "Bestätigter")

    def test_reimport_and_proposal_update_keep_user_name(self):
        img = make_image("20 PAP")
        self._import("a.pdf", "0" * 64, [sym("pap_21", [1, 1, 50, 50])], {"pap_21": img})
        key = self.store.list_entries()[0]["key"]
        self.store.update_entry(key, "20 PAP / Müll", "approved", 1)
        self._import("a.pdf", "0" * 64, [sym("pap_21", [1, 1, 50, 50])], {"pap_21": img})
        self.store.update_proposal(
            key,
            {"suggested_name": "99 XYZ", "suggestion_source": "Ausschnitt-OCR",
             "suggestion_kind": "ocr_material_pair", "observed_text": "99",
             "material_number": 99, "material_abbreviation": "XYZ"},
            raw_ocr="{}",
        )
        entry = self.store.get_entry(key)
        self.assertEqual(entry["confirmed_name"], "20 PAP / Müll")
        self.assertEqual(entry["state"], "approved")
        self.assertEqual(entry["suggested_name"], "99 XYZ")

    def test_stale_version_and_empty_approval(self):
        img = make_image("x")
        self._import("a.pdf", "0" * 64, [sym("s", [1, 1, 50, 50])], {"s": img})
        key = self.store.list_entries()[0]["key"]
        with self.assertRaises(catalog_store.StaleVersionError):
            self.store.update_entry(key, "Name", "approved", 99)
        with self.assertRaises(catalog_store.ValidationError):
            self.store.update_entry(key, "   ", "approved", 1)
        with self.assertRaises(catalog_store.ValidationError):
            self.store.update_entry(key, "x" * 201, "approved", 1)
        for bad_name in (123, ["x"], True):
            with self.assertRaises(catalog_store.ValidationError):
                self.store.update_entry(key, bad_name, "approved", 1)
        for bad_version in (True, 1.5, "1", [1]):
            with self.assertRaises(catalog_store.ValidationError):
                self.store.update_entry(key, "ok", "approved", bad_version)

    def test_concurrent_update_exactly_one_wins(self):
        img = make_image("x")
        self._import("a.pdf", "0" * 64, [sym("s", [1, 1, 50, 50])], {"s": img})
        key = self.store.list_entries()[0]["key"]
        barrier = threading.Barrier(2)
        outcomes = []

        def worker():
            try:
                barrier.wait(timeout=10)
                self.store.update_entry(key, "Winner", "approved", 1)
                outcomes.append("ok")
            except catalog_store.StaleVersionError:
                outcomes.append("stale")
            except Exception as exc:
                outcomes.append(f"unexpected:{exc}")

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertCountEqual(outcomes, ["ok", "stale"], outcomes)
        entry = self.store.get_entry(key)
        self.assertEqual(entry["confirmed_name"], "Winner")
        self.assertEqual(entry["version"], 2)

    def test_ignored_not_in_proposals_or_panel(self):
        img = make_image("x")
        self._import("a.pdf", "0" * 64, [sym("s", [1, 1, 50, 50])], {"s": img})
        key = self.store.list_entries()[0]["key"]
        doc_id = catalog_store.document_id(make_result("a.pdf", "0" * 64, [], {})["source"])
        self.store.update_entry(key, None, "ignored", 1)
        rows = self.store.matrix_rows()
        row = [r for r in rows if r["document_id"] == doc_id][0]
        self.assertEqual(row["approved_names"], [])
        self.assertEqual(row["proposals"], [])
        self.assertNotIn("unbestätigter Vorschlag",
                         catalog_server.catalog_panel(self.store, doc_id))
        self.assertEqual(len(self.store.list_entries(state="ignored")), 1)
        self.assertEqual(len(self.store.list_entries(state="proposed")), 0)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "cat.sqlite3"
        self.store = catalog_store.CatalogStore(self.db)
        self.store.import_result(
            make_result("a.pdf", "0" * 64,
                        [sym("pap_21", [1, 1, 50, 50])],
                        {"pap_21": make_image("20 PAP")}),
            fake_report_path("a.pdf"),
        )
        self.key = self.store.list_entries()[0]["key"]
        self.token = "test-token"
        self.server = catalog_server.CatalogServer(
            ("127.0.0.1", 0), catalog_server.Handler, self.store, self.token, 0
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _req(self, method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        h = {"Host": host or f"127.0.0.1:{self.server.port}"}
        h.update(headers or {})
        payload = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=payload, headers=h)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def test_health_and_host_check(self):
        status, data = self._req("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["app"], "local-symbol-catalog")
        status, _ = self._req("GET", "/api/health", host="evil.example:1")
        self.assertEqual(status, 403)

    def test_csrf_origin_body_checks(self):
        self.store.import_existing = lambda: {
            "files": 1, "imported": 1, "skipped": [], "errors": []}
        status, _ = self._req("POST", "/api/import",
                              body={}, headers={"Content-Type": "application/json"})
        self.assertEqual(status, 403)
        status, _ = self._req(
            "POST", "/api/import", body={},
            headers={"Content-Type": "application/json",
                     "X-Catalog-Token": self.token,
                     "Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self._req(
            "POST", "/api/import", body={},
            headers={"Content-Type": "application/json",
                     "X-Catalog-Token": self.token,
                     "Origin": f"http://127.0.0.1:{self.server.port}"})
        self.assertEqual(status, 200)
        conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        conn.request("POST", "/api/import",
                     body=b"x" * (catalog_server.MAX_BODY + 1),
                     headers={"Content-Type": "application/json",
                              "X-Catalog-Token": self.token})
        resp = conn.getresponse()
        resp.read()
        conn.close()
        self.assertEqual(resp.status, 413)

    def test_pilot_page_lists_frozen_results(self):
        status, data = self._req("GET", "/pilot/")
        self.assertEqual(status, 200)
        text = data.decode("utf-8") if isinstance(data, bytes) else str(data)
        self.assertIn("Dreier-Test", text)
        self.assertIn("824101", text)
        self.assertIn("999112494", text)
        self.assertIn("800319", text)
        for count in ("14", "7", "13"):
            self.assertIn(count, text)

    def test_guard_rejects_non_object_bodies(self):
        for raw in (b"null", b"[]", b'"x"', b"42"):
            conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
            conn.request("POST", "/api/import", body=raw,
                         headers={"Content-Type": "application/json",
                                  "X-Catalog-Token": self.token})
            resp = conn.getresponse()
            data = json.loads(resp.read())
            conn.close()
            self.assertEqual(resp.status, 400, raw)
            self.assertIn("error", data)

    def test_strict_version_and_name_types(self):
        for bad_version in (True, 1.5, "1", [1], None):
            status, _ = self._req(
                "POST", f"/api/entries/{self.key}",
                body={"name": "x", "state": "approved", "version": bad_version},
                headers={"Content-Type": "application/json", "X-Catalog-Token": self.token})
            self.assertEqual(status, 400, bad_version)
        for bad_name in (123, ["x"], True):
            status, _ = self._req(
                "POST", f"/api/entries/{self.key}",
                body={"name": bad_name, "state": "approved", "version": 1},
                headers={"Content-Type": "application/json", "X-Catalog-Token": self.token})
            self.assertEqual(status, 400, bad_name)

    def test_traversal_and_unknown_routes(self):
        for path in ("/reports/../catalog.sqlite3", "/api/entries/zz/../x",
                     "/static/../analyze_label.py", "/nope"):
            status, _ = self._req("GET", path)
            self.assertEqual(status, 404, path)
        status, _ = self._req("POST", "/api/nope", body={},
                              headers={"Content-Type": "application/json",
                                       "X-Catalog-Token": self.token})
        self.assertEqual(status, 404)

    def test_entry_update_via_http_and_409(self):
        status, _ = self._req(
            "POST", f"/api/entries/{self.key}",
            body={"name": "20 PAP / Müll", "state": "approved", "version": 1},
            headers={"Content-Type": "application/json", "X-Catalog-Token": self.token})
        self.assertEqual(status, 200)
        status, _ = self._req(
            "POST", f"/api/entries/{self.key}",
            body={"name": "other", "state": "approved", "version": 1},
            headers={"Content-Type": "application/json", "X-Catalog-Token": self.token})
        self.assertEqual(status, 409)
        status, _ = self._req(
            "POST", f"/api/entries/{self.key}",
            body={"name": "", "state": "approved", "version": 2},
            headers={"Content-Type": "application/json", "X-Catalog-Token": self.token})
        self.assertEqual(status, 400)
        status, data = self._req("GET", f"/api/entries/{self.key}/image")
        self.assertEqual(status, 200)

    def test_malicious_name_escaped_in_report_and_csv(self):
        status, _ = self._req(
            "POST", f"/api/entries/{self.key}",
            body={"name": "<script>alert(1)</script>", "state": "approved", "version": 1},
            headers={"Content-Type": "application/json", "X-Catalog-Token": self.token})
        self.assertEqual(status, 200)
        panel = catalog_server.catalog_panel(self.store, catalog_store.document_id(
            make_result("a.pdf", "0" * 64, [], {})["source"]))
        self.assertIn("&lt;script&gt;", panel)
        self.assertNotIn("<script>alert(1)</script>", panel)
        status, data = self._req(
            "POST", f"/api/entries/{self.key}",
            body={"name": "=EVIL(1)", "state": "approved", "version": 2},
            headers={"Content-Type": "application/json", "X-Catalog-Token": self.token})
        self.assertEqual(status, 200)
        status, csv_bytes = self._req("GET", "/api/matrix.csv")
        self.assertEqual(status, 200)
        self.assertIn("'=EVIL(1)", csv_bytes.decode("utf-8-sig"))
        status, _ = self._req(
            "POST", f"/api/entries/{self.key}",
            body={"name": "20 PAP / Müll", "state": "approved", "version": 3},
            headers={"Content-Type": "application/json", "X-Catalog-Token": self.token})
        self.assertEqual(status, 200)

    def test_import_and_render_never_import_ocr_or_pdf(self):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.split(".")[0] in BLOCKED:
                raise ImportError(f"blocked: {name}")
            return real_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            store = catalog_store.CatalogStore(Path(tmp) / "c.sqlite3")
            img = make_image("x")
            result = make_result("z.pdf", "9" * 64, [sym("s", [1, 1, 50, 50])], {"s": img})
            rp = fake_report_path("z.pdf")
            with mock.patch("builtins.__import__", side_effect=fake_import):
                store.import_result(result, rp)
                al.build_html(result)
            self.assertGreaterEqual(len(store.list_entries()), 1)


class StartupTests(unittest.TestCase):
    def test_main_never_calls_import_existing(self):
        store_instance = mock.Mock()
        server_instance = mock.Mock()
        server_instance.port = 9911
        with mock.patch.object(
            catalog_server.catalog_store, "CatalogStore", return_value=store_instance
        ) as store_cls, mock.patch.object(
            catalog_server, "check_existing", return_value=None
        ), mock.patch.object(
            catalog_server, "CatalogServer", return_value=server_instance
        ):
            code = catalog_server.main(["--port", "9911", "--db", "x.sqlite3"])
        self.assertEqual(code, 0)
        store_cls.assert_called_once()
        store_instance.import_existing.assert_not_called()
        server_instance.serve_forever.assert_called_once()
        server_instance.server_close.assert_called_once()


class BrowserUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest("playwright nicht installiert")
        browser_path = None
        for cand in (
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        ):
            if Path(cand).is_file():
                browser_path = cand
                break
        if not browser_path:
            raise unittest.SkipTest("kein Browser gefunden")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Path(cls.tmp.name) / "ui.sqlite3"
        cls.store = catalog_store.CatalogStore(cls.db)
        region_a = sym("region_001", [1, 1, 50, 50])
        region_a["candidate_kind"] = "graphic_region"
        region_a["proposal"] = {
            "suggested_name": "20 PAP",
            "suggestion_source": "Lokale Ausschnitt-OCR",
            "suggestion_kind": "ocr_material_pair",
            "observed_text": "20 PAP",
            "material_number": 20,
            "material_abbreviation": "PAP",
            "warning": "Unbestätigter OCR-Vorschlag.",
        }
        region_a["crop_ocr"] = {"method": "x", "scale": 1, "lines": []}
        region_b = sym("region_002", [60, 60, 110, 110])
        region_b["candidate_kind"] = "graphic_region"
        region_b["proposal"] = {
            "suggested_name": "Unbekanntes Symbol",
            "suggestion_source": "Keine Textgrundlage",
            "suggestion_kind": "unverified_hint",
            "observed_text": "",
            "material_number": None,
            "material_abbreviation": None,
            "warning": "Nur Vorschlag.",
        }
        region_b["crop_ocr"] = {"method": "x", "scale": 1, "lines": []}
        cls.store.import_result(
            make_result("ui.pdf", "7" * 64,
                        [region_a, region_b],
                        {"region_001": make_image("20 PAP"),
                         "region_002": make_image("TIDY")}),
            fake_report_path("ui.pdf"),
        )
        cls.server = catalog_server.CatalogServer(
            ("127.0.0.1", 0), catalog_server.Handler, cls.store, "ui-token", 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(executable_path=browser_path, headless=True)
        SHOT_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def _card_input(self, page, n):
        return page.locator(".entry input[type=text]").nth(n)

    def _card_buttons(self, page, n):
        return page.locator(".entry").nth(n).locator("button")

    def test_draft_survives_other_card_save_and_loads(self):
        url = f"http://127.0.0.1:{self.server.port}/"
        page = self.browser.new_page(viewport={"width": 1400, "height": 1200})
        try:
            page.goto(url, wait_until="load")
            page.wait_for_selector(".entry input[type=text]", timeout=15000)
            self.assertEqual(page.locator(".entry").count(), 2)
            self.assertIn("Vorschlag:", page.inner_text(".entry"))
            self.assertEqual(
                page.locator("a[href='/pilot/']").text_content(), "Dreier-Test")
            page.screenshot(path=str(SHOT_DIR / "ui-1-proposal.png"))

            card_b = 1
            self._card_input(page, card_b).fill("Draft B ungespeichert")

            self._card_input(page, 0).fill("Alpha bestätigt")
            self._card_buttons(page, 0).nth(0).click()
            page.wait_for_selector("text=Gespeichert:", timeout=15000)
            self.assertEqual(
                self._card_input(page, card_b).input_value(),
                "Draft B ungespeichert",
            )
            page.screenshot(path=str(SHOT_DIR / "ui-2-draft-survives-save.png"))

            page.click("#apply")
            page.wait_for_timeout(800)
            self.assertEqual(
                self._card_input(page, card_b).input_value(),
                "Draft B ungespeichert",
            )

            self._card_buttons(page, card_b).nth(0).click()
            page.wait_for_selector("text=Gespeichert:", timeout=15000)
            page.reload(wait_until="load")
            page.wait_for_selector(".entry input[type=text]", timeout=15000)
            self.assertEqual(
                self._card_input(page, card_b).input_value(),
                "Draft B ungespeichert",
            )

            self._card_buttons(page, 0).nth(2).click()
            page.wait_for_selector("text=Gespeichert:", timeout=15000)
            self._card_buttons(page, card_b).nth(2).click()
            page.wait_for_selector("text=Gespeichert:", timeout=15000)

            page.select_option("#state", "approved")
            page.click("#apply")
            page.wait_for_timeout(800)
            self.assertEqual(page.locator(".entry").count(), 0)
            self.assertTrue(page.locator("#empty").is_visible())
            page.screenshot(path=str(SHOT_DIR / "ui-3-filter-empty.png"))
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main()
