"""Optional, server-only Gemini (or Anthropic compatibility) narrative."""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import deque
from pathlib import Path

from dotenv import load_dotenv
from weather_service import _weather_session
from user_messages import contains_banned_text, strip_banned_text

load_dotenv(Path(__file__).resolve().parent / ".env")

API_URL = "https://api.anthropic.com/v1/messages"
TIMEOUT_SECONDS = 30
MAX_TOKENS = 1200
MODEL_NAME = os.getenv("MODEL_NAME", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_rate_lock = threading.Lock()
_ai_timestamps: deque[float] = deque()
_NUMBER = re.compile(r"(?<![\w])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?(?!\w)")
NARRATIVE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "key_findings", "risk_explanations", "recommendations", "warnings", "next_steps"],
    "properties": {
        "summary": {"type": "string"}, "key_findings": {"type": "array", "items": {"type": "string"}},
        "risk_explanations": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "next_steps": {"type": "array", "items": {"type": "string"}},
        "recommendations": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["title", "action", "reason", "expected_impact", "priority", "timing"],
            "properties": {**{key: {"type": "string"} for key in ("title", "action", "reason", "expected_impact", "timing")},
                           "priority": {"type": "string", "enum": ["High", "Medium", "Low"]}}}},
    },
}

SYSTEM_PROMPT = """You are GreenYield's careful crop advisory writer. Return only strict JSON with exactly these keys:
summary, key_findings[], risk_explanations[], recommendations[], warnings[], next_steps[].
Each recommendation must have exactly title, action, reason, expected_impact, priority, timing.
Use only supplied field inputs and estimate; never invent figures, scientific claims, prices, or yields.
Write simple short farmer-friendly sentences in the requested language. Recommend only actions a farmer controls. Do not mention models, datasets, sources, comparisons, years, records, scores, statistical methods, or accuracy. Explain uncertainty plainly and never promise an outcome. Do not prescribe a pesticide or pesticide dose without a confirmed pest/disease diagnosis. For high soil pH, recommend a soil test and local advice; do not invent amendment amounts or say gypsum is a general pH-lowering treatment. End summary or next_steps with a reminder to check decisions with a local agronomist or agriculture extension office. Keep every number anchored to a supplied number. JSON only."""


def validate_narrative(value) -> bool:
    """Validate the parsed response against NARRATIVE_SCHEMA's fixed fields/types."""
    if not _matches_schema(value, NARRATIVE_SCHEMA):
        return False
    closing = " ".join([value["summary"], *value["next_steps"]]).casefold()
    return "agronomist" in closing or "extension office" in closing or "कृषि विशेषज्ञ" in closing or "कृषि विस्तार" in closing


def _matches_schema(value, schema):
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict) or not set(schema.get("required", ())) <= set(value):
            return False
        if schema.get("additionalProperties") is False and set(value) - set(schema.get("properties", {})):
            return False
        return all(key not in value or _matches_schema(value[key], child)
                   for key, child in schema.get("properties", {}).items())
    if expected == "array":
        return isinstance(value, list) and all(_matches_schema(item, schema["items"]) for item in value)
    if expected == "string":
        return isinstance(value, str) and ("enum" not in schema or value in schema["enum"])
    return False


def _numbers(value):
    found = []
    if isinstance(value, bool) or value is None:
        return found
    if isinstance(value, (int, float)):
        found.append(float(value))
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(_numbers(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_numbers(item))
    return found


def _known_number(number: float, allowed: list[float]) -> bool:
    for candidate in allowed:
        if abs(number - candidate) < 1e-8:
            return True
        # A writer may round a provided value to the nearest whole/1dp/2dp.
        for digits in (0, 1, 2):
            if abs(number - round(candidate, digits)) < 1e-8:
                return True
    return False


def _clean_text(text: str, allowed: list[float]):
    safe, removed = [], False
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        numbers = [float(token.replace(",", "").rstrip("%")) for token in _NUMBER.findall(sentence)]
        if any(not _known_number(number, allowed) for number in numbers):
            removed = True
        elif sentence.strip():
            safe.append(sentence.strip())
    return " ".join(safe), removed


def guardrail_narrative(narrative: dict, source_data: dict) -> tuple[dict, bool]:
    allowed = _numbers(source_data)
    cleaned = dict(narrative)
    removed = False
    for key in ("summary", "key_findings", "risk_explanations", "warnings", "next_steps"):
        if key == "summary":
            cleaned[key], bad = _clean_text(cleaned[key], allowed)
            safe = strip_banned_text(cleaned[key])
            bad |= safe != cleaned[key]
            cleaned[key] = safe
            removed |= bad
        else:
            kept = []
            for item in cleaned[key]:
                safe, bad = _clean_text(item, allowed)
                filtered = strip_banned_text(safe)
                bad |= filtered != safe
                safe = filtered
                removed |= bad
                if safe:
                    kept.append(safe)
            cleaned[key] = kept
    recommendations = []
    for rec in cleaned["recommendations"]:
        clean_rec = {}
        valid = True
        for key, text in rec.items():
            safe, bad = _clean_text(text, allowed)
            filtered = strip_banned_text(safe)
            bad |= filtered != safe
            safe = filtered
            removed |= bad
            if bad and not safe:
                valid = False
            clean_rec[key] = safe
        if valid:
            recommendations.append(clean_rec)
    cleaned["recommendations"] = recommendations
    if removed:
        cleaned["warnings"] = list(cleaned["warnings"]) + ["Some AI sentences were omitted because they included numbers not present in the report data."]
    return cleaned, removed


def fallback_narrative(analysis: dict, language: str = "English") -> dict:
    y = analysis["yield"]
    risk = analysis["risk"]
    loc = analysis["location"]
    effect = analysis["what_if"]["recommended_scenario"]
    is_hindi = language.casefold() == "hindi"
    if is_hindi:
        summary = (f"अनुमानित उपज लगभग {y['yield_kg_ha']:.1f} kg/ha है; आपके खेत का अनुमानित उत्पादन "
                   f"{y['total_tonnes']:.3f} टन है। जोखिम स्तर {risk['status']} है। "
                   "स्थानीय कृषि विशेषज्ञ से सलाह लें।")
        findings = [f"संभावित उपज लगभग {y['yield_kg_ha']:.1f} kg/ha है।"]
        precautions = ["बारिश और तापमान के बदलाव पर खेत की निगरानी करें।"]
        next_steps = ["खाद की मात्रा बदलने से पहले स्थानीय कृषि विशेषज्ञ या नज़दीकी KVK से सलाह लें।",
                      "ये सुझाव स्थानीय कृषि विशेषज्ञ या नज़दीकी KVK से जाँचें।"]
    else:
        summary = (f"Expected yield is about {y['yield_kg_ha']:.1f} kg/ha, or {y['total_tonnes']:.3f} tonnes for this field. "
                   f"Field risk is {risk['status']}. Check major decisions with a local agronomist.")
        findings = [f"Expected yield is about {y['yield_kg_ha']:.1f} kg/ha."]
        precautions = ["Watch the field for weather and disease signs that match the risk factors below."]
        next_steps = ["Check fertilizer plans against local crop guidance before changing application.",
                      "Check these suggestions with a local agronomist or the nearest agriculture extension office."]
    risk_lines = [f"{x['title']}: {x['explanation']}" for x in risk["factors"] if x["score"] > 0]
    recommendations = []
    if effect.get("change_kg_ha", 0) > 0:
        recommendations.append({"title": f"Review {effect['title'].lower()}",
            "action": f"Compare the current application plan with a field preview of {effect['input_total_kg']:.2f} kg total.",
            "reason": "This field preview showed a higher estimate; results can differ on your farm.",
            "expected_impact": f"Possible change of {effect['change_kg_ha']:+.1f} kg/ha.",
            "priority": "Medium", "timing": "Before the next application decision"})
    recommendations.append({"title": "Check the local field conditions",
        "action": "Use the risk factors as prompts to inspect waterlogging, moisture stress, and crop health.",
        "reason": "Conditions can vary across a field.",
        "expected_impact": "No yield change is quantified for inspection alone.",
        "priority": "High" if risk["status"] in {"High", "Severe"} else "Medium", "timing": "During the current crop season"})
    warnings = []
    return {"summary": summary, "key_findings": findings,
            "risk_explanations": risk_lines + precautions, "recommendations": recommendations,
            "warnings": warnings, "next_steps": next_steps}


def _allow_rate():
    now = time.monotonic()
    with _rate_lock:
        while _ai_timestamps and now - _ai_timestamps[0] > 60:
            _ai_timestamps.popleft()
        if len(_ai_timestamps) >= 10:
            return False
        _ai_timestamps.append(now)
        return True


def _extract_json(response):
    content = response.get("content", [])
    text = "\n".join(item.get("text", "") for item in content if item.get("type") == "text")
    if not text:
        raise ValueError("No text returned")
    # tolerate only a surrounding markdown fence; JSON itself remains strict.
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    return json.loads(text)


def _extract_gemini_json(response):
    candidates = response.get("candidates", [])
    parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
    text = "\n".join(part.get("text", "") for part in parts if isinstance(part, dict))
    if not text:
        raise ValueError("Gemini returned no summary text")
    return json.loads(text)


def _generate_gemini(analysis: dict, language: str, context: dict, api_key: str, model: str):
    fallback = fallback_narrative(analysis, language)
    if not _allow_rate():
        return fallback, False, "Gemini summary unavailable; showing standard field guidance.", 0
    attempts, repair = 0, ""
    deadline = time.monotonic() + TIMEOUT_SECONDS
    url = GEMINI_URL.format(model=model) + "?key=" + api_key
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        attempts += 1
        body = {"systemInstruction": {"parts": [{"text": SYSTEM_PROMPT + ("\n" + repair if repair else "")}]},
                "contents": [{"role": "user", "parts": [{"text": json.dumps(
                    {"language": language, "report_data": context}, ensure_ascii=False)}]}],
                "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": NARRATIVE_SCHEMA,
                                     "temperature": 0.2, "maxOutputTokens": MAX_TOKENS}}
        try:
            response = _weather_session.post(url, headers={"Content-Type": "application/json"}, json=body,
                                             timeout=max(.1, remaining))
            response.raise_for_status()
            narrative = _extract_gemini_json(response.json())
            if not validate_narrative(narrative):
                raise ValueError("Gemini output did not match the report format")
            narrative, removed = guardrail_narrative(narrative, context)
            if not validate_narrative(narrative) or (removed and not narrative["summary"]):
                raise ValueError("Gemini output failed report safety checks")
            return narrative, True, ("Gemini summary included; unsupported numeric sentences were removed." if removed else ""), attempts
        except Exception as exc:
            logging.getLogger(__name__).warning("Gemini narrative attempt failed: %s", type(exc).__name__)
            if attempt == 0:
                repair = "Return corrected strict JSON matching the schema and use only numbers in report_data."
                continue
            return fallback, False, "Gemini summary unavailable; showing standard field guidance.", attempts
    return fallback, False, "Gemini summary unavailable; showing standard field guidance.", attempts


def generate_narrative(analysis: dict, language: str = "English") -> tuple[dict, bool, str, int]:
    """Make one report-level AI operation (one schema repair retry at most)."""
    fallback = fallback_narrative(analysis, language)
    context = {"crop": analysis["location"]["crop"], "season": analysis["location"]["season"],
               "location": {"state": analysis["location"]["state"], "district": analysis["location"]["district"]},
               "field_inputs": [{"name": item["name"], "value": item["value"]}
                                for item in analysis["data_quality"]["input_sources"]],
               "expected_yield_kg_ha": analysis["yield"]["yield_kg_ha"],
               "likely_yield_range_kg_ha": analysis["yield"]["interval_90_kg_ha"],
               "expected_total_field_tonnes": analysis["yield"]["total_tonnes"],
               "field_risk": {"status": analysis["risk"]["status"],
                              "drivers": [{"title": item["title"], "level": item.get("level"),
                                          "explanation": item.get("explanation")}
                                         for item in analysis["risk"]["factors"] if item.get("score", 0) > 0]}}
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key:
        return _generate_gemini(analysis, language, context, gemini_key,
                                os.getenv("GEMINI_MODEL", GEMINI_MODEL).strip() or GEMINI_MODEL)
    api_key, model = os.getenv("ANTHROPIC_API_KEY", "").strip(), os.getenv("MODEL_NAME", MODEL_NAME).strip()
    if not api_key or not model:
        return fallback, False, "AI summary unavailable, showing standard analysis (configure ANTHROPIC_API_KEY and MODEL_NAME).", 0
    if not _allow_rate():
        return fallback, False, "AI summary unavailable, showing standard analysis (AI request rate limit reached).", 0
    attempts = 0
    repair = ""
    deadline = time.monotonic() + TIMEOUT_SECONDS
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        attempts += 1
        body = {"model": model, "max_tokens": MAX_TOKENS,
                "system": SYSTEM_PROMPT + ("\n" + repair if repair else ""),
                "messages": [{"role": "user", "content": json.dumps({"language": language, "report_data": context}, ensure_ascii=False)}]}
        try:
            response = _weather_session.post(API_URL, headers={"x-api-key": api_key,
                "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json=body, timeout=max(.1, remaining))
            response.raise_for_status()
            narrative = _extract_json(response.json())
            if not validate_narrative(narrative):
                raise ValueError("The response did not match the required JSON fields and types")
            narrative, removed = guardrail_narrative(narrative, context)
            if not validate_narrative(narrative):
                raise ValueError("The response failed the numeric guardrail")
            if removed and not narrative["summary"]:
                raise ValueError("The summary contained unsupported numbers")
            return narrative, True, ("AI summary included; unsupported numeric sentences were removed." if removed else ""), attempts
        except Exception as exc:
            logging.getLogger(__name__).warning("AI narrative attempt failed: %s", type(exc).__name__)
            if attempt == 0:
                repair = ("Your previous response was invalid or used unsupported numbers. Return corrected strict JSON only, "
                          "matching the schema and using only numbers supplied in report_data.")
                continue
            return fallback, False, f"AI summary unavailable, showing standard analysis ({type(exc).__name__}).", attempts
    return fallback, False, "AI summary unavailable, showing standard analysis.", attempts
