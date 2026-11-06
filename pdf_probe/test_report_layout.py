"""Browser layout regression for the report overlay alignment (.page-wrap).

Launches an installed Edge/Chrome via Playwright and measures whether the
absolutely positioned .ovl boxes coincide with the rendered image bounds.
Evidence (screenshots + metrics JSON) is written to OVERLAY_VERIFY_DIR.
"""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_label as al

BASE_DIR = Path(__file__).resolve().parent
VERIFY_DIR = Path(
    os.environ.get(
        "OVERLAY_VERIFY_DIR",
        BASE_DIR / "artifacts" / "overlay-verification" / "run",
    )
)
VIEWPORTS = (1440, 1000, 600)
TOL_WRAPPER_PX = 1.0
TOL_BOX_PX = 1.5

BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)

CASES = (
    ("pdf001", BASE_DIR / "artifacts" / "result" / "latest100-20261105" / "pdf_001"),
    ("original824101", BASE_DIR / "artifacts" / "result"),
)

MEASURE_JS = """() => {
    const wrap = document.querySelector('.page-wrap');
    const img = wrap ? wrap.querySelector('img') : null;
    if (!wrap || !img) return {error: 'no .page-wrap/img'};
    const r = el => { const b = el.getBoundingClientRect();
        return {x: b.x, y: b.y, width: b.width, height: b.height}; };
    const boxes = [...wrap.querySelectorAll('.ovl')].map(el => ({
        label: el.dataset.symbolId || '',
        footer: el.classList.contains('footer'),
        rect: r(el),
        display: getComputedStyle(el).display,
    }));
    return {wrap: r(wrap), img: r(img), boxes: boxes,
            natural: {w: img.naturalWidth, h: img.naturalHeight}};
}"""


def find_browser():
    for path in BROWSER_CANDIDATES:
        if Path(path).is_file():
            return path
    return None


def expected_boxes(result):
    render = result.get("render") or {}
    w, h, dpi = render.get("width_px"), render.get("height_px"), render.get("dpi") or 300
    out = {}
    for sym in result.get("symbols", []):
        bbox = sym.get("bbox_pixels")
        if bbox and w and h:
            out[sym["id"]] = (bbox[0] / w, bbox[1] / h, (bbox[2] - bbox[0]) / w, (bbox[3] - bbox[1]) / h)
    contacts = result.get("contacts") or {}
    bp = contacts.get("bbox_points")
    if bp and w and h:
        s = dpi / 72.0
        out["__footer__"] = (bp[0] * s / w, bp[1] * s / h, (bp[2] - bp[0]) * s / w, (bp[3] - bp[1]) * s / h)
    return out, w, h


class ReportLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.browser_path = find_browser()
        if not cls.browser_path:
            raise unittest.SkipTest("Kein installierter Edge/Chrome gefunden.")
        from playwright.sync_api import sync_playwright

        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch(
            executable_path=cls.browser_path, headless=True
        )
        VERIFY_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def _measure(self, case_name, report_dir, viewport):
        index = report_dir / "index.html"
        result_json = report_dir / "result.json"
        self.assertTrue(index.is_file(), f"fehlt: {index}")
        result = json.loads(result_json.read_text(encoding="utf-8"))
        exp, render_w, render_h = expected_boxes(result)

        page = self.browser.new_page(viewport={"width": viewport, "height": 1200})
        try:
            page.goto(index.resolve().as_uri(), wait_until="load")
            page.wait_for_function(
                "document.querySelector('.page-wrap img') && "
                "document.querySelector('.page-wrap img').complete && "
                "document.querySelector('.page-wrap img').naturalWidth > 0"
            )
            data = page.evaluate(MEASURE_JS)
            shot = VERIFY_DIR / f"{case_name}-{viewport}px.png"
            page.locator(".page-wrap").screenshot(path=str(shot))
        finally:
            page.close()

        metrics = {
            "case": case_name,
            "viewport_px": viewport,
            "report": str(index),
            "screenshot": str(shot),
            "render_px": [render_w, render_h],
            "wrap": data["wrap"],
            "img": data["img"],
            "boxes": [],
        }
        for key in ("x", "y", "width", "height"):
            metrics.setdefault("wrap_delta", {})[key] = abs(
                data["wrap"][key] - data["img"][key]
            )
        measured = set()
        for box in data["boxes"]:
            label = "__footer__" if box["footer"] else box["label"]
            measured.add(label)
            frac = exp.get(label)
            entry = {"label": label, "actual": box["rect"], "expected_frac": frac}
            if frac:
                er = data["img"]
                expected = {
                    "x": er["x"] + frac[0] * er["width"],
                    "y": er["y"] + frac[1] * er["height"],
                    "width": frac[2] * er["width"],
                    "height": frac[3] * er["height"],
                }
                entry["expected"] = expected
                entry["delta"] = {
                    k: abs(expected[k] - box["rect"][k]) for k in expected
                }
            metrics["boxes"].append(entry)
        metrics["missing_expected"] = sorted(set(exp) - measured)
        metrics["unexpected_boxes"] = sorted(measured - set(exp))
        (VERIFY_DIR / f"{case_name}-{viewport}px.json").write_text(
            json.dumps(metrics, indent=1), encoding="utf-8"
        )
        return metrics

    def _assert_layout(self, metrics):
        self.assertEqual(
            metrics["missing_expected"],
            [],
            f"{metrics['case']}@{metrics['viewport_px']} erwartete Boxen nicht "
            f"gemessen: {metrics['missing_expected']}",
        )
        for key, delta in metrics["wrap_delta"].items():
            self.assertLessEqual(
                delta,
                TOL_WRAPPER_PX,
                f"{metrics['case']}@{metrics['viewport_px']} wrapper/img {key} drift {delta:.3f}px",
            )
        for box in metrics["boxes"]:
            if "delta" not in box:
                continue
            for key, delta in box["delta"].items():
                self.assertLessEqual(
                    delta,
                    TOL_BOX_PX,
                    f"{metrics['case']}@{metrics['viewport_px']} box {box['label']} "
                    f"{key} drift {delta:.3f}px",
                )

    def test_pdf001_layout(self):
        metrics = [
            self._measure("pdf001", CASES[0][1], vp) for vp in VIEWPORTS
        ]
        for m in metrics:
            self._assert_layout(m)

    def test_original_layout(self):
        metrics = [
            self._measure("original824101", CASES[1][1], vp) for vp in VIEWPORTS
        ]
        for m in metrics:
            self._assert_layout(m)

    def test_overlay_toggle(self):
        page = self.browser.new_page(viewport={"width": 1440, "height": 1200})
        try:
            page.goto((CASES[1][1] / "index.html").resolve().as_uri(), wait_until="load")
            page.wait_for_function(
                "document.querySelector('.page-wrap img') && "
                "document.querySelector('.page-wrap img').complete"
            )
            n = page.evaluate("document.querySelectorAll('.ovl').length")
            self.assertGreater(n, 0)
            page.click("#ovl-toggle")
            hidden = page.evaluate(
                "[...document.querySelectorAll('.ovl')].filter(e => "
                "getComputedStyle(e).display !== 'none').length"
            )
            self.assertEqual(hidden, 0)
            page.click("#ovl-toggle")
            visible = page.evaluate(
                "[...document.querySelectorAll('.ovl')].filter(e => "
                "getComputedStyle(e).display !== 'none').length"
            )
            self.assertEqual(visible, n)
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main()
