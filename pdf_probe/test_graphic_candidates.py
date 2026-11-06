import unittest

from PIL import Image, ImageDraw

import graphic_candidates as graphics


class GraphicCandidateTests(unittest.TestCase):
    def test_blank_page_has_no_candidates(self):
        self.assertEqual(graphics.find_graphic_candidates(Image.new("RGB", (300, 400), "white")), [])

    def test_unfamiliar_graphic_is_found_without_classification(self):
        image = Image.new("RGB", (300, 300), "white")
        ImageDraw.Draw(image).polygon([(100, 60), (150, 130), (60, 110)], outline="black", width=5)
        candidates = graphics.find_graphic_candidates(image)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["candidate_kind"], "graphic_region")
        self.assertIsNone(candidate["score"])
        self.assertTrue(candidate["review_required"])
        self.assertEqual(candidate["label"], "Unbenannter Grafikkandidat")
        x0, y0, x1, y1 = candidate["bbox_pixels"]
        self.assertLessEqual(x0, 60)
        self.assertLessEqual(y0, 60)
        self.assertGreaterEqual(x1, 150)
        self.assertGreaterEqual(y1, 130)

    def test_two_nearby_symbols_stay_separate(self):
        image = Image.new("RGB", (300, 300), "white")
        drawing = ImageDraw.Draw(image)
        drawing.ellipse((30, 40, 90, 100), outline="black", width=5)
        drawing.rectangle((105, 40, 165, 100), outline="black", width=5)
        self.assertEqual(len(graphics.find_graphic_candidates(image)), 2)

    def test_known_text_ink_is_not_an_extra_graphic(self):
        image = Image.new("RGB", (300, 300), "white")
        drawing = ImageDraw.Draw(image)
        drawing.rectangle((20, 20, 80, 35), fill="black")
        drawing.ellipse((100, 100, 170, 170), outline="black", width=5)
        native = [{"text": "WORD", "bbox": [20 * 72 / 300, 20 * 72 / 300, 81 * 72 / 300, 36 * 72 / 300]}]
        candidates = graphics.find_graphic_candidates(image, native_lines=native)
        self.assertEqual(len(candidates), 1)
        self.assertGreater(candidates[0]["bbox_pixels"][1], 80)

    def test_repeated_barcode_strokes_not_catalogued_as_symbols(self):
        image = Image.new("RGB", (300, 300), "white")
        drawing = ImageDraw.Draw(image)
        for x in range(30, 200, 8):
            drawing.rectangle((x, 40, x + 3, 170), fill="black")
        self.assertEqual(graphics.find_graphic_candidates(image), [])

    def test_connected_graphic_is_not_split_by_text_inside_it(self):
        image = Image.new("RGB", (250, 250), "white")
        ImageDraw.Draw(image).ellipse((40, 40, 160, 160), fill="black")
        ocr = [{
            "text": "25", "score": 0.99,
            "polygon_pixels": [[70, 80], [130, 80], [130, 120], [70, 120]],
        }]
        candidates = graphics.find_graphic_candidates(image, ocr_lines=ocr)
        self.assertEqual(len(candidates), 1)
        self.assertGreater(candidates[0]["bbox_pixels"][3] - candidates[0]["bbox_pixels"][1], 120)

    def test_crop_ocr_proposals_do_not_approve_or_use_templates(self):
        image = Image.new("RGB", (200, 200), "white")
        ImageDraw.Draw(image).rectangle((40, 40, 150, 150), outline="black", width=5)
        candidates = graphics.find_graphic_candidates(image)

        def engine(_):
            return [
                ([[0, 0], [20, 0], [20, 20], [0, 20]], "20", 0.99),
                ([[0, 22], [40, 22], [40, 40], [0, 40]], "PAP", 0.99),
            ], None

        enriched = graphics.add_ocr_proposals(candidates, image, engine)
        self.assertEqual(enriched[0]["proposal"]["suggested_name"], "20 PAP")
        self.assertEqual(enriched[0]["crop_ocr"]["lines"][0]["text"], "20")
        self.assertTrue(enriched[0]["review_required"])
        self.assertNotIn("approved", enriched[0])
        self.assertEqual(enriched[0]["id"], candidates[0]["id"])


if __name__ == "__main__":
    unittest.main()
