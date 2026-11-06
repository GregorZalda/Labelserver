import unittest

from PIL import Image, ImageDraw, ImageOps

import catalog_rules as rules


class CatalogRulesTests(unittest.TestCase):
    def test_material_number_comes_from_text_not_old_template_id(self):
        proposed = rules.proposal_from_text(["20", "PAP"], template_id="pap_21")
        self.assertEqual(proposed["suggested_name"], "20 PAP")
        self.assertEqual(proposed["material_number"], 20)
        self.assertEqual(proposed["suggestion_kind"], "ocr_material_pair")

    def test_missing_number_is_not_inferred_from_template(self):
        proposed = rules.proposal_from_text(["PAP"], template_id="pap_21")
        self.assertIsNone(proposed["material_number"])
        self.assertIn("Nummer offen", proposed["suggested_name"])
        self.assertNotIn("21", proposed["suggested_name"])

    def test_multiple_numbers_and_inconsistent_pair_abstain(self):
        for texts in (["20", "21", "PAP"], ["22", "PET"], ["PAP"], []):
            proposed = rules.proposal_from_text(texts, template_id="pap_21")
            self.assertIsNone(proposed["material_number"])
            self.assertEqual(proposed["suggestion_kind"], "unverified_hint")

    def test_valid_pairs_are_proposals_not_certificates(self):
        for texts, expected in ((["01", "PET"], "01 PET"), (["22PAP"], "22 PAP"), (["40", "FE"], "40 FE")):
            proposed = rules.proposal_from_text(texts)
            self.assertEqual(proposed["suggested_name"], expected)
            self.assertIn("Unbestätigter", proposed["warning"])
            self.assertNotIn("approved", proposed)

    def test_exact_keys_preserve_material_number_difference(self):
        images = []
        for text in ("20 PAP", "21 PAP", "22 PAP"):
            image = Image.new("RGB", (90, 40), "white")
            ImageDraw.Draw(image).text((5, 5), text, fill="black")
            images.append(image)
        keys = [rules.exact_visual_key(image) for image in images]
        self.assertEqual(len(set(keys)), 3)
        self.assertEqual(keys[0], rules.exact_visual_key(ImageOps.expand(images[0], border=10, fill="white")))

    def test_blank_image_rejected(self):
        with self.assertRaises(ValueError):
            rules.exact_visual_key(Image.new("RGB", (30, 30), "white"))

    def test_legacy_template_results_identified_by_provenance(self):
        self.assertTrue(rules.is_legacy_template_result({
            "tool": {"name": "pdf_probe analyze_label", "version": "0.1.0"},
        }))
        self.assertTrue(rules.is_legacy_template_result({
            "reference": {"path": r"C:\old\symbol-reference.png", "symbols": [{"id": "pap_21"}]},
        }))
        self.assertFalse(rules.is_legacy_template_result({
            "tool": {"name": "pdf_probe analyze_label", "version": "0.2.0"},
            "reference": None,
        }))

    def test_old_identifier_alone_does_not_propose_symbol_name(self):
        for template_id in ("pet_01", "pap_21", "tidyman", "triman", "info_tri", "al_azul"):
            proposed = rules.proposal_from_text([], template_id)
            self.assertEqual(proposed["suggested_name"], "Unbekanntes Symbol")
            self.assertIsNone(proposed["material_number"])

    def test_sorting_text_suggests_text_family_not_graphic_certainty(self):
        for texts, expected in (
            (["Al Azul"], "Spanischer Sortierhinweis – Azul"),
            (["AI Azul"], "Spanischer Sortierhinweis – Azul"),
            (["Al Marrón"], "Spanischer Sortierhinweis – Marron"),
            (["FR", "BAC", "DE", "TRI", "BOÎTE", "PAPIER"],
             "Französische Sortierinformation – Boite, Papier"),
        ):
            proposal = rules.proposal_from_text(texts, source="Lokale Ausschnitt-OCR")
            self.assertEqual(proposal["suggested_name"], expected)
            self.assertEqual(proposal["suggestion_kind"], "ocr_sorting_text")
            self.assertIsNone(proposal["material_number"])
            self.assertIn("nicht bestätigt", proposal["warning"])
        self.assertEqual(rules.proposal_from_text(["Azul"])["suggested_name"], "Unbekanntes Symbol")

    def test_quantity_is_not_a_material_code(self):
        for text in ("10×", "25x"):
            proposal = rules.proposal_from_text([text])
            self.assertEqual(proposal["suggestion_kind"], "ocr_quantity")
            self.assertIsNone(proposal["material_number"])

    def test_context_uses_symbol_position(self):
        lines = [
            {"text": "20", "polygon_pixels": [[10, 10], [20, 10], [20, 20], [10, 20]]},
            {"text": "21", "polygon_pixels": [[100, 100], [120, 100], [120, 120], [100, 120]]},
        ]
        self.assertEqual(rules.context_texts(lines, [0, 0, 30, 30]), ["20"])

    def test_crop_ocr_keeps_observed_text(self):
        image = Image.new("RGB", (90, 40), "white")
        ImageDraw.Draw(image).text((5, 5), "20 PAP", fill="black")
        calls = []

        def engine(pixels):
            calls.append(pixels.shape)
            return [([[0, 0], [10, 0], [10, 10], [0, 10]], "20", 0.9)], None

        result = rules.read_crop(engine, image)
        self.assertEqual(result["lines"][0]["text"], "20")
        self.assertEqual(len(calls), 1)
        self.assertGreater(calls[0][0], 40)


if __name__ == "__main__":
    unittest.main()
