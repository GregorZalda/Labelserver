"""Ordinary unittest checks for analyze_label + analysis_rules execution.

These tests do not assert matching quality/scores; they cover parsing,
safety and plumbing only.
"""

import builtins
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analyze_label as al
import analysis_rules as rules

FOOTER = (
    "DURABLE · Hunke & Jochheim GmbH & Co. KG · Westfalenstraße 77–79 · "
    "58636 Iserlohn · Germany · info@durable.de · durable.de"
)
PNG_1PX = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg=="
)
BLOCKED_MODULES = {"pymupdf", "fitz", "cv2", "numpy", "onnxruntime",
                   "rapidocr_onnxruntime", "analysis_rules", "catalog_store"}


def make_fake_result():
    return {
        "schema": al.RESULT_SCHEMA,
        "tool": {"name": al.TOOL_NAME, "version": al.TOOL_VERSION},
        "generated_at": "2026-01-01T00:00:00+00:00",
        "source": {
            "path": "x/<script>alert(1)</script>.pdf",
            "name": "<script>alert(1)</script>.pdf",
            "size_bytes": 1,
            "sha256": "0" * 64,
            "pages": 1,
            "page_rect_pts": [0, 0, 1, 1],
            "pdf_metadata": {},
        },
        "environment": {"python": "test"},
        "method": {
            "render_dpi": 300,
            "match_threshold": 0.82,
            "match_scales": [0.65],
            "native_text": "PDF-Text",
            "ocr": "OCR",
            "matching": "Vorlagenabgleich",
        },
        "render": {"dpi": 300, "width_px": 10, "height_px": 10},
        "reference": {
            "path": "references/symbol-reference.png",
            "sha256": "f" * 64,
            "size": [307, 235],
            "symbols": [{"id": "s1", "label": "<script>alert(1)</script>", "crop_box": [0, 0, 1, 1]}],
        },
        "contact_source": "PDF-Text",
        "contacts": {
            "original": "<script>alert(1)</script>",
            "fields": {"E-Mail": "<script>alert(1)</script>"},
            "bbox_points": [0, 0, 1, 1],
            "status": "Aus PDF-Text gelesen",
            "method": "PDF-Text",
        },
        "native_lines": [{"text": "<script>alert(1)</script>", "bbox": [0, 0, 1, 1], "method": "PDF-Text"}],
        "ocr_lines": [
            {
                "text": "<script>alert(1)</script>",
                "score": 0.5,
                "polygon_pixels": [[0, 0], [1, 0], [1, 1], [0, 1]],
                "bbox": [0, 0, 1, 1],
                "method": "Lokale OCR",
            }
        ],
        "symbols": [
            {
                "id": "s1",
                "label": "<script>alert(1)</script>",
                "status": al.UNKNOWN,
                "score": 0.1,
                "bbox_pixels": [1, 1, 5, 5],
                "method": "x",
                "review_required": True,
            }
        ],
        "disclaimers": ["<script>alert(1)</script>"],
        "assets": {"page.png": PNG_1PX, "ref_s1.png": PNG_1PX, "cand_s1.png": PNG_1PX},
    }


class ContactTests(unittest.TestCase):
    def test_native_footer_fields(self):
        lines = [{"text": FOOTER, "bbox": [0, 0, 100, 10], "method": "PDF-Text"}]
        result = rules.extract_contacts(lines)
        self.assertEqual(result["original"], FOOTER)
        self.assertEqual(result["status"], "Aus PDF-Text gelesen")
        self.assertEqual(result["method"], "PDF-Text")
        self.assertEqual(result["bbox_points"], [0, 0, 100, 10])
        self.assertEqual(
            result["fields"],
            {
                "E-Mail": "info@durable.de",
                "Firma / Marke": "DURABLE · Hunke & Jochheim GmbH & Co. KG",
                "Straße / Hausnummer": "Westfalenstraße 77–79",
                "PLZ": "58636",
                "Ort": "Iserlohn",
                "Land": "Germany",
                "Website": "durable.de",
            },
        )

    def test_empty_footer_missing(self):
        for lines in ([], [{"text": "kein kontakt", "bbox": [0, 0, 1, 1], "method": "PDF-Text"}]):
            result = rules.extract_contacts(lines)
            self.assertEqual(result["original"], "")
            self.assertEqual(result["fields"], {})
            self.assertIsNone(result["bbox_points"])
            self.assertEqual(result["status"], al.UNKNOWN)


class TemplateRemovalTests(unittest.TestCase):
    def test_old_template_config_absent(self):
        for attr in ("SYMBOLS", "match_symbols", "MATCH_THRESHOLD",
                     "MATCH_SCALES", "REFERENCE_SIZE"):
            self.assertFalse(hasattr(rules, attr), attr)

    def test_disabled_status_constants(self):
        self.assertEqual(rules.SYMBOL_DETECTION_STATUS, "graphic_candidates")
        self.assertIn("Vorlagenfrei", rules.SYMBOL_DETECTION_MESSAGE)


class RunAnalysisTests(unittest.TestCase):
    def test_run_analysis_without_templates(self):
        import pymupdf
        import catalog_store
        import graphic_candidates
        from PIL import Image as PILImage

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            pdf = tmp / "one.pdf"
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_text((50, 50), "NATIVETEXT")
            doc.save(pdf)
            doc.close()
            hash_before = hashlib.sha256(pdf.read_bytes()).hexdigest()
            ocr_line = {
                "text": "OCRTEST", "score": 0.9,
                "polygon_pixels": [[0, 0], [10, 0], [10, 10], [0, 10]],
                "bbox": [0, 0, 10, 10], "method": "Lokale OCR",
            }
            region = {
                "id": "region_001", "label": "Unbenannter Grafikkandidat",
                "status": "Grafikkandidat – prüfen",
                "candidate_kind": "graphic_region",
                "bbox_pixels": [190, 190, 320, 280],
                "score": None, "method": "graphic-regions/1",
                "review_required": True,
            }
            enriched = {
                **region,
                "proposal": {"suggested_name": "Unbekanntes Symbol",
                             "suggestion_source": "Keine Textgrundlage",
                             "suggestion_kind": "unverified_hint",
                             "observed_text": "", "material_number": None,
                             "material_abbreviation": None, "warning": "w"},
                "crop_ocr": {"method": "x", "scale": 1, "lines": []},
            }
            fake_engine = object()
            create_ocr = mock.Mock(return_value=fake_engine)
            find = mock.Mock(return_value=[region])
            add_props = mock.Mock(return_value=[enriched])
            opened_paths = []
            real_open = PILImage.open
            real_store_cls = catalog_store.CatalogStore

            def spy_open(fp, *args, **kwargs):
                if isinstance(fp, str):
                    opened_paths.append(fp)
                return real_open(fp, *args, **kwargs)

            out_dir = tmp / "out"
            with mock.patch.object(rules, "create_ocr", create_ocr), \
                 mock.patch.object(rules, "run_ocr", return_value=[ocr_line]), \
                 mock.patch.object(graphic_candidates,
                                   "find_graphic_candidates", find), \
                 mock.patch.object(graphic_candidates,
                                   "add_ocr_proposals", add_props), \
                 mock.patch.object(PILImage, "open", side_effect=spy_open), \
                 mock.patch.object(catalog_store, "ARTIFACTS_DIR", tmp), \
                 mock.patch.object(
                     catalog_store, "CatalogStore",
                     lambda *a, **k: real_store_cls(tmp / "cat.sqlite3")):
                result = al.run_analysis(pdf, out_dir)

            self.assertEqual(create_ocr.call_count, 1)
            self.assertEqual(add_props.call_count, 1)
            self.assertIs(add_props.call_args[0][2], fake_engine)
            self.assertEqual(add_props.call_args[0][3], [ocr_line])
            self.assertEqual(result["symbols"], [enriched])
            self.assertIsNone(result["reference"])
            self.assertEqual(result["tool"]["version"], "0.3.0")
            self.assertEqual(result["method"]["symbol_detection"],
                             "graphic_candidates")
            self.assertEqual(result["method"]["graphics_algorithm"],
                             "graphic-regions/1")
            self.assertEqual(result["method"]["matching"],
                             rules.SYMBOL_DETECTION_MESSAGE)
            self.assertNotIn("match_threshold", result["method"])
            self.assertNotIn("match_scales", result["method"])
            self.assertIn("OCRTEST",
                          [line["text"] for line in result["ocr_lines"]])
            self.assertIn("NATIVETEXT",
                          [line["text"] for line in result["native_lines"]])
            self.assertEqual(hashlib.sha256(pdf.read_bytes()).hexdigest(),
                             hash_before)
            self.assertIn("cand_region_001.png", result["assets"])
            for name in result["assets"]:
                self.assertFalse(
                    name.startswith(("ref_", "reference")), name)
            self.assertTrue((out_dir / "assets" / "cand_region_001.png").is_file())
            for f in (out_dir / "assets").iterdir():
                self.assertFalse(
                    f.name.startswith(("ref_", "reference")), f.name)
            self.assertFalse(
                any("symbol-reference" in p for p in opened_paths))
            self.assertTrue((out_dir / "result.json").is_file())
            store = catalog_store.CatalogStore(tmp / "cat.sqlite3")
            self.assertEqual(len(store.list_documents()), 1)
            self.assertEqual(len(store.list_entries()), 1)
            entry = store.list_entries()[0]
            self.assertEqual(entry["state"], "proposed")
            self.assertIsNone(entry["confirmed_name"])


class RendererSymbolTests(unittest.TestCase):
    def test_disabled_renderer_shows_status_not_templates(self):
        result = make_fake_result()
        result["tool"] = {"name": al.TOOL_NAME, "version": "0.2.0"}
        result["method"] = {
            "render_dpi": 300,
            "symbol_detection": "disabled",
            "native_text": "x", "ocr": "x",
            "matching": "Keine Symbolerkennung eingerichtet.",
        }
        result["symbols"] = []
        result["reference"] = None
        result["assets"] = {"page.png": PNG_1PX}
        html_out = al.build_html(result)
        self.assertIn("Symbolerkennung noch nicht eingerichtet", html_out)
        self.assertIn("keine Aussage über vorhandene oder fehlende Symbole",
                      html_out)
        self.assertNotIn("Referenzvorlage", html_out)
        self.assertNotIn("Vorlagen-Kandidatenmatrix", html_out)
        self.assertNotIn("Historischer Bericht", html_out)
        header, _row = al.build_matrix(result)
        self.assertIn("Kontaktstatus", header)
        self.assertNotIn("<script>alert(1)</script>", html_out)

    def test_graphic_renderer_shows_proposal_no_templates(self):
        result = make_fake_result()
        result["tool"] = {"name": al.TOOL_NAME, "version": "0.3.0"}
        result["method"] = {
            "render_dpi": 300,
            "symbol_detection": "graphic_candidates",
            "native_text": "x", "ocr": "x",
            "matching": "Vorlagenfrei", "graphics_algorithm": "graphic-regions/1",
        }
        result["reference"] = None
        result["symbols"] = [{
            "id": "region_001", "label": "Unbenannter Grafikkandidat",
            "status": "Grafikkandidat – prüfen",
            "candidate_kind": "graphic_region",
            "bbox_pixels": [1, 1, 5, 5], "score": None,
            "method": "graphic-regions/1", "review_required": True,
            "proposal": {
                "suggested_name": "20 PAP",
                "suggestion_source": "Lokale Ausschnitt-OCR",
                "suggestion_kind": "ocr_material_pair",
                "observed_text": "20 PAP",
                "material_number": 20, "material_abbreviation": "PAP",
                "warning": "Unbestätigter OCR-Vorschlag.",
            },
            "crop_ocr": {"method": "x", "scale": 1,
                         "lines": [{"text": "20 PAP", "score": 0.9}]},
        }]
        result["assets"]["cand_region_001.png"] = PNG_1PX
        html_out = al.build_html(result)
        self.assertIn("Grafikkandidaten – keine bestätigten Symbole", html_out)
        self.assertIn("20 PAP", html_out)
        self.assertIn("Lokale Ausschnitt-OCR", html_out)
        self.assertIn("Rohe Ausschnitt-OCR", html_out)
        self.assertNotIn("Referenzvorlage", html_out)
        self.assertNotIn("Ähnlichkeit:", html_out)
        self.assertNotIn("Historischer Bericht", html_out)
        header, _row = al.build_matrix(result)
        self.assertIn("20 PAP", header)

    def test_legacy_result_shows_historical_banner(self):
        html_out = al.build_html(make_fake_result())
        self.assertIn("Historischer Bericht", html_out)
        self.assertIn("nicht in den Katalog übernommen", html_out)
        self.assertIn("Vorlagen-Kandidatenmatrix", html_out)


class SafetyTests(unittest.TestCase):
    def test_html_escapes_injected_script(self):
        html_out = al.build_html(make_fake_result())
        self.assertNotIn("<script>alert(1)</script>", html_out)
        self.assertIn("&lt;script&gt;", html_out)

    def test_csv_formula_neutralization(self):
        self.assertEqual(al.csv_safe("=HYPERLINK(1)"), "'=HYPERLINK(1)")
        self.assertEqual(al.csv_safe("+43"), "'+43")
        self.assertEqual(al.csv_safe("-1"), "'-1")
        self.assertEqual(al.csv_safe("@tag"), "'@tag")
        self.assertEqual(al.csv_safe("\tX"), "'\tX")
        self.assertEqual(al.csv_safe("normal"), "normal")
        self.assertEqual(al.csv_safe(""), "")
        self.assertEqual(al.csv_safe(None), "")

    def test_csv_written_semicolon_utf8sig(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "matrix.csv"
            al.write_matrix_csv(make_fake_result(), out)
            raw = out.read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
            text = raw.decode("utf-8-sig")
            self.assertIn(";", text.splitlines()[0])
            self.assertEqual(len(text.splitlines()), 2)

    def test_matrix_source_columns(self):
        result = make_fake_result()
        header, row = al.build_matrix(result)
        self.assertEqual(header[:3], ["PDF", "Quellpfad", "SHA-256"])
        self.assertEqual(row[0], result["source"]["name"])
        self.assertEqual(row[1], result["source"]["path"])
        self.assertEqual(row[2], result["source"]["sha256"])
        kidx = header.index("Kontaktstatus")
        self.assertEqual(header[kidx + 1], "Original-Kontaktzeile")
        self.assertEqual(row[kidx], result["contacts"]["status"])
        self.assertEqual(row[kidx + 1], result["contacts"]["original"])
        self.assertEqual(header[kidx + 2], result["symbols"][0]["label"])
        self.assertEqual(len(header), len(row))

    def test_malicious_asset_base64_rejected(self):
        result = make_fake_result()
        result["assets"]["page.png"] = 'AAAA" onerror="alert(1) x="'
        with self.assertRaises(al.FriendlyError):
            al.build_html(result)
        with self.assertRaises(al.FriendlyError):
            al.build_html({**result, "assets": {"page.png": 12345}})

    def test_render_json_without_pdf_or_ocr(self):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.split(".")[0] in BLOCKED_MODULES:
                raise ImportError(f"blocked in render-json test: {name}")
            return real_import(name, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "result.json"
            json_path.write_text(json.dumps(make_fake_result()), encoding="utf-8")
            with mock.patch("builtins.__import__", side_effect=fake_import):
                code = al.main(["--render-json", str(json_path), "--no-open"])
            self.assertEqual(code, 0)
            self.assertTrue((Path(tmp) / "index.html").is_file())
            self.assertTrue((Path(tmp) / "matrix.csv").is_file())
            self.assertTrue((Path(tmp) / "page.png").is_file())
            self.assertEqual(json_path.read_bytes(), json.dumps(make_fake_result()).encode("utf-8"))

    def test_render_json_new_output_copies_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            src_dir = Path(tmp) / "src"
            src_dir.mkdir()
            json_path = src_dir / "result.json"
            payload = json.dumps(make_fake_result()).encode("utf-8")
            json_path.write_bytes(payload)
            new_out = Path(tmp) / "new-out"
            code = al.main(["--render-json", str(json_path), "--output", str(new_out), "--no-open"])
            self.assertEqual(code, 0)
            self.assertTrue((new_out / "index.html").is_file())
            self.assertTrue((new_out / "result.json").is_file())
            self.assertEqual((new_out / "result.json").read_bytes(), payload)
            self.assertEqual(json_path.read_bytes(), payload)


class OutputRoutingTests(unittest.TestCase):
    def test_default_empty_returns_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            default = Path(tmp) / "result"
            self.assertEqual(al.resolve_analysis_output(None, default), default)
            default.mkdir()
            self.assertEqual(al.resolve_analysis_output(None, default), default)

    def test_default_populated_returns_unique_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            default = Path(tmp) / "result"
            default.mkdir()
            (default / "result.json").write_text("{}", encoding="utf-8")
            out = al.resolve_analysis_output(None, default)
            self.assertNotEqual(out, default)
            self.assertEqual(out.parent, default.parent)
            self.assertRegex(out.name, r"^result-\d{8}-\d{6}-[0-9a-f]{8}$")
            self.assertFalse(out.exists())

    def test_explicit_output_returned_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            default = Path(tmp) / "result"
            default.mkdir()
            (default / "result.json").write_text("{}", encoding="utf-8")
            explicit = Path(tmp) / "chosen"
            self.assertEqual(al.resolve_analysis_output(str(explicit), default), explicit)


class InputValidationTests(unittest.TestCase):
    def _sha(self, path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def test_missing_file_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = al.main(["--pdf", str(Path(tmp) / "nope.pdf"), "--output", str(Path(tmp) / "o"), "--no-open"])
            self.assertEqual(code, 1)

    def test_invalid_pdf_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.pdf"
            bad.write_text("keine pdf", encoding="utf-8")
            code = al.main(["--pdf", str(bad), "--output", str(Path(tmp) / "o"), "--no-open"])
            self.assertEqual(code, 1)
            self.assertFalse((Path(tmp) / "o" / "result.json").exists())

    def test_multi_page_pdf_fails_and_stays_readonly(self):
        import pymupdf

        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "two.pdf"
            doc = pymupdf.open()
            doc.new_page()
            doc.new_page()
            doc.save(pdf)
            doc.close()
            before = self._sha(pdf)
            code = al.main(["--pdf", str(pdf), "--output", str(Path(tmp) / "o"), "--no-open"])
            self.assertEqual(code, 1)
            self.assertEqual(self._sha(pdf), before)
            self.assertFalse((Path(tmp) / "o" / "result.json").exists())


if __name__ == "__main__":
    unittest.main()
