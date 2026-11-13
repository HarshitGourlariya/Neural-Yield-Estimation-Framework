"""Command line crop yield estimate and 90% conformal residual interval."""
import argparse
from pathlib import Path

import joblib
import pandas as pd
import numpy as np

ROOT = Path(__file__).parent
MODEL = ROOT / "models" / "yield_model.joblib"


def prediction(bundle, values):
    row = pd.DataFrame([values])[bundle["features"]]
    from train import stress_factors
    limits = bundle["refs"].get(str(values["Crop"]), bundle["refs"]["__global__"])
    model_row = row.copy()
    model_row["Temperature"] = model_row["Temperature"].astype(float)
    model_row.at[0, "Temperature"] = np.clip(float(values["Temperature"]), limits["temp_q01"], limits["temp_q99"])
    raw = float(np.expm1(bundle["model"].predict(model_row)[0]))
    f = stress_factors(values, bundle["refs"])
    stress = float(np.prod([f[k] for k in ("water", "temperature", "fertilizer", "soil_ph", "soil_class", "pesticide")]))
    ceiling = bundle["physical_ceiling"]
    point = float(np.clip(raw * stress, 0, ceiling))
    q = bundle["conformal_q90"]
    return {"yield_kg_ha": point, "interval_90_kg_ha": [max(0., point-q), min(ceiling, point+q)],
            "stress_factors": f, "pre_stress_model_estimate": raw}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--crop", required=True); p.add_argument("--state", required=True)
    p.add_argument("--season", required=True); p.add_argument("--soil-type", required=True)
    p.add_argument("--temperature", type=float, required=True); p.add_argument("--rainfall", type=float, required=True)
    p.add_argument("--humidity", type=float, required=True); p.add_argument("--ph", type=float, required=True)
    p.add_argument("--fertilizer", type=float, required=True, help="Total fertilizer applied in kg/ha")
    p.add_argument("--pesticide", type=float, required=True, help="Pesticide applied in kg/ha")
    a = vars(p.parse_args()); a["Crop"] = a.pop("crop"); a["State"] = a.pop("state"); a["Season"] = a.pop("season")
    a["Temperature"] = a.pop("temperature"); a["Rainfall"] = a.pop("rainfall")
    a["Humidity"] = a.pop("humidity"); a["pH"] = a.pop("ph"); a["Fertilizer"] = a.pop("fertilizer")
    a["Pesticide"] = a.pop("pesticide"); a["Soil Type"] = a.pop("soil_type")
    if not MODEL.exists(): p.error("Model missing. Run `python train.py` first.")
    print(pd.Series(prediction(joblib.load(MODEL), a)).to_json(indent=2))


if __name__ == "__main__": main()
