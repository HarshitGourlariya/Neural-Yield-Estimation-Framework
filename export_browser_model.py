"""Export the fitted sklearn histogram model into a small browser JSON format."""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parent
MODEL = ROOT / "models" / "yield_model.joblib"
DEST = ROOT / "static" / "model" / "yield-model.json"


def export_model(source: Path = MODEL, dest: Path = DEST) -> dict:
    bundle = joblib.load(source)
    pipeline = bundle["model"]
    outer = pipeline.named_steps["prep"]
    booster = pipeline.named_steps["reg"]
    numeric = list(bundle["features"][:6])
    categorical = list(bundle["features"][6:])
    cat_imputer = outer.named_transformers_["cat"].named_steps["impute"]
    cat_encoder = outer.named_transformers_["cat"].named_steps["ordinal"]
    numeric_imputer = outer.named_transformers_["num"]
    categories = {}
    category_fallback = {}
    for i, name in enumerate(categorical):
        categories[name] = [str(v) for v in cat_encoder.categories_[i]]
        fallback = str(cat_imputer.statistics_[i])
        category_fallback[name] = categories[name].index(fallback) if fallback in categories[name] else 0

    trees = []
    for (predictor,) in booster._predictors:
        nodes = predictor.nodes
        packed = []
        for node in nodes:
            packed.append([
                int(node["left"]), int(node["right"]), int(node["feature_idx"]),
                int(node["bin_threshold"]), int(node["is_leaf"]), int(node["is_categorical"]),
                int(node["bitset_idx"]), float(node["value"]), int(node["missing_go_to_left"]),
            ])
        trees.append({"nodes": packed,
                      "category_bitsets": predictor.binned_left_cat_bitsets.astype(np.uint32).tolist()})

    model = {
        "format_version": 1,
        "features": {"numeric": numeric, "categorical": categorical,
                     "numeric_fallback": [float(x) for x in numeric_imputer.statistics_],
                     "category_values": categories, "category_fallback": category_fallback},
        # The HGB internal preprocessor puts categorical columns first.
        "bins": {"numeric_thresholds": [x.tolist() for x in booster._bin_mapper.bin_thresholds_[len(categorical):]],
                 "missing_bin": int(booster._bin_mapper.missing_values_bin_idx_)},
        "booster": {"baseline": float(booster._baseline_prediction.ravel()[0]), "trees": trees},
        "refs": bundle["refs"], "physical_ceiling": float(bundle["physical_ceiling"]),
        "conformal_q90": float(bundle["conformal_q90"]),
        "districts_by_state": bundle["districts_by_state"],
        "typical_soil_by_region": bundle["typical_soil_by_region"],
        "seasons_by_crop": {},
    }
    # Season compatibility is taken from observed feature pairs only.
    try:
        import train
        frame = train.prepare()
        model["seasons_by_crop"] = {str(crop): sorted(group.Season.astype(str).unique().tolist())
                                    for crop, group in frame.groupby("Crop")}
    except Exception:
        model["seasons_by_crop"] = {}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(model, separators=(",", ":")), encoding="utf-8")
    print(f"Exported {len(trees)} trees to {dest} ({dest.stat().st_size:,} bytes)")
    return model


if __name__ == "__main__":
    export_model()
