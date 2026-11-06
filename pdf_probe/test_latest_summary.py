import csv
import json
import tempfile
import unittest
from pathlib import Path

import analyze_label as analysis
import build_latest_summary as report


class LatestSummaryTests(unittest.TestCase):
    def make_run(self, root):
        selected = [
            {
                "rank": number, "id": f"pdf_{number:03d}",
                "path": f"\\\\example\\labels\\p{number:03d}.pdf",
                "filename": f"p{number:03d}.pdf",
                "created_utc": "2026-11-04T12:00:00+00:00",
                "modified_utc": "2026-11-04T12:00:00+00:00",
                "size_bytes": 100,
            }
            for number in range(1, 101)
        ]
        selected[0]["filename"] = "=HYPERLINK(1).pdf"
        selected[1]["filename"] = "<script>alert(1)</script>.pdf"
        selection = {
            "selected": selected, "selection": "fixture", "limitations": "fixture",
            "pdfs_with_readable_metadata": 150, "scan_errors": [],
        }
        (root / "selection.json").write_text(json.dumps(selection), encoding="utf-8")
        jobs = []
        for item in selected:
            number = item["rank"]
            status = "success" if number <= 50 else "reused" if number <= 60 else "failed" if number <= 70 else "pending"
            jobs.append({**item, "state": status, "source_sha256": "0" * 64})
            if status not in report.AVAILABLE:
                continue
            fields = {key: "value" for key in analysis.CONTACT_FIELDS}
            if number > 40:
                fields = {}
            elif number > 20:
                fields = {"E-Mail": "a@example.invalid", "Website": "example.invalid"}
            result = {
                "source": {"path": item["path"], "sha256": "0" * 64, "pages": 1},
                "contacts": {"fields": fields, "original": "fixture", "status": "fixture"},
                "native_lines": [], "ocr_lines": [],
                "symbols": [
                    {
                        "id": key, "label": key,
                        "status": "Kandidat – prüfen" if key == "pet_01" and number % 2 == 0 else analysis.UNKNOWN,
                        "bbox_pixels": [1, 1, 4, 4] if key == "pet_01" and number % 2 == 0 else None,
                    }
                    for key in report.SYMBOL_IDS
                ],
            }
            target = root / item["id"]
            target.mkdir()
            (target / "result.json").write_text(json.dumps(result), encoding="utf-8")
        state = {
            "selection_sha256": analysis.sha256_file(root / "selection.json"),
            "jobs": jobs, "started_at_utc": "fixture", "finished_at_utc": None,
            "recognition_scope": "fixture",
        }
        (root / "batch-state.json").write_text(json.dumps(state), encoding="utf-8")

    def test_counts_exclude_failed_and_pending_from_detection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_run(root)
            snapshot = report.build_snapshot(root)
            self.assertEqual(snapshot["analyzed_result_count"], 60)
            self.assertEqual(snapshot["terminal_count"], 70)
            self.assertEqual(snapshot["state_counts"], {"success": 50, "reused": 10, "failed": 10, "pending": 30})
            self.assertEqual(snapshot["candidate_document_counts"]["pet_01"], 30)
            self.assertEqual(sum(snapshot["candidate_document_counts"].values()), 30)
            self.assertEqual(snapshot["contact_field_count_histogram"]["0"], 20)
            self.assertEqual(snapshot["contact_field_count_histogram"]["2"], 20)
            self.assertEqual(snapshot["contact_field_count_histogram"]["7"], 20)
            self.assertEqual(sum(snapshot["contact_field_count_histogram"].values()), 60)
            report.write_csv(snapshot, root)
            report.write_html(snapshot, root)
            with (root / "matrix.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle, delimiter=";"))
            self.assertEqual(len(rows), 101)
            self.assertEqual({len(row) for row in rows}, {30})
            self.assertTrue(rows[1][1].startswith("'="))
            html = (root / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("<script>alert(1)</script>", html)
            self.assertIn("&lt;script&gt;", html)
            self.assertIn("Zwischenstand", html)

    def test_wrong_source_assignment_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_run(root)
            path = root / "pdf_001" / "result.json"
            result = json.loads(path.read_text(encoding="utf-8"))
            result["source"]["path"] = "\\\\example\\labels\\wrong.pdf"
            path.write_text(json.dumps(result), encoding="utf-8")
            with self.assertRaises(ValueError):
                report.build_snapshot(root)

    def test_candidate_without_location_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_run(root)
            path = root / "pdf_002" / "result.json"
            result = json.loads(path.read_text(encoding="utf-8"))
            result["symbols"][0]["bbox_pixels"] = None
            path.write_text(json.dumps(result), encoding="utf-8")
            with self.assertRaises(ValueError):
                report.build_snapshot(root)

    def test_multipage_counts_documents_once_and_preserves_contact_conflicts(self):
        from run_multipage_supplement import combine_pages

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_run(root)
            baseline = json.loads((root / "batch-state.json").read_text(encoding="utf-8"))
            original = baseline["jobs"][60]
            pages = []
            for number in (1, 2):
                pages.append({
                    "contacts": {
                        "fields": {"E-Mail": f"page{number}@example.invalid"},
                        "original": f"page {number}",
                    },
                    "native_lines": [], "ocr_lines": [],
                    "symbols": [
                        {
                            "id": key, "label": key,
                            "status": "Kandidat – prüfen" if key == "pet_01" else analysis.UNKNOWN,
                            "bbox_pixels": [1, 1, 4, 4] if key == "pet_01" else None,
                        }
                        for key in report.SYMBOL_IDS
                    ],
                })
            combined = combine_pages(original["path"], "0" * 64, pages)
            self.assertEqual(combined["contacts"]["fields"]["E-Mail"], "page1@example.invalid | page2@example.invalid")
            self.assertEqual(len(combined["symbols"][0]["page_evidence"]), 2)
            target = root / "multipage" / "pdf_061"
            target.mkdir(parents=True)
            (target / "document-result.json").write_text(json.dumps(combined), encoding="utf-8")
            supplement = {
                "finished_at_utc": "fixture",
                "jobs": [{
                    "id": original["id"], "path": original["path"],
                    "state": "success", "source_sha256": "0" * 64,
                }],
            }
            (root / "multipage-state.json").write_text(json.dumps(supplement), encoding="utf-8")
            snapshot = report.build_snapshot(root)
            self.assertEqual(snapshot["analyzed_result_count"], 61)
            self.assertEqual(snapshot["terminal_count"], 70)
            self.assertEqual(snapshot["candidate_document_counts"]["pet_01"], 31)
            self.assertEqual(snapshot["state_counts"]["failed"], 9)
            self.assertEqual(snapshot["state_counts"]["success_multipage"], 1)
            self.assertEqual(snapshot["rows"][60]["report_href"], "multipage/pdf_061/index.html")

    def test_manual_notes_are_attached_to_correct_document_and_escaped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_run(root)
            manual = {
                "scope": "fixture",
                "findings": [{
                    "rank": 2, "id": "pdf_002", "file": "second.pdf",
                    "symbol_review": "<script>wrong material</script>",
                    "contact_review": "partial",
                }],
            }
            (root / "manual-review.json").write_text(json.dumps(manual), encoding="utf-8")
            snapshot = report.build_snapshot(root)
            report.write_csv(snapshot, root)
            report.write_html(snapshot, root)
            with (root / "matrix.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle, delimiter=";"))
            self.assertEqual(rows[1][-2:], ["", ""])
            self.assertEqual(rows[2][-2:], ["<script>wrong material</script>", "partial"])
            html = (root / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("<script>wrong material</script>", html)
            self.assertIn("&lt;script&gt;wrong material&lt;/script&gt;", html)


if __name__ == "__main__":
    unittest.main()
