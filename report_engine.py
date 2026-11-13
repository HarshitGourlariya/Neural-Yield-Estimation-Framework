"""Deterministic report analysis built only from the trained model and source data."""
from __future__ import annotations

from functools import lru_cache
import math
from statistics import NormalDist

import numpy as np
import pandas as pd

from predict import prediction
from train import NUM, prepare, normalize_state


@lru_cache(maxsize=1)
def historical_data():
    return prepare()


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _series_rows(df, values):
    crop_rows = df[(df.Crop == values["Crop"]) & (df.Season == values["Season"])]
    district_rows = crop_rows[(crop_rows.District.str.casefold() == str(values["District"]).casefold()) &
                              (crop_rows.State == normalize_state(values["State"]))]
    state_rows = crop_rows[crop_rows.State == normalize_state(values["State"])]
    return crop_rows, district_rows, state_rows


def _q(series, q, default):
    series = pd.to_numeric(series, errors="coerce").dropna()
    return float(series.quantile(q)) if not series.empty else float(default)


def _predict(bundle, model_values):
    return prediction(bundle, model_values)


INPUT_WARNING_FIELDS = {
    "temperature": ("Temperature", "Temperature", "°C"),
    "humidity": ("Humidity", "Humidity", "%"),
    "rainfall": ("Rainfall", "Rainfall", "mm/year"),
    "ph": ("pH", "Soil pH", ""),
    "fertilizer_kg": ("Fertilizer", "Fertilizer", "kg"),
    "pesticide_kg": ("Pesticide", "Pesticide", "kg"),
}


def input_warning(state: str, district: str, crop: str, season: str, field: str,
                  value: float, area_acres: float) -> str:
    """Flag clear outliers against comparable entries, allowing for rounding/noise."""
    if field not in INPUT_WARNING_FIELDS:
        return ""
    feature, label, unit = INPUT_WARNING_FIELDS[field]
    df = historical_data()
    crop_rows, district_rows, state_rows = _series_rows(df, {"Crop": crop, "Season": season,
        "District": district, "State": normalize_state(state)})
    if not district_rows.empty:
        reference, mention_location = district_rows, f" for {crop} in {district}"
    elif not state_rows.empty:
        reference, mention_location = state_rows, f" for {crop} in {state}"
    else:
        reference, mention_location = crop_rows, f" for {crop}"
    if reference.empty:
        return ""
    observed = pd.to_numeric(reference[feature], errors="coerce").dropna()
    if observed.empty:
        return ""
    factor = 1.0
    if field in ("fertilizer_kg", "pesticide_kg"):
        factor = max(float(area_acres), .0001) * .404685642
    entered = float(value) / factor
    low, high = float(observed.quantile(.01)), float(observed.quantile(.99))
    spread = float(observed.quantile(.75) - observed.quantile(.25))
    # Tiny differences (for example 75.7% vs a local historical max of 75.64%)
    # are not useful warnings. pH also needs a wider tolerance because source
    # records are rounded and often repeat a single district value.
    tolerance = max(.2 if field == "ph" else .5 if field in ("humidity", "temperature")
                    else 5.0 if field == "rainfall" else .1, .1 * spread)
    direction = "higher" if entered > high + tolerance else "lower" if entered < low - tolerance else ""
    if not direction:
        return ""
    display_value = f"{float(value):g}"
    if field in ("fertilizer_kg", "pesticide_kg"):
        return (f"Entered {label.casefold()} amount ({display_value} {unit}) is {direction} than amounts recorded"
                f"{mention_location} for a similar field area. Please check this value and the field area; this comparison is not a"
                f" {label.casefold()} dose recommendation.")
    return (f"Entered {label.casefold()} ({display_value}{(' ' + unit) if unit else ''}) is outside the range recorded"
            f"{mention_location}. Please check this value and its unit.")


def build_analysis(bundle: dict, raw_inputs: dict, model_values: dict,
                   area_acres: float, soil: str, input_sources: dict | None = None) -> dict:
    """Build deterministic comparisons, risk, local perturbations and chart data."""
    df = historical_data()
    crop_rows, district_rows, state_rows = _series_rows(df, model_values)
    comparison_rows = district_rows if not district_rows.empty else state_rows
    comparison_scope = "district" if not district_rows.empty else "state" if not state_rows.empty else "crop and season"
    climate_rows = district_rows if not district_rows.empty else state_rows
    if comparison_rows.empty:
        comparison_rows = crop_rows
    if climate_rows.empty:
        climate_rows = crop_rows
    crop_inputs = crop_rows if len(crop_rows) >= 20 else df[df.Crop == model_values["Crop"]]
    if crop_inputs.empty:
        crop_inputs = df

    predicted = _predict(bundle, model_values)
    point = float(predicted["yield_kg_ha"])
    low, high = map(float, predicted["interval_90_kg_ha"])
    area_ha = float(area_acres) * 0.404685642
    output = {
        "yield_kg_ha": round(point, 1), "interval_90_kg_ha": [round(low, 1), round(high, 1)],
        "area_acres": round(float(area_acres), 2), "area_hectares": round(area_ha, 4),
        "total_tonnes": round(point * area_ha / 1000, 3),
        "total_tonnes_interval": [round(low * area_ha / 1000, 3), round(high * area_ha / 1000, 3)],
        "stress_factors": {k: round(float(v), 4) for k, v in predicted["stress_factors"].items()},
    }

    yields = pd.to_numeric(comparison_rows.Yield, errors="coerce").dropna()
    regional_mean = float(yields.mean()) if not yields.empty else point
    regional_best = float(yields.max()) if not yields.empty else point
    years = comparison_rows.Year.astype(int)
    comparison = {"scope": comparison_scope, "mean_yield_kg_ha": round(regional_mean, 1),
                  "best_observed_yield_kg_ha": round(regional_best, 1), "records": int(len(yields)),
                  "years": [int(years.min()), int(years.max())] if len(years) else []}

    climate_trend = climate_rows.groupby("Year", as_index=False)[["Temperature", "Rainfall"]].mean().sort_values("Year")
    climate_average = float(climate_rows.Rainfall.mean()) if len(climate_rows) else float(crop_inputs.Rainfall.mean())
    climate_scope = "district" if not district_rows.empty else "state" if not state_rows.empty else "crop and season"
    history = comparison_rows.groupby("Year").Yield.mean().sort_index()
    historical_chart = {"years": [int(x) for x in history.index],
                        "yield_kg_ha": [round(float(x), 1) for x in history.values]}
    climate_chart = {"years": [int(x) for x in climate_trend.Year.tolist()],
                     "temperature_c": [round(float(x), 1) for x in climate_trend.Temperature.tolist()],
                     "rainfall_mm": [round(float(x), 1) for x in climate_trend.Rainfall.tolist()]}

    # Local contributions are counterfactual predictions with one field set to
    # its crop/season input median. They are model sensitivity, not causal SHAP.
    contributions = []
    for name in NUM:
        reference = _q(crop_inputs[name], .5, model_values[name])
        counterfactual = dict(model_values)
        counterfactual[name] = reference
        ref_yield = float(_predict(bundle, counterfactual)["yield_kg_ha"])
        contributions.append({"feature": name, "value": round(float(model_values[name]), 3),
                              "reference": round(reference, 3),
                              "delta_from_reference_kg_ha": round(point - ref_yield, 1)})
    for name, frame_name in (("State", "State"), ("Crop", "Crop"), ("Season", "Season"), ("Soil Type", "Soil Type")):
        modes = crop_inputs[frame_name].mode(dropna=True)
        reference = str(modes.iloc[0]) if len(modes) else str(model_values[name])
        counterfactual = dict(model_values)
        counterfactual[name] = reference
        ref_yield = float(_predict(bundle, counterfactual)["yield_kg_ha"])
        contributions.append({"feature": name, "value": str(model_values[name]), "reference": reference,
                              "delta_from_reference_kg_ha": round(point - ref_yield, 1)})

    # Scenario curves stay inside observed crop/season input ranges.
    scenarios = {}
    best_scenario = {"yield_kg_ha": point, "title": "Current inputs", "change_kg_ha": 0.0}
    for name, form_key, user_name in (("Fertilizer", "fertilizer_kg", "Fertilizer"),
                                      ("Pesticide", "pesticide_kg", "Pesticide"),
                                      ("pH", "ph", "Soil pH"),
                                      ("Rainfall", "rainfall", "Rainfall"),
                                      ("Temperature", "temperature", "Temperature"),
                                      ("Humidity", "humidity", "Humidity")):
        per_hectare = pd.to_numeric(crop_inputs[name], errors="coerce").dropna()
        if per_hectare.empty:
            continue
        ha_points = sorted(set(round(_q(per_hectare, q, model_values[name]), 3)
                               for q in (.05, .25, .5, .75, .95)))
        curve = []
        for candidate in ha_points:
            changed = dict(model_values)
            changed[name] = candidate
            estimate = float(_predict(bundle, changed)["yield_kg_ha"])
            change = estimate - point
            is_application = name in ("Fertilizer", "Pesticide")
            candidate_value = candidate * area_ha if is_application else candidate
            curve.append({"input_value": round(candidate_value, 2 if is_application else 1),
                          "input_total_kg": round(candidate_value, 2) if is_application else None,
                          "input_kg_ha": candidate if is_application else None,
                          "yield_kg_ha": round(estimate, 1),
                          "change_kg_ha": round(change, 1)})
            if name == "Fertilizer" and change > best_scenario["change_kg_ha"] and candidate_value != _safe_float(raw_inputs.get(form_key)):
                best_scenario = {"yield_kg_ha": round(estimate, 1), "title": f"{user_name} scenario",
                                 "change_kg_ha": round(change, 1), "input_total_kg": round(candidate_value, 2),
                                 "input_kg_ha": candidate, "control": name}
        scenarios[user_name.casefold()] = {"label": user_name,
            "unit": "kg total for field" if is_application else {"pH": "pH", "Rainfall": "mm/year",
                "Temperature": "°C", "Humidity": "%"}[name],
            "current_value": round(_safe_float(raw_inputs.get(form_key)), 2 if is_application else 1),
            "current_total_kg": round(_safe_float(raw_inputs.get(form_key)), 2) if is_application else None,
            "current_kg_ha": round(float(model_values[name]), 3) if is_application else None,
            "curve": curve}

    # Range warnings use input percentiles only (never yield statistics).
    range_warnings = []
    input_ranges = {}
    for name in NUM:
        lo, hi = _q(crop_inputs[name], .01, model_values[name]), _q(crop_inputs[name], .99, model_values[name])
        current = float(model_values[name])
        input_ranges[name] = [round(lo, 3), round(hi, 3)]
        if current < lo or current > hi:
            range_warnings.append({"feature": name, "value": round(current, 3),
                                   "training_range_1_99": input_ranges[name]})

    # The interval supplies a 90% central range; use its width to approximate
    # a normal distribution for an interpretable below-threshold probability.
    failure_threshold = regional_mean * .5
    sigma = max((high - low) / (2 * 1.644854), 1.0)
    p_below = float(NormalDist(mu=point, sigma=sigma).cdf(failure_threshold))
    rain_ratio = float(model_values["Rainfall"]) / max(climate_average, 1.0)
    temp_hi = _q(crop_inputs.Temperature, .9, model_values["Temperature"])
    temp_lo = _q(crop_inputs.Temperature, .1, model_values["Temperature"])
    humidity = float(model_values["Humidity"])
    factors = []
    yield_points = min(40.0, 40.0 * p_below)
    factors.append({"title": "Yield below half the regional average", "value": round(p_below * 100, 1),
                    "unit": "% estimated probability", "score": round(yield_points, 1),
                    "level": "High" if p_below >= .5 else "Moderate" if p_below >= .2 else "Low",
                    "explanation": f"The interval-based estimated chance of yield below {failure_threshold:.1f} kg/ha."})
    if rain_ratio < .8:
        rain_points = min(25.0, 25.0 * (1 - rain_ratio) / .8)
        rain_level = "High" if rain_ratio < .4 else "Moderate"
        rain_text = "Rainfall is below the historical location average."
    elif rain_ratio > 1.5:
        rain_points = min(25.0, 25.0 * (rain_ratio - 1.5) / 1.5 + 8)
        rain_level = "High" if rain_ratio > 2.0 else "Moderate"
        rain_text = "Rainfall is above the historical location average; excess water can increase waterlogging risk."
    else:
        rain_points, rain_level = 0.0, "Low"
        rain_text = "Rainfall is within the broad historical location comparison band."
    factors.append({"title": "Rainfall comparison", "value": round(rain_ratio * 100, 1),
                    "unit": f"% of {climate_scope} historical average", "reference_mm": round(climate_average, 1),
                    "score": round(rain_points, 1), "level": rain_level,
                    "explanation": f"{rain_text} Reference: {climate_scope} mean over {int(climate_rows.Year.min())}–{int(climate_rows.Year.max())}.",
                    "historical_average_mm": round(climate_average, 1)})
    if model_values["Temperature"] > temp_hi:
        heat_points = min(20.0, 20.0 * (model_values["Temperature"] - temp_hi) / max(temp_hi - temp_lo, 1))
        heat_level = "High" if model_values["Temperature"] > temp_hi + 5 else "Moderate"
        heat_text = "Entered annual temperature is above the crop/season training 90th percentile."
    elif model_values["Temperature"] < temp_lo:
        heat_points = min(20.0, 20.0 * (temp_lo - model_values["Temperature"]) / max(temp_hi - temp_lo, 1))
        heat_level = "Moderate"
        heat_text = "Entered annual temperature is below the crop/season training 10th percentile."
    else:
        heat_points, heat_level = 0.0, "Low"
        heat_text = "Entered annual temperature is within the crop/season training 10th–90th percentile band."
    factors.append({"title": "Temperature stress screen", "value": round(float(model_values["Temperature"]), 1),
                    "unit": "°C", "reference_band_c": [round(temp_lo, 1), round(temp_hi, 1)],
                    "score": round(heat_points, 1), "level": heat_level, "explanation": heat_text})
    humidity_points = min(10.0, max(0.0, (humidity - 80.0) / 2.0))
    factors.append({"title": "Humidity and disease screen", "value": round(humidity, 1), "unit": "% RH",
                    "score": round(humidity_points, 1), "level": "Moderate" if humidity_points >= 5 else "Low",
                    "explanation": "High humidity can favor some crop diseases; this is a general screening rule, not a disease diagnosis."})
    mismatch_points = min(15.0, 3.0 * len(range_warnings))
    factors.append({"title": "Inputs outside training range", "value": len(range_warnings), "unit": "features",
                    "score": mismatch_points, "level": "Moderate" if range_warnings else "Low",
                    "explanation": "Predictions are less certain for inputs outside the crop-specific 1st–99th percentile range."})
    risk_score = round(min(100.0, sum(x["score"] for x in factors)), 1)
    risk_status = "Severe" if risk_score >= 75 else "High" if risk_score >= 50 else "Moderate" if risk_score >= 25 else "Low"
    risk = {"score": risk_score, "status": risk_status, "factors": factors,
            "threshold_yield_kg_ha": round(failure_threshold, 1),
            "probability_below_threshold_pct": round(p_below * 100, 1),
            "probability_method": "Normal approximation using the model's 90% interval width; not a calibrated crop-failure frequency."}

    x_values = np.linspace(max(0, point - 3 * sigma), point + 3 * sigma, 25)
    distribution = {"yield_kg_ha": [round(float(x), 1) for x in x_values],
                    "relative_density": [round(float(np.exp(-.5 * ((x - point) / sigma) ** 2)), 5) for x in x_values]}
    scenarios_chart = {"labels": ["Current", best_scenario["title"]],
                       "yield_kg_ha": [round(point, 1), round(float(best_scenario["yield_kg_ha"]), 1)]}
    sources = input_sources or {}
    input_summary = []
    labels = {"temperature": "Annual mean temperature", "humidity": "Annual mean humidity", "rainfall": "Annual rainfall",
              "ph": "Soil pH", "fertilizer_kg": "Fertilizer applied", "pesticide_kg": "Pesticide applied",
              "soil_moisture": "Soil moisture", "light_lux": "Light intensity", "area_acres": "Field area"}
    for key, label in labels.items():
        input_summary.append({"name": label, "value": raw_inputs.get(key),
                              "source": sources.get(key, "manual"), "field": key})
    quality = {"input_sources": input_summary, "outside_training_range": range_warnings,
               "limitations": ["Model weather/yield records end in 2017 and are annual district observations.",
                   "Soil pH and soil class are district-level references, not a field soil test.",
                   "Soil moisture and light are collected by the form but are not trained model features.",
                   "Irrigation, sowing date, seed variety, and sensor time-series were not present in the training data.",
                   "The 90% interval uses held-out-region residual calibration and may not cover new conditions as expected."]}
    return {"location": {"state": model_values["State"], "district": model_values["District"],
                          "crop": model_values["Crop"], "season": model_values["Season"], "soil_type": soil},
        "yield": output, "comparison": comparison, "risk": risk,
        "what_if": {"scenarios": scenarios, "recommended_scenario": best_scenario,
                    "not_modeled_controls": ["irrigation", "sowing date", "seed variety"]},
        "contributions": contributions, "data_quality": quality,
        "unusual_inputs": [warning for field, input_value in raw_inputs.items()
            if field in INPUT_WARNING_FIELDS and input_value is not None and str(input_value) != ""
            for warning in [input_warning(model_values["State"], model_values["District"], model_values["Crop"],
                model_values["Season"], field, float(input_value), area_acres)] if warning],
        "charts": {"historical_yield": historical_chart, "climate_trend": climate_chart,
                   "local_contributions": contributions, "scenarios": scenarios_chart,
                   "yield_distribution": distribution},
        "input_ranges": input_ranges,
        "climate_reference": {"scope": climate_scope, "rainfall_mean_mm": round(climate_average, 1),
                               "years": [int(climate_rows.Year.min()), int(climate_rows.Year.max())]},
        "context": {"regional_average_kg_ha": round(regional_mean, 1), "best_historical_kg_ha": round(regional_best, 1)}}


def update_model_inputs(bundle, original_model_values, raw_inputs, changes):
    """Apply validated, user-selected field values to an unsaved preview."""
    supported = {"fertilizer_kg", "pesticide_kg", "ph", "temperature", "humidity", "rainfall"}
    unsupported = set(changes) - supported
    if unsupported:
        raise ValueError("This preview does not support one or more of those fields.")
    model_values = dict(original_model_values)
    updated = dict(raw_inputs)
    area_ha = max(0.0001, _safe_float(updated.get("area_acres"), 1) * .404685642)
    applied = {}
    for feature, key in (("Fertilizer", "fertilizer_kg"), ("Pesticide", "pesticide_kg")):
        if key not in changes:
            continue
        amount = float(changes[key])
        if not math.isfinite(amount) or amount < 0 or amount > 1_000_000:
            raise ValueError(f"{key} must be between 0 and 1,000,000 kg total.")
        per_ha = amount / area_ha
        updated[key] = amount
        model_values[feature] = per_ha
        applied[key] = amount
    for key, feature, minimum, maximum, label in (
        ("ph", "pH", 0.1, 14.0, "Soil pH"), ("temperature", "Temperature", -10.0, 55.0, "Temperature"),
        ("humidity", "Humidity", 0.0, 100.0, "Humidity"), ("rainfall", "Rainfall", 0.0, 12000.0, "Rainfall")):
        if key not in changes:
            continue
        value = float(changes[key])
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"{label} must be between {minimum:g} and {maximum:g}.")
        updated[key] = value
        model_values[feature] = value
        applied[key] = value
    return model_values, updated, applied
