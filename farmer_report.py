"""Project the internal report into safe, field-focused page/PDF/AI content."""
from __future__ import annotations

from report_ai import fallback_narrative
from report_engine import input_warning
from user_messages import FARMER_MESSAGES as M, sanitize_tree, strip_banned_text

_DISPLAY_FEATURE = {
    "Temperature": "Temperature", "Rainfall": "Rainfall", "Humidity": "Humidity",
    "pH": "Soil pH", "Fertilizer": "Fertilizer", "Pesticide": "Pesticide",
    "State": "Location", "Crop": "Crop", "Season": "Season", "Soil Type": "Nearby soil type",
}


def farmer_risk(analysis: dict, inputs: dict) -> dict:
    internal = analysis["risk"]
    drivers = []
    factors = {item["title"]: item for item in internal["factors"]}
    rainfall = float(inputs["rainfall"])
    rain_factor = factors.get("Rainfall comparison", {})
    ratio = float(rain_factor.get("value", 100)) / 100
    if ratio < .8:
        drivers.append({"title": "Water stress", "level": rain_factor.get("level", "Moderate"),
                        "value": f"{rainfall:g} mm/year", "explanation": M["weather_risk_low"]})
    elif ratio > 1.5:
        drivers.append({"title": "Too much rain", "level": rain_factor.get("level", "Moderate"),
                        "value": f"{rainfall:g} mm/year", "explanation": M["weather_risk_high"]})
    else:
        drivers.append({"title": "Water availability", "level": "Low", "value": f"{rainfall:g} mm/year",
                        "explanation": M["water_ok"]})

    heat = factors.get("Temperature stress screen", {})
    temp_value = float(inputs["temperature"])
    temp_band = heat.get("reference_band_c", [temp_value, temp_value])
    if temp_value > float(temp_band[1]):
        temp_text, temp_level = M["heat_risk"], heat.get("level", "Moderate")
    elif temp_value < float(temp_band[0]):
        temp_text, temp_level = M["cold_risk"], heat.get("level", "Moderate")
    else:
        temp_text, temp_level = M["temperature_ok"], "Low"
    drivers.append({"title": "Temperature", "level": temp_level,
                    "value": f"{temp_value:g} °C", "explanation": temp_text})

    humidity = float(inputs["humidity"])
    hum_factor = factors.get("Humidity and disease screen", {})
    hum_level = hum_factor.get("level", "Low")
    drivers.append({"title": "Humidity", "level": hum_level,
                    "value": f"{humidity:g}%", "explanation": M["humidity_risk"] if humidity >= 85 else M["humidity_ok"]})

    return {"status": internal["status"], "drivers": sanitize_tree(drivers)}


def farmer_recommendations(narrative: dict) -> dict:
    cleaned = dict(narrative)
    def rewrite(text):
        text = str(text).replace("tested scenario", "field preview").replace("Tested scenario", "Field preview")
        return strip_banned_text(text)
    for key in ("summary", "key_findings", "risk_explanations", "warnings", "next_steps"):
        if key == "summary":
            cleaned[key] = rewrite(cleaned.get(key, "")) or M["normal_uncertainty"]
        else:
            cleaned[key] = [safe for item in cleaned.get(key, [])
                            if (safe := rewrite(item))]
    recs = []
    for item in cleaned.get("recommendations", []):
        safe_item = {key: rewrite(item.get(key, "")) for key in
                     ("title", "action", "reason", "expected_impact", "priority", "timing")}
        if all(safe_item.values()):
            recs.append(safe_item)
    cleaned["recommendations"] = recs
    if not cleaned["summary"]:
        cleaned["summary"] = M["normal_uncertainty"]
    if not cleaned["next_steps"] or not any("agronomist" in item.casefold() or "कृषि" in item for item in cleaned["next_steps"]):
        cleaned["next_steps"] = list(cleaned["next_steps"]) + [M["check_adviser"]]
    return cleaned


def practical_actions(analysis: dict, inputs: dict) -> list[dict]:
    """Short, cautious field actions; never infer a chemical dose from yield data."""
    hindi = str(inputs.get("language", "English")).casefold() == "hindi"
    risks = {item.get("title"): item for item in analysis.get("risk", {}).get("factors", [])}
    rainfall = risks.get("Rainfall comparison", {}).get("value", 100)
    temperature = risks.get("Temperature stress screen", {}).get("level", "Low")
    humidity = float(inputs.get("humidity", 0) or 0)
    ph = float(inputs.get("ph", 7) or 7)
    actions = []
    if float(rainfall) < 80:
        action = ("Check the soil before watering. If it is dry and water is available, irrigate in smaller turns." if not hindi
                  else "पानी देने से पहले मिट्टी देखें। मिट्टी सूखी हो और पानी उपलब्ध हो, तो थोड़ी-थोड़ी सिंचाई करें।")
        actions.append(("Check water", action,
                        "Entered rainfall is low compared with the location reference." if not hindi else "दर्ज बारिश स्थान के सामान्य संदर्भ से कम है।", "High"))
    elif float(rainfall) > 150:
        action = ("After heavy rain, clear blocked field drains. Do not irrigate while water is standing." if not hindi
                  else "तेज बारिश के बाद खेत की नालियाँ साफ रखें। खेत में पानी खड़ा हो तो सिंचाई न करें।")
        actions.append(("Prevent standing water", action,
                        "Too much standing water can harm roots." if not hindi else "खेत में पानी रुकने से जड़ों को नुकसान हो सकता है।", "High"))
    if temperature in {"High", "Moderate"}:
        action = ("Check the crop during the cooler part of the day. If the soil is dry, water early in the morning." if not hindi
                  else "ठंडे समय में फसल देखें। मिट्टी सूखी हो तो सुबह जल्दी सिंचाई करें।")
        actions.append(("Watch heat stress", action,
                        "The entered temperature is outside the usual range for this crop and season." if not hindi else "दर्ज तापमान इस फसल और मौसम की आम सीमा से बाहर है।", "Medium"))
    if humidity >= 80:
        action = ("Look under leaves for spots, mildew or insects. Remove badly affected parts and ask a local KVK before spraying." if not hindi
                  else "पत्तियों के नीचे धब्बे, फफूँद या कीड़े देखें। ज्यादा प्रभावित हिस्से हटाएँ और छिड़काव से पहले KVK से पूछें।")
        actions.append(("Inspect for disease", action,
                        "Humid weather can support some crop diseases; this is not a diagnosis." if not hindi else "नमी में कुछ रोग बढ़ सकते हैं; यह रोग की पक्की पहचान नहीं है।", "Medium"))
    if ph > 7.5:
        action = ("Get a soil test and ask your KVK whether this is calcareous alkaline or sodic soil. For some calcareous soils, the soil-test plan may use elemental sulfur to lower pH over time. For confirmed sodic soil, the plan may use gypsum with suitable drainage. Do not apply either just because pH is high; the safe amount depends on the soil test." if not hindi else
                  "मिट्टी की जाँच कराकर KVK से पूछें कि मिट्टी चूना-युक्त क्षारीय है या सोडिक। कुछ चूना-युक्त मिट्टियों में जाँच की सलाह से मौलिक गंधक धीरे-धीरे pH घटा सकती है। सोडिक मिट्टी में उचित जल-निकास के साथ जिप्सम की सलाह मिल सकती है। सिर्फ़ pH ऊँचा देखकर इनमें से कुछ न डालें; सही मात्रा मिट्टी-जाँच से तय होती है।")
        reason = "The entered pH is high; the right treatment depends on soil type and a soil test." if not hindi else "दर्ज pH ऊँचा है; सही उपाय मिट्टी के प्रकार और जाँच पर निर्भर करता है।"
        actions.append(("If soil pH is high", action, reason, "High"))
    elif ph < 5.5:
        action = ("Ask for a soil test and lime requirement before adding agricultural lime. The amount depends on soil type and test results." if not hindi else
                  "कृषि चूना डालने से पहले मिट्टी की जाँच और चूने की जरूरत पता करें। मात्रा मिट्टी के प्रकार और जाँच के नतीजे पर निर्भर करती है।")
        reason = "Low pH can limit some crops, but the safe correction amount is soil-specific." if not hindi else "कम pH कुछ फसलों को प्रभावित कर सकता है, लेकिन सही मात्रा मिट्टी के अनुसार तय होती है।"
        actions.append(("If soil pH is low", action, reason, "High"))
    actions.append(("Use fertilizer carefully",
        "Follow the crop and field instructions on the fertilizer label or soil-test advice. Do not add extra just to chase the estimate." if not hindi else
        "खाद की मात्रा फसल के लेबल या मिट्टी-जाँच की सलाह के अनुसार रखें। अनुमान बढ़ाने के लिए अतिरिक्त खाद न डालें।",
        "The yield estimate cannot determine a safe fertilizer dose." if not hindi else "उपज का अनुमान सुरक्षित खाद की मात्रा तय नहीं कर सकता।", "Medium"))
    actions.append(("Spray only for a confirmed problem",
        "Do not spray pesticide just to raise yield. First identify the pest or disease; use only a product approved for this crop and follow its label dose and safety steps. Ask your local KVK if unsure." if not hindi else
        "उपज बढ़ाने के लिए बिना जाँच कीटनाशक न छिड़कें। पहले कीट या रोग पहचानें; इस फसल के लिए स्वीकृत दवा ही लेबल की मात्रा और सुरक्षा नियमों के अनुसार डालें। संदेह हो तो KVK से पूछें।",
        "The estimate cannot identify a pest or safely calculate a pesticide dose." if not hindi else "यह अनुमान कीट नहीं पहचान सकता और न ही कीटनाशक की सुरक्षित मात्रा बता सकता है।", "High"))
    return [{"title": title, "action": action, "reason": reason,
             "expected_impact": "No yield change is promised." if not hindi else "उपज बढ़ने की गारंटी नहीं है।",
             "priority": priority, "timing": "During this crop season" if not hindi else "इस फसल के मौसम में"}
            for title, action, reason, priority in actions]


def public_report(report: dict) -> dict:
    analysis = report["analysis"]
    location, result = analysis["location"], analysis["yield"]
    inputs = report["inputs"]
    risk = farmer_risk(analysis, inputs)
    source_map = inputs.get("input_sources", {})
    input_summary = []
    labels = {
        "temperature": ("Annual temperature", "°C"), "humidity": ("Annual humidity", "%"),
        "rainfall": ("Annual rainfall", "mm/year"), "ph": ("Soil pH", ""),
        "fertilizer_kg": ("Fertilizer used", "kg"), "pesticide_kg": ("Pesticide used", "kg"),
        "soil_moisture": ("Soil moisture", "%"), "light_lux": ("Light intensity", "lux"),
        "area_acres": ("Field area", "acres"),
    }
    for key, (label, unit) in labels.items():
        raw_source = str(source_map.get(key, "manual")).casefold()
        source = M["source_sensor"] if "sensor" in raw_source and "not connected" not in raw_source else M["source_online"] if "online" in raw_source else M["source_manual"]
        input_summary.append({"name": label, "value": inputs.get(key), "unit": unit})

    # Recalculate these display-only warnings so saved reports also benefit
    # from the current rounding tolerance and clear, non-prescriptive wording.
    warning_fields = {"temperature": "temperature", "humidity": "humidity", "rainfall": "rainfall",
                      "ph": "ph", "fertilizer_kg": "fertilizer_kg", "pesticide_kg": "pesticide_kg"}
    warnings = [input_warning(location["state"], location["district"], location["crop"], location["season"],
                              field, float(inputs[key]), float(inputs.get("area_acres", 1)))
                for key, field in warning_fields.items()
                if inputs.get(key) not in (None, "")]
    warnings = [strip_banned_text(item) for item in warnings]
    warnings = [item for item in warnings if item]
    current_input = {key: inputs.get(key) for key in labels}
    ranges = result.get("interval_90_kg_ha", [result["yield_kg_ha"], result["yield_kg_ha"]])
    public_analysis = {
        "location": {"state": location["state"], "district": location["district"], "crop": location["crop"],
                     "season": location["season"], "soil_type": location["soil_type"]},
        "estimate": {"yield_kg_ha": result["yield_kg_ha"], "likely_range_kg_ha": ranges,
                     "area_acres": result["area_acres"], "total_tonnes": result["total_tonnes"],
                     "total_tonnes_range": result["total_tonnes_interval"]},
        "risk": risk,
        "input_summary": input_summary,
        "check_your_inputs": warnings,
        "what_if": {"scenarios": {
            key: {"label": val.get("label", key.title()), "unit": val.get("unit"),
                  "current_total_kg": val.get("current_total_kg"), "current_value": val.get("current_value"),
                  "curve": [{"input_total_kg": p.get("input_total_kg"), "input_value": p.get("input_value"),
                             "yield_kg_ha": p.get("yield_kg_ha")}
                            for p in val.get("curve", [])]}
            for key, val in analysis["what_if"].get("scenarios", {}).items()},
            "recommended_scenario": {k: v for k, v in analysis["what_if"].get("recommended_scenario", {}).items()
                                     if k in {"title", "input_total_kg", "change_kg_ha"}}},
        "charts": {
            "fertilizer": None,
            "pesticide": None,
            "input_impacts": [],
            "scenarios": {"labels": ["Current", "Changed"], "yield_kg_ha": [result["yield_kg_ha"], analysis["what_if"].get("recommended_scenario", {}).get("yield_kg_ha", result["yield_kg_ha"])]},
            "weather": {"temperature_c": inputs["temperature"], "rainfall_mm": inputs["rainfall"],
                        "humidity_pct": inputs["humidity"]},
        },
        "input_values": current_input,
    }
    public_analysis["charts"]["fertilizer"] = public_analysis["what_if"]["scenarios"].get("fertilizer")
    public_analysis["charts"]["pesticide"] = public_analysis["what_if"]["scenarios"].get("pesticide")
    public_analysis = sanitize_tree(public_analysis)
    clean_narrative = farmer_recommendations(report.get("narrative", {}))
    if not clean_narrative.get("key_findings") or not clean_narrative.get("recommendations"):
        clean_narrative = farmer_recommendations(fallback_narrative(analysis, inputs.get("language", "English")))
    clean_narrative["recommendations"] = practical_actions(analysis, inputs)
    public_inputs = {key: inputs.get(key) for key in labels}
    public_inputs.update({"state": location["state"], "district": location["district"], "crop": location["crop"],
                          "season": location["season"], "language": inputs.get("language", "English")})
    return {"id": report["id"], "created_at": report["created_at"], "inputs": public_inputs,
            "language": inputs.get("language", "English"),
            "ai_provider": report.get("ai_provider", ""), "preview": bool(report.get("preview", False)),
            "ai_notice": M["ai_fallback"] if not report.get("ai_used", False) else "",
            "analysis": public_analysis, "narrative": clean_narrative}
