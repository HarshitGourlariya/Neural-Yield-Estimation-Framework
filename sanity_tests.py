"""Fail-loud agronomic response tests and sensitivity plot generator."""
import joblib
import numpy as np
import pandas as pd
from predict import MODEL, prediction

bundle = joblib.load(MODEL)
crop = "Wheat" if "Wheat" in bundle["refs"] else next(iter(bundle["refs"]))
state = "Punjab" if "Punjab" in bundle["districts_by_state"] else next(iter(bundle["districts_by_state"]))
soil = "Loamy" if "Loamy" in bundle["typical_soil_by_region"].values() else next(iter(set(bundle["typical_soil_by_region"].values())))
base = {"Crop": crop, "State": state, "Season": "Rabi", "Soil Type": soil,
        "Temperature": 23., "Rainfall": 900., "Humidity": 60., "pH": 6.8,
        "Fertilizer": 120., "Pesticide": .2}

worst = {**base, "Rainfall": 1., "Temperature": 45., "Fertilizer": 0., "pH": 4.0,
         "Soil Type": "Aridisols" if "Aridisols" in set(bundle["typical_soil_by_region"].values()) else soil}
worst_y = prediction(bundle, worst)["yield_kg_ha"]
best = {**base, "Rainfall": max(800., bundle["refs"].get(crop, bundle["refs"]["__global__"])["rain_q75"]),
        "Temperature": bundle["refs"].get(crop, bundle["refs"]["__global__"])["temp_median"],
        "Fertilizer": max(150., bundle["refs"].get(crop, bundle["refs"]["__global__"])["fert_q75"]),
        "pH": bundle["refs"].get(crop, bundle["refs"]["__global__"])["ph_median"]}
best_y = prediction(bundle, best)["yield_kg_ha"]
print(f"Worst-case estimate: {worst_y:.1f} kg/ha")
print(f"Best-case estimate: {best_y:.1f} kg/ha")
assert worst_y < 1800, f"Worst case too high: {worst_y:.1f} kg/ha"
assert best_y > worst_y, "Best-case estimate must exceed worst-case estimate"
assert best_y > 500, "Best-case estimate is implausibly low; inspect the trained model and assumptions"

rain = [1, 10, 50, 100, 200, 400, 800, 1200, 2000, 4000, 8000]
rain_y = [prediction(bundle, {**base, "Rainfall": x})["yield_kg_ha"] for x in rain]
assert rain_y[0] < rain_y[4] <= rain_y[6], f"Rainfall response is not sensible: {list(zip(rain,rain_y))}"
fert = [0, 20, 50, 100, 200, 400]
fert_y = [prediction(bundle, {**base, "Fertilizer": x})["yield_kg_ha"] for x in fert]
assert all(b >= a - 1e-6 for a,b in zip(fert_y, fert_y[1:])), f"Fertilizer response decreased: {fert_y}"
temps = [5, 15, 23, 30, 40, 50]
temp_y = [prediction(bundle, {**base, "Temperature": x})["yield_kg_ha"] for x in temps]
assert temp_y[2] >= temp_y[-1], f"Heat stress did not lower prediction: {temp_y}"
print("Rainfall curve:", list(zip(rain, [round(x,1) for x in rain_y])))
print("Fertilizer curve:", list(zip(fert, [round(x,1) for x in fert_y])))
print("Temperature curve:", list(zip(temps, [round(x,1) for x in temp_y])))

def svg_chart(title, xs, ys, xlabel, x_labels=None):
    width, height = 420, 290
    left, right, top, bottom = 58, 18, 34, 58
    max_y = max(ys) * 1.12 or 1
    points = []
    for i, y in enumerate(ys):
        x = left + i * (width-left-right) / max(1, len(ys)-1)
        py = height-bottom - y/max_y*(height-top-bottom)
        points.append((x, py))
    line = " ".join(f"{x:.1f},{y:.1f}" for x,y in points)
    labels = "".join(f'<text x="{x:.1f}" y="{height-bottom+20}" text-anchor="middle" font-size="10">{x_labels[i] if x_labels else xs[i]}</text>' for i,(x,_) in enumerate(points))
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#377b45"/>' for x,y in points)
    return f'<g transform="translate({chart_x},0)"><text x="210" y="20" text-anchor="middle" font-size="15" font-weight="bold">{title}</text><line x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" stroke="#667"/><line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" stroke="#667"/><polyline points="{line}" fill="none" stroke="#377b45" stroke-width="2.5"/>{dots}{labels}<text x="210" y="{height-12}" text-anchor="middle" font-size="11">{xlabel}</text><text x="14" y="145" transform="rotate(-90 14 145)" text-anchor="middle" font-size="11">Predicted yield kg/ha</text></g>'

panels = [("Water response", rain, rain_y, "Rainfall mm/year"),
          ("Fertilizer response", fert, fert_y, "Fertilizer kg/ha"),
          ("Temperature response", temps, temp_y, "Temperature °C")]
body = []
for idx, (title, xs, ys, xlabel) in enumerate(panels):
    chart_x = idx * 430
    body.append(svg_chart(title, xs, ys, xlabel))
svg = '<svg xmlns="http://www.w3.org/2000/svg" width="1290" height="290" viewBox="0 0 1290 290"><rect width="100%" height="100%" fill="#fff"/>' + ''.join(body) + '</svg>'
with open("sensitivity_curves.svg", "w", encoding="utf-8") as f:
    f.write(svg)
print("Saved sensitivity_curves.svg")
