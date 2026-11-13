"""Focused deterministic/report-AI tests; no live LLM requests are made."""
import os
import json
import unittest
from unittest.mock import patch

import joblib

import report_ai
import report_engine
import train


class ReportAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = joblib.load(train.OUT / "yield_model.joblib")
        row = train.prepare().iloc[0]
        cls.model_values = {key: row[key] for key in train.FEATURES}
        cls.model_values["District"] = row["District"]
        cls.soil = row["Soil Type"]
        cls.area = 1.0
        cls.raw = {"state": row["State"], "district": row["District"], "crop": row["Crop"], "season": row["Season"],
            "area_acres": cls.area, "temperature": row["Temperature"], "humidity": row["Humidity"], "rainfall": row["Rainfall"],
            "ph": row["pH"], "fertilizer_kg": row["Fertilizer"] * .404685642,
            "pesticide_kg": row["Pesticide"] * .404685642, "soil_moisture": 40, "light_lux": 10000,
            "input_sources": {"temperature": "online", "humidity": "online", "rainfall": "manual"}, "language": "English"}

    def test_bad_weather_risk_increases_and_interval_probability_is_reported(self):
        bad = dict(self.model_values)
        bad.update({"Rainfall": 1.0, "Temperature": 55.0, "Humidity": 95.0, "Fertilizer": 0.0, "pH": 14.0})
        result = report_engine.build_analysis(self.bundle, self.raw, bad, self.area, self.soil)
        self.assertGreaterEqual(result["risk"]["score"], 50)
        self.assertIn(result["risk"]["status"], {"High", "Severe"})
        self.assertGreaterEqual(result["risk"]["probability_below_threshold_pct"], 0)
        self.assertEqual(len(result["charts"]["yield_distribution"]["yield_kg_ha"]), 25)

    def test_what_if_uses_only_supported_controls_in_training_range(self):
        model_values = dict(self.model_values)
        df = report_engine.historical_data()
        rows = df[df.Crop == model_values["Crop"]]
        fertilizer_total = float(rows.Fertilizer.median()) * .404685642
        changed, raw, applied = report_engine.update_model_inputs(self.bundle, model_values, self.raw,
            {"fertilizer_kg": fertilizer_total})
        self.assertEqual(applied["fertilizer_kg"], fertilizer_total)
        self.assertAlmostEqual(changed["Fertilizer"], float(rows.Fertilizer.median()), places=3)
        with self.assertRaises(ValueError):
            report_engine.update_model_inputs(self.bundle, model_values, self.raw, {"irrigation": 20})


class NarrativeTests(unittest.TestCase):
    @staticmethod
    def sample_analysis():
        return {"yield": {"yield_kg_ha": 100.0, "total_tonnes": .04, "interval_90_kg_ha": [50, 150]},
            "risk": {"status": "Moderate", "score": 30.0, "factors": []},
            "location": {"crop": "Rice", "season": "Kharif", "state": "Assam", "district": "Cachar"},
            "comparison": {"scope": "district", "mean_yield_kg_ha": 200.0},
            "what_if": {"recommended_scenario": {"change_kg_ha": 0}},
            "data_quality": {"limitations": [], "outside_training_range": [], "input_sources": []}}

    def test_schema_rejects_missing_fields(self):
        self.assertFalse(report_ai.validate_narrative({"summary": "text"}))

    def test_number_guardrail_removes_unprovided_number_sentence(self):
        narrative = {"summary": "Estimate is 123.4 kg/ha. Add 75 kg fertilizer.",
            "key_findings": [], "risk_explanations": [], "recommendations": [], "warnings": [], "next_steps": []}
        cleaned, removed = report_ai.guardrail_narrative(narrative, {"yield": {"estimate": 123.4}, "inputs": {"kg": 12.0}})
        self.assertTrue(removed)
        self.assertEqual(cleaned["summary"], "Estimate is 123.4 kg/ha.")
        self.assertTrue(any("omitted" in item for item in cleaned["warnings"]))

    def test_missing_key_uses_rule_based_fallback(self):
        analysis = self.sample_analysis()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "", "MODEL_NAME": ""}, clear=False):
            narrative, ai_used, notice, calls = report_ai.generate_narrative(analysis)
        self.assertFalse(ai_used)
        self.assertEqual(calls, 0)
        self.assertIn("standard analysis", notice)
        self.assertTrue(report_ai.validate_narrative(narrative))

    def test_mock_llm_schema_retry_and_number_guardrail(self):
        class FakeResponse:
            def __init__(self, text): self.text = text
            def raise_for_status(self): pass
            def json(self): return {"content": [{"type": "text", "text": self.text}]}
        valid = {"summary": "The estimate is 100 kg/ha.", "key_findings": ["The interval is 50 to 150 kg/ha."],
            "risk_explanations": [], "recommendations": [], "warnings": [], "next_steps": ["Ask a local agronomist to review this estimate."]}
        responses = [FakeResponse('{"bad":true}'), FakeResponse(json.dumps(valid))]
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "mock-key", "MODEL_NAME": "mock-model"}), \
             patch.object(report_ai, "_allow_rate", return_value=True), \
             patch.object(report_ai._weather_session, "post", side_effect=responses) as post:
            result, used, notice, attempts = report_ai.generate_narrative(self.sample_analysis())
        self.assertTrue(used)
        self.assertEqual(attempts, 2)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result["summary"], valid["summary"])

    def test_mock_llm_failure_returns_standard_analysis(self):
        import requests
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "mock-key", "MODEL_NAME": "mock-model"}), \
             patch.object(report_ai, "_allow_rate", return_value=True), \
             patch.object(report_ai._weather_session, "post", side_effect=requests.Timeout("mock timeout")) as post:
            narrative, used, notice, attempts = report_ai.generate_narrative(self.sample_analysis())
        self.assertFalse(used)
        self.assertIn("standard analysis", notice)
        self.assertEqual(attempts, 2)
        self.assertEqual(post.call_count, 2)
        self.assertTrue(report_ai.validate_narrative(narrative))


if __name__ == "__main__":
    unittest.main()
