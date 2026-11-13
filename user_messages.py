"""Shared farmer-facing copy and a last-line filter for technical report text."""
from __future__ import annotations

import re

FARMER_MESSAGES = {
    "ready": "Ready when you are",
    "preparing": "Getting your field estimate ready",
    "service_error": "The estimate service is temporarily unavailable. Please try again shortly.",
    "input_error": "Please check the highlighted field values and try again.",
    "weather_title": "Weather conditions",
    "weather_hint": "Typical yearly weather for the selected location",
    "weather_unavailable": "Weather could not be filled automatically. Enter these values manually.",
    "weather_wait": "Getting weather for {location}…",
    "weather_current": "Current conditions (for reference): {temperature} °C and {humidity}% humidity.",
    "weather_recent": "Recent conditions (for reference): {temperature} °C, {humidity}% humidity and {rainfall} mm of rain.",
    "weather_summary": "Weather for your location",
    "auto_weather": "Weather filled automatically",
    "edited_manually": "Edited manually",
    "choose_location": "Choose a location to fill this field.",
    "soil_not_test": "Typical soil near this location; field soil can vary.",
    "soil_state_fallback": "No local soil label is available; a nearby soil type is shown as a guide.",
    "loading_title": "Preparing your field estimate",
    "loading_steps": ["Estimating your harvest…", "Checking field risks…", "Preparing practical advice…"],
    "report_header": "Your field report",
    "expected_yield": "Expected yield",
    "likely_range": "Likely range",
    "field_total": "Expected production for your field",
    "risk_status": "Field risk",
    "check_inputs": "Check your inputs",
    "normal_uncertainty": "Estimates can change with weather, pests and farm management.",
    "weather_risk_low": "Low rainfall may leave the crop short of water. Check soil moisture and water access.",
    "weather_risk_high": "Excess rain may waterlog the field. Check drainage after heavy rain.",
    "heat_risk": "High temperatures may stress the crop. Watch for wilting during hot periods.",
    "cold_risk": "Cool conditions may slow crop growth. Watch the crop for delayed development.",
    "humidity_risk": "Humid conditions can favor some crop diseases. Inspect the crop and seek local advice if symptoms appear.",
    "water_ok": "The rainfall entered for this field does not raise a water-stress warning.",
    "temperature_ok": "The temperature entered does not raise a heat or cold warning.",
    "humidity_ok": "The humidity entered does not raise a disease warning.",
    "yield_risk": "These field conditions may result in a small harvest. Review the risks and actions below.",
    "recommendation_reason": "This option changed the estimate in the field preview. Results can differ on your farm.",
    "field_check_template": "{label} of {value} {unit} is much {direction} than usual for {crop} in {location}. Please check this value.",
    "field_warning_low": "lower",
    "field_warning_high": "higher",
    "ai_fallback": "Standard field guidance is shown below.",
    "ai_number_warning": "Some advice was left out because it included a number that did not match this report.",
    "check_adviser": "Check these suggestions with a local agronomist or the nearest agriculture extension office.",
    "report_disclaimer": "This is a field estimate, not a promise. Weather, pests and farm management can change the harvest. Check major decisions with a local agronomist or the nearest agriculture extension office.",
    "chart_history_yield": "Your expected harvest",
    "chart_climate": "Weather entered for this field",
    "chart_fertilizer": "Fertilizer change preview",
    "chart_pesticide": "Pesticide change preview",
    "chart_contributions": "Inputs that can shift the estimate",
    "chart_scenarios": "Current and changed input preview",
    "chart_distribution": "Possible harvest range",
    "whatif_update": "Updating your field estimate…",
    "whatif_ready": "Preview: {yield} kg/ha · {risk} field risk. The estimate and risk reasons now reflect these inputs.",
    "advice_saved": "Updated field advice saved as a new report.",
    "advice_regenerating": "Preparing updated advice…",
    "action_expected": "Possible change to estimate",
    "action_timing": "When to consider it",
    "input_source_label": "How entered",
    "source_online": "Online weather",
    "source_manual": "Entered by you",
    "source_sensor": "Field sensor",
    "risk_low": "Low",
    "risk_moderate": "Moderate",
    "risk_high": "High",
    "risk_severe": "Severe",
    "risk_watch": "Field risks to watch",
    "field_outlook": "Field outlook",
    "recommended_actions": "Recommended actions",
    "farm_previews": "Farm input previews",
    "what_if": "What if I change…?",
    "planning": "For your planning",
    "field_details": "Your field details",
    "report_brand": "YOUR FIELD COMPANION",
    "report_return": "← Back to field",
    "copy_link": "Copy report link",
    "download_report": "Download PDF",
    "check_input_heading": "Check your inputs",
    "field_preview_status": "Changes show a field preview.",
}

# Central deny-list: used before farmer-facing HTML/PDF and before AI output is saved.
BANNED_TERMS = (
    "model", "training", "percentile", "interval", "calibrat", "approximation",
    "held-out", "held out", "mae", "r2", "records", "samples", "features",
    "shap", "dataset", "source data", "data period", "historical", "observed", "trained",
    "state average", "best observed", "average:", "score/100", "source:",
    "based on", "technical", "statistics", "statistical", "risk score", "tested scenario",
    "regional average", "reference data", "proxy",
)
BANNED_PATTERNS = (
    re.compile(r"\b(?:19|20)\d{2}\s*[-–—]\s*(?:19|20)\d{2}\b", re.I),
    re.compile(r"\b\d[\d,]*(?:\.\d+)?\s+(?:records?|samples?)\b", re.I),
    re.compile(r"\b(?:average|observed)\s*:", re.I),
    re.compile(r"\b(?:state|district|regional|crop and season)\s+average\b", re.I),
    re.compile(r"\b(?:1st|first|90th|99th)\s*[-––]?\s*\d*(?:st|nd|rd|th)?\s*percentile\b", re.I),
)


def contains_banned_text(value: str) -> bool:
    lowered = str(value).casefold()
    return any(term.casefold() in lowered for term in BANNED_TERMS) or any(pattern.search(str(value)) for pattern in BANNED_PATTERNS)


def strip_banned_text(value: str, fallback: str = "") -> str:
    """Drop a full unsafe sentence rather than exposing a partial statistic."""
    safe_sentences = []
    for sentence in re.split(r"(?<=[.!?])\s+", str(value).strip()):
        if sentence and not contains_banned_text(sentence):
            safe_sentences.append(sentence)
    safe = " ".join(safe_sentences)
    return safe if safe else fallback


def sanitize_tree(value, fallback: str = ""):
    """Recursively remove banned text from public JSON/PDF structures."""
    if isinstance(value, str):
        return strip_banned_text(value, fallback)
    if isinstance(value, list):
        return [cleaned for item in value if (cleaned := sanitize_tree(item, fallback)) not in (None, "")]
    if isinstance(value, dict):
        return {key: cleaned for key, item in value.items()
                if (cleaned := sanitize_tree(item, fallback)) not in (None, "")}
    return value
