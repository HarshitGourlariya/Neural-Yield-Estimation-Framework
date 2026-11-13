"""Train a location-aware, stress-responsive crop yield model.

Only measured weather/soil/application values and physical location categories
are features. Year, district identifiers, and yield-derived aggregates are not.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.preprocessing import OrdinalEncoder
from sklearn.linear_model import Ridge
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).parent
DATA = ROOT / "data" / "crop_yield_dataset.csv"
OUT = ROOT / "models"
TARGET = "Yield"
NUM = ["Temperature", "Rainfall", "Humidity", "pH", "Fertilizer", "Pesticide"]
CAT = ["State", "Crop", "Season", "Soil Type"]
FEATURES = NUM + CAT
SEED = 42
PHYSICAL_CEILING = 60000.0  # kg/ha engineering safety bound; see README limitations


def normalize_state(v):
    s = " ".join(str(v).strip().split())
    return {"AndhraPradesh": "Andhra Pradesh"}.get(s, s)


def prepare(path=DATA):
    df = pd.read_csv(path, low_memory=False)
    df["District"] = df["Region"].astype(str).str.split(",", n=1).str[0].str.strip()
    df["State"] = df["Region"].astype(str).str.split(",", n=1).str[-1].map(normalize_state)
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    for col in NUM:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=[TARGET, *FEATURES])
    df = df[df[TARGET] >= 0].copy()
    return df


def stress_factors(row, refs):
    """Transparent physical guardrails, with thresholds learned from X only.

    Annual rainfall is a coarse proxy, not crop-season water balance. Its
    low-end response is deliberately strong; excessive rain represents flood.
    Fertilizer is total applied mass, not nutrient composition.
    """
    crop = str(row["Crop"])
    r = refs.get(crop, refs["__global__"])
    rain = max(0.0, float(row["Rainfall"]))
    # Water rises to a crop-specific adequate-rain reference, then is flat;
    # extreme annual totals carry a flooding penalty.
    adequate = max(250.0, r["rain_q75"])
    water = min(1.0, rain / adequate)
    flood_start = max(adequate * 2.2, r["rain_q95"])
    if rain > flood_start:
        water *= max(0.15, 1.0 - 0.7 * (rain - flood_start) / max(flood_start, 1))
    # Response curves use input distributions only, never target statistics.
    temp = float(row["Temperature"])
    opt = r["temp_median"]
    heat = 1.0 if temp <= opt + 2 else max(0.05, 1 - (temp - opt - 2) / 18)
    cold = 1.0 if temp >= opt - 10 else max(0.05, 1 - (opt - 10 - temp) / 20)
    temp_factor = min(heat, cold)
    fert_opt = max(20.0, r["fert_q75"])
    fertilizer = 0.25 + 0.75 * min(max(0.0, float(row["Fertilizer"])) / fert_opt, 1.0)
    ph_delta = abs(float(row["pH"]) - r["ph_median"])
    ph_factor = max(0.15, 1.0 - 0.18 * ph_delta)
    soil_name = str(row["Soil Type"])
    # Coarse soil-class prior; actual field texture/drainage is unavailable.
    soil_factor = {"Mollisols": 1.0, "Alfisols": .94, "Inceptisols": .90,
                   "Vertisols": .86, "Entisols": .82, "Aridisols": .72}.get(soil_name, .85)
    # A small pesticide penalty only above the observed crop q90.
    pest_excess = max(0.0, float(row["Pesticide"]) - r["pest_q90"])
    pesticide = max(0.7, 1.0 - 0.15 * pest_excess / max(r["pest_q90"], .01))
    return {"water": water, "temperature": temp_factor, "fertilizer": fertilizer,
            "soil_ph": ph_factor, "soil_class": soil_factor, "pesticide": pesticide}


def build_refs(df):
    refs = {}
    for crop, g in df.groupby("Crop"):
        refs[crop] = {"rain_q75": float(g.Rainfall.quantile(.75)),
                      "rain_q95": float(g.Rainfall.quantile(.95)),
                      "temp_median": float(g.Temperature.median()),
                      "temp_q01": float(g.Temperature.quantile(.01)),
                      "temp_q99": float(g.Temperature.quantile(.99)),
                      "fert_q75": float(g.Fertilizer.quantile(.75)),
                      "ph_median": float(g["pH"].median()),
                      "pest_q90": float(g.Pesticide.quantile(.90))}
    refs["__global__"] = {"rain_q75": float(df.Rainfall.quantile(.75)),
                          "rain_q95": float(df.Rainfall.quantile(.95)),
                          "temp_median": float(df.Temperature.median()),
                          "temp_q01": float(df.Temperature.quantile(.01)),
                          "temp_q99": float(df.Temperature.quantile(.99)),
                          "fert_q75": float(df.Fertilizer.quantile(.75)),
                          "ph_median": float(df["pH"].median()),
                          "pest_q90": float(df.Pesticide.quantile(.90))}
    return refs


def augment_stress(train, refs):
    """Create explicitly flagged conservative scenarios from train X only.

    Synthetic labels are relative to each real example's fitted-scale
    baseline, multiplied by the documented stress response below.
    They are used only in the fitting fold; never in validation metrics.
    """
    base = train.sample(n=min(12000, len(train)), random_state=SEED).copy()
    syn = base.copy()
    rng = np.random.default_rng(SEED)
    syn["Rainfall"] = base.Rainfall.to_numpy() * rng.uniform(.05, .25, len(base))
    syn["Temperature"] = base.Temperature.to_numpy() + rng.uniform(5, 12, len(base))
    syn["Fertilizer"] = 0.0
    syn["Pesticide"] = 0.0
    syn["pH"] = rng.choice([4.0, 9.5], len(base))
    syn["is_synthetic"] = True
    # Apply stress penalty to paired real yield; this is explicitly a
    # counterfactual label, not observed crop-failure evidence.
    factors = [stress_factors(row, refs) for row in syn[FEATURES].to_dict("records")]
    mult = np.prod([[f[k] for k in ("water", "temperature", "fertilizer", "soil_ph", "soil_class")] for f in factors], axis=1)
    syn[TARGET] = base[TARGET].to_numpy() * mult
    observed = train.copy(); observed["is_synthetic"] = False
    return pd.concat([observed, syn], ignore_index=True)


def make_estimator(kind="hgb"):
    if kind == "ridge":
        prep = ColumnTransformer([
            ("num", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), NUM),
            ("cat", Pipeline([("impute", SimpleImputer(strategy="most_frequent")),
                               ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=2))]), CAT),
        ])
        reg = Ridge(alpha=15.0)
    else:
        prep = ColumnTransformer([
            ("num", SimpleImputer(strategy="median"), NUM),
            ("cat", Pipeline([("impute", SimpleImputer(strategy="most_frequent")),
                               ("ordinal", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1))]), CAT),
        ], sparse_threshold=0)
        reg = HistGradientBoostingRegressor(max_iter=130, learning_rate=.08,
            max_leaf_nodes=31, l2_regularization=2.0, random_state=SEED,
            categorical_features=[False] * len(NUM) + [True] * len(CAT),
            # Rain and fertilizer are constrained increasing; pesticide is
            # constrained decreasing. Flood and heat penalties are handled by
            # the explicit non-monotonic physical stress response.
            monotonic_cst=[0, 1, 0, 0, 1, -1, 0, 0, 0, 0])
    return Pipeline([("prep", prep), ("reg", reg)])


def physical_predict(model, X, refs):
    # log1p makes the learned component positive and gives a continuous path
    # below the training minimum; stress factors force physical failure cases.
    model_x = X.copy()
    for idx, row in model_x.iterrows():
        limits = refs.get(str(row["Crop"]), refs["__global__"])
        model_x.at[idx, "Temperature"] = np.clip(float(row["Temperature"]), limits["temp_q01"], limits["temp_q99"])
    raw = np.expm1(model.predict(model_x))
    factors = [stress_factors(row, refs) for row in X[FEATURES].to_dict("records")]
    stress = np.prod([[f[k] for k in ("water", "temperature", "fertilizer", "soil_ph", "soil_class", "pesticide")] for f in factors], axis=1)
    return np.clip(raw * stress, 0, PHYSICAL_CEILING)


def metrics(y, p, low_mask, high_mask):
    return {"mae": float(mean_absolute_error(y, p)),
            "rmse": float(mean_squared_error(y, p) ** .5),
            "r2": float(r2_score(y, p)),
            "bottom_decile_mae": float(mean_absolute_error(y[low_mask], p[low_mask])),
            "top_decile_mae": float(mean_absolute_error(y[high_mask], p[high_mask]))}


def main():
    df = prepare()
    print("DATA SUMMARY")
    print(f"Rows: {len(df):,}; features: {FEATURES}")
    print("Yield quantiles (kg/ha):\n", df[TARGET].describe(percentiles=[.01,.05,.1,.25,.5,.75,.9,.95,.99]).to_string())
    print("Missing values:\n", df[FEATURES + [TARGET]].isna().sum().to_string())
    print("Feature ranges:\n", df[NUM].describe().loc[["min","50%","max"]].to_string())
    print("Yield Pearson correlations:\n", df[NUM].corrwith(df[TARGET]).round(3).to_string())
    low = df[TARGET] <= df[TARGET].quantile(.1); high = df[TARGET] >= df[TARGET].quantile(.9)
    print(f"Low-yield counts: <50={int((df[TARGET]<50).sum()):,}; <100={int((df[TARGET]<100).sum()):,}; "
          f"bottom decile={int(low.sum()):,}; zero={int((df[TARGET]==0).sum()):,}")
    print("LEAKAGE REPORT: excluded Year, Region/district identifier, row ids, N/P/K, target encodings, and yield-derived aggregates. "
          "State is parsed from Region and Soil Type is a physical measured/location attribute. Region is used only as CV group.")

    groups = df["Region"].astype(str)
    cv = GroupKFold(n_splits=5)
    results, oof = {}, {}
    for kind in ("ridge", "hgb"):
        pred = np.full(len(df), np.nan)
        for tr, va in cv.split(df[FEATURES], df[TARGET], groups):
            fold = df.iloc[tr].copy(); valid = df.iloc[va]
            refs = build_refs(fold)
            augmented = augment_stress(fold, refs)
            est = make_estimator(kind)
            with threadpool_limits(limits=1):
                est.fit(augmented[FEATURES], np.log1p(augmented[TARGET]))
                pred[va] = physical_predict(est, valid[FEATURES], refs)
        results[kind] = metrics(df[TARGET].to_numpy(), pred, low.to_numpy(), high.to_numpy())
        oof[kind] = pred
        print(f"{kind.upper()} group-CV:", json.dumps(results[kind]))

    # Final model is the continuous/log-target boosted model plus explicit
    # agronomic stress response. Intervals use held-out-region OOF errors.
    final_refs = build_refs(df)
    augmented = augment_stress(df, final_refs)
    augmented.loc[augmented["is_synthetic"], [*FEATURES, TARGET, "is_synthetic"]].to_csv(
        OUT / "synthetic_stress_rows.csv", index=False)
    final = make_estimator("hgb")
    with threadpool_limits(limits=1):
        final.fit(augmented[FEATURES], np.log1p(augmented[TARGET]))
    residual = np.abs(df[TARGET].to_numpy() - oof["hgb"])
    q90 = float(np.quantile(residual, .90))
    # Soil and district lists are only physical catalog/soil labels.
    training_districts = df.groupby("State")["District"].apply(lambda s: sorted(set(s))).to_dict()
    districts = {state: list(values) for state, values in training_districts.items()}
    # Keep commonly requested urban locations selectable; no matching paired
    # field measurements means the response will clearly show limited coverage.
    delhi = ["Central Delhi", "East Delhi", "New Delhi", "North Delhi", "North East Delhi",
             "North West Delhi", "Shahdara", "South Delhi", "South East Delhi",
             "South West Delhi", "West Delhi"]
    districts["Delhi"] = delhi
    districts.setdefault("Uttar Pradesh", []).append("Greater Noida")
    districts["Uttar Pradesh"] = sorted(set(districts["Uttar Pradesh"]))
    soil_by_region = df.groupby("Region")["Soil Type"].agg(lambda s: s.mode().iloc[0]).to_dict()
    bundle = {"model": final, "refs": final_refs, "features": FEATURES,
              "physical_ceiling": PHYSICAL_CEILING, "conformal_q90": q90,
              "districts_by_state": districts, "typical_soil_by_region": soil_by_region}
    OUT.mkdir(exist_ok=True)
    joblib.dump(bundle, OUT / "yield_model.joblib")
    report = {"model": "log-target HistGradientBoosting + transparent multiplicative stress factors",
        "rows": int(len(df)), "historical_year_range": [int(df.Year.min()), int(df.Year.max())],
        "feature_rows_through": int(df.Year.max()), "features": FEATURES,
        "excluded_features": ["Year", "Region/district ID", "row IDs", "target encodings", "district/year yield means", "Nitrogen", "Phosphorus", "Potassium"],
        "synthetic_rows_added_final_fit": int(len(augmented)-len(df)),
        "synthetic_label_method": "paired observed yield multiplied by water/heat/fertilizer/pH/soil-class stress factors; synthetic flag retained in training frame only",
        "evaluation": "5-fold GroupKFold by Region; synthetic rows only in fold training; validation uses observed records only",
        "monotonic_constraints": {"Rainfall": "increasing before explicit flood stress", "Fertilizer": "increasing to saturation", "Pesticide": "decreasing"},
        "comparison": results, "mae_kg_ha": results["hgb"]["mae"], "rmse_kg_ha": results["hgb"]["rmse"],
        "r2": results["hgb"]["r2"], "bottom_decile_mae": results["hgb"]["bottom_decile_mae"],
        "top_decile_mae": results["hgb"]["top_decile_mae"], "conformal_90_abs_error": q90,
        "physical_ceiling_kg_ha": PHYSICAL_CEILING,
        "rows_below_50": int((df[TARGET]<50).sum()), "rows_below_100": int((df[TARGET]<100).sum()),
        "zero_yield_rows": int((df[TARGET]==0).sum()), "districts_by_state": districts,
        "training_districts_by_state": training_districts,
        "typical_soil_by_region": soil_by_region, "states": sorted(df.State.unique()),
        "crops": sorted(df.Crop.unique()), "seasons": sorted(df.Season.unique()),
        "soil_types": sorted(df["Soil Type"].unique()),
        "sensor_fields_not_in_training": ["soil moisture", "light intensity"],
        "notes": ["Source records provide annual rainfall and district proxy pH; neither equals a field-season measurement.",
                  "2024/25 yield-only records lack matched weather and sensor features and are not used."]}
    (OUT / "training_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("SAVED models/yield_model.joblib and models/training_report.json")
    print("FINAL REPORT:", json.dumps({k:v for k,v in report.items() if k not in ("districts_by_state","typical_soil_by_region")}, indent=2))


if __name__ == "__main__":
    main()
