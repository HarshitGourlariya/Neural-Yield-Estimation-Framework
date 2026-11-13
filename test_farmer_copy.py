"""Farmer-facing report copy checks; no network requests are used."""
import os
import unittest
from unittest.mock import patch

import joblib
import train
from app import app
from farmer_report import public_report
from report_ai import fallback_narrative
from report_engine import build_analysis
from report_pdf import create_pdf
from user_messages import BANNED_PATTERNS, BANNED_TERMS, contains_banned_text


class FarmerCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()
        cls.bundle = joblib.load(train.OUT / "yield_model.joblib")
        cls.row = train.prepare().iloc[0]
        rows = train.prepare()
        local = rows[(rows.State == cls.row.State) & (rows.District == cls.row.District) &
                     (rows.Crop == cls.row.Crop) & (rows.Season == cls.row.Season)]
        cls.typical_fertilizer = float(local.Fertilizer.median()) * .404685642 if not local.empty else 20.0
        cls.state, cls.district = str(cls.row.State), str(cls.row.District)
        cls.crop, cls.season = str(cls.row.Crop), str(cls.row.Season)

    def report(self, fertilizer=None, rainfall=None, temperature=None):
        fertilizer = self.typical_fertilizer if fertilizer is None else fertilizer
        body = {"state": self.state, "district": self.district, "crop": self.crop, "season": self.season,
                "area_acres": 1, "temperature": temperature if temperature is not None else float(self.row.Temperature),
                "humidity": float(self.row.Humidity), "rainfall": rainfall if rainfall is not None else float(self.row.Rainfall),
                "ph": float(self.row["pH"]), "fertilizer_kg": fertilizer, "pesticide_kg": .1,
                "soil_moisture": 40, "light_lux": 10000,
                "input_sources": {"temperature": "online", "humidity": "online", "rainfall": "manual"}}
        created = self.client.post("/api/reports", json=body)
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        return created.json["report_id"]

    def test_rendered_page_and_pdf_do_not_expose_internal_terms(self):
        for fertilizer, rainfall, temperature in ((None, None, None), (999999, None, None), (0, 1, 55)):
            report_id = self.report(fertilizer, rainfall, temperature)
            page = self.client.get(f"/report/{report_id}")
            self.assertEqual(page.status_code, 200)
            html = page.get_data(as_text=True).casefold()
            for term in BANNED_TERMS:
                self.assertNotIn(term.casefold(), html, term)
            for pattern in BANNED_PATTERNS:
                self.assertIsNone(pattern.search(html))
            pdf = self.client.get(f"/report/{report_id}/pdf")
            self.assertEqual(pdf.status_code, 200)
            self.assertTrue(pdf.data.startswith(b"%PDF"))
            pdf_text = pdf.data.decode("latin-1").casefold()
            for term in BANNED_TERMS:
                self.assertNotIn(term.casefold(), pdf_text, term)
            for pattern in BANNED_PATTERNS:
                self.assertIsNone(pattern.search(pdf_text))

    def test_unusual_input_gets_field_warning(self):
        response = self.client.post("/api/field-check", json={"state": self.state, "district": self.district,
            "crop": self.crop, "season": self.season, "field": "fertilizer_kg", "value": 999999,
            "area_acres": 1})
        self.assertTrue(response.json["warning"])
        self.assertIn("Please check this value", response.json["warning"])
        report_id = self.report(999999)
        public = self.client.get(f"/api/reports/{report_id}").json
        self.assertTrue(public["analysis"]["check_your_inputs"])

    def test_debug_route_is_hidden_by_default_and_available_when_enabled(self):
        with patch.dict(os.environ, {"DEBUG_MODE": "false"}):
            self.assertEqual(self.client.get("/admin/model-info").status_code, 404)
        with patch.dict(os.environ, {"DEBUG_MODE": "true"}):
            response = self.client.get("/admin/model-info")
            self.assertEqual(response.status_code, 200)
            self.assertIn("mae_kg_ha", response.json)

    def test_public_projection_keeps_estimate_and_excludes_comparisons(self):
        self.assertNotIn("mae", self.client.get("/api/meta").get_json())
        self.assertNotIn("rows", self.client.get("/api/meta").get_json())


if __name__ == "__main__":
    unittest.main()
