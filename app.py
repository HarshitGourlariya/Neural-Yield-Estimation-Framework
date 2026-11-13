from __future__ import annotations

import json
import math
import os
import uuid
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from flask import Flask, abort, jsonify, make_response, render_template, request, send_from_directory

from predict import prediction
from report_ai import generate_narrative
from report_engine import build_analysis, historical_data, update_model_inputs
from report_pdf import create_pdf
from report_store import get as get_saved_report, save as save_report
from farmer_report import public_report
from report_engine import input_warning
from user_messages import FARMER_MESSAGES
from weather_service import WeatherServiceError, bundled_locations_by_state, get_weather

ROOT = Path(__file__).parent
MODEL_PATH = ROOT / "models" / "yield_model.joblib"
REPORT_PATH = ROOT / "models" / "training_report.json"
app = Flask(__name__)
_rate_windows = {}


@app.before_request
def limit_public_requests():
    """Basic per-process throttling for public calls; production should use a shared gateway limiter."""
    limits = {"/api/weather": (30, 60), "/api/reports": (12, 60), "/api/field-check": (30, 60)}
    limit = limits.get(request.path)
    if request.method != "POST" and request.path != "/api/weather":
        return None
    if request.path.startswith("/api/reports/") and request.method == "POST":
        limit = (20, 60)
    if not limit:
        return None
    maximum, seconds = limit
    now = time.monotonic()
    key = (request.remote_addr or "unknown", request.path)
    start, count = _rate_windows.get(key, (now, 0))
    if now - start >= seconds:
        start, count = now, 0
    if count >= maximum:
        response = jsonify({"error": "Too many requests. Please wait a minute and try again."})
        response.status_code = 429
        response.headers["Retry-After"] = str(max(1, int(seconds - (now - start))))
        return response
    _rate_windows[key] = (start, count + 1)
    if len(_rate_windows) > 5000:
        _rate_windows.clear()
    return None


@app.after_request
def add_basic_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    return response

@app.context_processor
def inject_farmer_messages():
    return {"messages": FARMER_MESSAGES}


@lru_cache(maxsize=1)
def load_model():
    return joblib.load(MODEL_PATH) if MODEL_PATH.exists() else None


@lru_cache(maxsize=1)
def load_report():
    return json.loads(REPORT_PATH.read_text(encoding="utf-8")) if REPORT_PATH.exists() else None


def available_locations(report):
    locations = {state: list(values) for state, values in report["districts_by_state"].items()}
    for state, bundled in bundled_locations_by_state().items():
        locations[state] = sorted(set(locations.get(state, [])) | set(bundled))
    return locations


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"ready": True}), 200


@app.get("/service-worker.js")
def service_worker():
    response = make_response(send_from_directory(ROOT / "static", "service-worker.js"))
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/manifest.webmanifest")
def pwa_manifest():
    response = make_response(send_from_directory(ROOT, "manifest.webmanifest"))
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/offline-report.html")
def offline_report_shell():
    return send_from_directory(ROOT / "static", "offline-report.html")


@app.get("/report/offline/<report_id>")
def offline_report_page(report_id):
    return send_from_directory(ROOT / "static", "offline-report.html")


@app.get("/api/meta")
def meta():
    report = load_report()
    if not report:
        return jsonify({"ready": False, "message": "The estimate service is not ready yet. Please try again shortly."}), 503
    crop_seasons = historical_data()[["Crop", "Season"]].dropna().drop_duplicates()
    return jsonify({"ready": True,
        "districts_by_state": available_locations(report),
        "typical_soil_by_region": report["typical_soil_by_region"],
        "crops": report["crops"], "seasons": report["seasons"],
        "crops_by_season": {season: sorted(crop_seasons.loc[
            crop_seasons.Season == season, "Crop"].astype(str).unique().tolist())
            for season in report["seasons"]}})


@app.get("/api/weather")
def weather():
    """Best-effort online defaults; failures are returned as recoverable errors."""
    report = load_report()
    if not report:
        return jsonify({"error": "Location information is not ready. You can still enter weather manually."}), 503
    state, location = request.args.get("state", "").strip(), request.args.get("location", "").strip()
    if location not in available_locations(report).get(state, []):
        return jsonify({"error": "Choose a location from the selected state."}), 400
    try:
        result = get_weather(state, location)
    except WeatherServiceError as exc:
        return jsonify({"error": f"Could not fetch weather: {exc} Enter the values manually.", "step": exc.stage}), 502
    except Exception:
        app.logger.exception("Unexpected weather lookup failure")
        return jsonify({"error": "Online weather is temporarily unavailable. Enter the values manually."}), 502
    return jsonify(result)


@app.post("/api/field-check")
def field_check():
    body = request.get_json(silent=True) or {}
    try:
        warning = input_warning(str(body["state"]), str(body["district"]), str(body["crop"]),
                                str(body.get("season", "")), str(body["field"]),
                                float(body["value"]), float(body.get("area_acres", 1)))
        return jsonify({"warning": warning})
    except (KeyError, TypeError, ValueError):
        return jsonify({"warning": ""})


@app.post("/api/predict")
def predict():
    bundle, report = load_model(), load_report()
    if bundle is None or report is None:
        return jsonify({"error": "The estimate service is not ready yet. Please try again shortly."}), 503
    body = request.get_json(silent=True) or {}
    try:
        state, district = str(body["state"]), str(body["district"])
        if district not in report["districts_by_state"].get(state, []):
            raise ValueError("Choose a district from the selected state.")
        area = float(body["area_acres"])
        if area <= 0: raise ValueError("Field area must be greater than zero.")
        hectare = area * .404685642
        region = f"{district}, {state}"
        soil = report["typical_soil_by_region"].get(region)
        if soil is None:
            # Physical soil fallback: most common soil class within the state.
            state_regions = [k for k in report["typical_soil_by_region"] if k.endswith(", " + state)]
            soils = [report["typical_soil_by_region"][k] for k in state_regions]
            soil = max(set(soils), key=soils.count) if soils else report["soil_types"][0]
        values = {"State": state, "Crop": str(body["crop"]), "Season": str(body["season"]), "Soil Type": soil,
            "Temperature": float(body["temperature"]), "Rainfall": float(body["rainfall"]),
            "Humidity": float(body["humidity"]), "pH": float(body["ph"]),
            "Fertilizer": float(body["fertilizer_kg"]) / hectare,
            "Pesticide": float(body["pesticide_kg"]) / hectare}
        if not (0 <= values["Humidity"] <= 100 and 0 < values["pH"] <= 14 and 0 <= float(body["soil_moisture"]) <= 100):
            raise ValueError("Check humidity (0–100%), soil moisture (0–100%), and soil pH (0–14).")
        if not (-10 <= values["Temperature"] <= 55 and 0 <= float(body["light_lux"]) <= 200000):
            raise ValueError("Temperature must be -10–55 °C and light 0–200,000 lux.")
        if not (0 <= values["Rainfall"] <= 12000 and values["Fertilizer"] >= 0 and values["Pesticide"] >= 0):
            raise ValueError("Rainfall must be 0–12,000 mm/year; fertilizer and pesticide must be zero or greater.")
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"error": str(exc) or "Enter all required values."}), 400

    result = prediction(bundle, values)
    point = result["yield_kg_ha"]
    low, high = result["interval_90_kg_ha"]
    coverage_note = "" if region in report["typical_soil_by_region"] else "A nearby soil type is used as a guide for this location."
    return jsonify({"yield_kg_ha": round(point, 1), "yield_t_ha": round(point/1000, 2),
        "total_tonnes": round(point * hectare / 1000, 2), "area_acres": area,
        "low_kg_ha": round(low, 1), "high_kg_ha": round(high, 1),
        "typical_soil": soil,
        "coverage_note": coverage_note,
        "stress_factors": result["stress_factors"],
        "sensor_note": "Your entered weather, soil pH and farm inputs are included. Soil moisture and light are saved for reference."})


def validate_report_inputs(body, report):
    state, district = str(body.get("state", "")).strip(), str(body.get("district", "")).strip()
    if district not in available_locations(report).get(state, []):
        raise ValueError("Choose a district from the selected state.")
    crop, season = str(body.get("crop", "")), str(body.get("season", ""))
    if crop not in report["crops"] or season not in report["seasons"]:
        raise ValueError("Choose a season first, then choose a crop from that season.")
    if crop not in set(historical_data().loc[historical_data().Season == season, "Crop"].dropna().astype(str)):
        raise ValueError("That crop is not listed for the selected season. Choose a crop shown for that season.")
    def number(key):
        try: value = float(body[key])
        except (KeyError, TypeError, ValueError): raise ValueError(f"Enter a valid value for {key.replace('_', ' ')}.")
        if not math.isfinite(value): raise ValueError(f"Enter a finite value for {key.replace('_', ' ')}.")
        return value
    area = number("area_acres")
    if not .01 <= area <= 1_000_000: raise ValueError("Field area must be between 0.01 and 1,000,000 acres.")
    temperature, humidity, rainfall = number("temperature"), number("humidity"), number("rainfall")
    ph, fertilizer, pesticide = number("ph"), number("fertilizer_kg"), number("pesticide_kg")
    moisture, light = number("soil_moisture"), number("light_lux")
    if not -10 <= temperature <= 55: raise ValueError("Temperature must be between -10 and 55 °C.")
    if not 0 <= humidity <= 100: raise ValueError("Humidity must be between 0 and 100%.")
    if not 0 <= rainfall <= 12000: raise ValueError("Rainfall must be between 0 and 12,000 mm/year.")
    if not 0 < ph <= 14: raise ValueError("Soil pH must be between 0 and 14.")
    if not 0 <= moisture <= 100: raise ValueError("Soil moisture must be between 0 and 100%.")
    if not 0 <= light <= 200000: raise ValueError("Light intensity must be between 0 and 200,000 lux.")
    if not 0 <= fertilizer <= 1_000_000 or not 0 <= pesticide <= 1_000_000:
        raise ValueError("Fertilizer and pesticide must be between 0 and 1,000,000 kg total.")
    region = f"{district}, {state}"
    soil = report["typical_soil_by_region"].get(region)
    if soil is None:
        state_regions = [k for k in report["typical_soil_by_region"] if k.endswith(", " + state)]
        soils = [report["typical_soil_by_region"][k] for k in state_regions]
        soil = max(set(soils), key=soils.count) if soils else report["soil_types"][0]
    area_ha = area * .404685642
    model_values = {"State": state, "District": district, "Crop": crop, "Season": season, "Soil Type": soil,
        "Temperature": temperature, "Rainfall": rainfall, "Humidity": humidity, "pH": ph,
        "Fertilizer": fertilizer / area_ha, "Pesticide": pesticide / area_ha}
    raw = {key: body.get(key) for key in ("state", "district", "crop", "season", "area_acres", "temperature", "humidity",
        "rainfall", "ph", "fertilizer_kg", "pesticide_kg", "soil_moisture", "light_lux")}
    raw["input_sources"] = body.get("input_sources") if isinstance(body.get("input_sources"), dict) else {}
    raw["language"] = body.get("language", "English") if body.get("language") in ("English", "Hindi") else "English"
    return raw, model_values, area, soil


def create_saved_report(body, report_data, bundle, report_id=None):
    raw, model_values, area, soil = validate_report_inputs(body, report_data)
    analysis = build_analysis(bundle, raw, model_values, area, soil, raw["input_sources"])
    narrative, ai_used, ai_notice, ai_calls = generate_narrative(analysis, raw["language"])
    ai_provider = ("Gemini" if ai_used and os.getenv("GEMINI_API_KEY", "").strip()
                   else "Anthropic" if ai_used else "")
    report_id = report_id or uuid.uuid4().hex
    created = datetime.now(timezone.utc).isoformat()
    save_report(report_id, raw, analysis, narrative, ai_used, ai_calls, created, ai_provider)
    return {"id": report_id, "created_at": created, "inputs": raw, "analysis": analysis,
            "narrative": narrative, "ai_used": ai_used, "ai_calls": ai_calls,
            "ai_provider": ai_provider, "ai_notice": ai_notice}


@app.post("/api/reports")
def create_report():
    bundle, report_data = load_model(), load_report()
    if bundle is None or report_data is None:
        return jsonify({"error": "The estimate service is not ready yet. Please try again shortly."}), 503
    body = request.get_json(silent=True) or {}
    try:
        result = create_saved_report(body, report_data, bundle)
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"error": str(exc) or "Check the field inputs and try again."}), 400
    except Exception:
        app.logger.exception("Report creation failed")
        return jsonify({"error": "Could not build the report. Please check the inputs and try again."}), 500
    return jsonify({"report_id": result["id"], "report_url": f"/report/{result['id']}"}), 201


@app.get("/report/<report_id>")
def report_page(report_id):
    result = get_saved_report(report_id)
    if not result: abort(404)
    return render_template("report.html", report=public_report(result))


@app.get("/api/reports/<report_id>")
def report_json(report_id):
    result = get_saved_report(report_id)
    if not result: return jsonify({"error": "Report not found."}), 404
    return jsonify(public_report(result))


@app.post("/api/reports/<report_id>/what-if")
def report_what_if(report_id):
    saved, bundle, report_data = get_saved_report(report_id), load_model(), load_report()
    if not saved: return jsonify({"error": "Report not found."}), 404
    if bundle is None or report_data is None: return jsonify({"error": "The estimate service is not ready yet. Please try again shortly."}), 503
    try:
        body = request.get_json(silent=True) or {}
        model_values = {"State": saved["inputs"]["state"], "District": saved["inputs"]["district"],
            "Crop": saved["inputs"]["crop"], "Season": saved["inputs"]["season"],
            "Soil Type": saved["analysis"]["location"]["soil_type"],
            "Temperature": float(saved["inputs"]["temperature"]), "Humidity": float(saved["inputs"]["humidity"]),
            "Rainfall": float(saved["inputs"]["rainfall"]), "pH": float(saved["inputs"]["ph"]),
            "Fertilizer": float(saved["inputs"]["fertilizer_kg"]) / (float(saved["inputs"]["area_acres"]) * .404685642),
            "Pesticide": float(saved["inputs"]["pesticide_kg"]) / (float(saved["inputs"]["area_acres"]) * .404685642)}
        model_values, raw_inputs, _ = update_model_inputs(bundle, model_values, saved["inputs"], body)
        analysis = build_analysis(bundle, raw_inputs, model_values, float(raw_inputs["area_acres"]),
                                  saved["analysis"]["location"]["soil_type"], saved["inputs"].get("input_sources", {}))
        changed = dict(saved); changed["analysis"] = analysis
        changed["preview"] = True
        return jsonify({"report": public_report(changed)})
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/reports/<report_id>/regenerate")
def regenerate_report_advice(report_id):
    saved, bundle, report_data = get_saved_report(report_id), load_model(), load_report()
    if not saved: return jsonify({"error": "Report not found."}), 404
    if bundle is None or report_data is None: return jsonify({"error": "The estimate service is not ready yet. Please try again shortly."}), 503
    try:
        body = request.get_json(silent=True) or {}
        inputs = dict(saved["inputs"])
        inputs.update({key: body[key] for key in ("fertilizer_kg", "pesticide_kg", "ph", "temperature",
                                                   "humidity", "rainfall") if key in body})
        inputs["language"] = body.get("language", inputs.get("language", "English"))
        result = create_saved_report(inputs, report_data, bundle)
        return jsonify({"report": public_report(result)}), 201
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:
        app.logger.exception("Report advice regeneration failed")
        return jsonify({"error": "Could not regenerate advice. The existing report is still available."}), 500


@app.get("/report/<report_id>/pdf")
def report_pdf(report_id):
    result = get_saved_report(report_id)
    if not result: abort(404)
    result = public_report(result)
    try:
        pdf_data = create_pdf(result)
    except Exception:
        app.logger.exception("Report PDF rendering failed")
        return "Could not create this PDF right now.", 500
    response = make_response(pdf_data)
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"attachment; filename=greenyield-{report_id[:10]}.pdf"
    return response


@app.get("/admin/model-info")
def developer_model_info():
    if os.getenv("DEBUG_MODE", "").casefold() != "true":
        abort(404)
    info = load_report()
    if not info: abort(503)
    return jsonify({"name": info.get("model"), "rows": info.get("rows"), "mae_kg_ha": info.get("mae_kg_ha"),
                    "r2": info.get("r2"), "year_range": info.get("historical_year_range"),
                    "states": info.get("states"), "features": info.get("features")})


@app.post("/api/sensors")
def sensors():
    body = request.get_json(silent=True) or {}
    expected = {"temperature": (-10, 55), "humidity": (0, 100), "soil_moisture": (0, 100), "light_lux": (0, 200000), "ph": (0, 14)}
    readings = {}
    try:
        for key, (lo, hi) in expected.items():
            if key in body and body[key] is not None:
                val = float(body[key])
                if not lo <= val <= hi: return jsonify({"error": f"{key} is outside the accepted range."}), 400
                readings[key] = val
        if not readings: return jsonify({"error": "Send at least one supported sensor reading."}), 400
    except (TypeError, ValueError): return jsonify({"error": "Sensor readings must be numeric."}), 400
    return jsonify({"accepted": True, "source": body.get("source", "esp32"), "readings": readings,
                    "note": "Readings are validated and echoed; persistent sensor history is not enabled."})


if __name__ == "__main__": app.run(host="127.0.0.1", port=5000, debug=False)
