"""Compare browser inference against Python for real, varied, and stress inputs."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from train import FEATURES, prepare
from predict import prediction

ROOT = Path(__file__).resolve().parent
JS = ROOT / "static" / "model" / "inference.js"
MODEL = ROOT / "models" / "yield_model.joblib"


def make_cases(count=128):
    bundle = joblib.load(MODEL)
    frame = prepare()
    picked = frame.sample(n=count, random_state=214)
    cases = [{key: row[key].item() if isinstance(row[key], np.generic) else row[key]
              for key in FEATURES} for _, row in picked.iterrows()]
    base = dict(cases[0])
    for i, (rain, temp, fert, ph, pest) in enumerate([
        (0, 55, 0, 4, 0), (1, 50, 0, 9.5, 10), (20, 48, 5, 4.5, 5),
        (12000, -10, 5000, 14, 100), (0, -10, 0, 14, 0), (2500, 35, 1000, 7, 0),
    ]):
        case = dict(base)
        case.update({"Rainfall":rain,"Temperature":temp,"Fertilizer":fert,"pH":ph,"Pesticide":pest})
        cases.append(case)
    # Target categories and inputs always come from the actual shipped catalogs.
    return bundle, cases


def main():
    model_bundle, cases = make_cases()
    expected = [prediction(model_bundle, case) for case in cases]
    script = r"""
const fs=require('fs'), model=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));
let input=''; process.stdin.setEncoding('utf8'); process.stdin.on('data',chunk=>input+=chunk);
process.stdin.on('end',()=>{
const cases=JSON.parse(input);
const engine=require(process.argv[4]);
process.stdout.write(JSON.stringify(cases.map(x=>engine.predict(model,x))));
});
"""
    result = subprocess.run(["node", "-e", script, str(ROOT/"static"/"model"/"yield-model.json"),
                             "", "", str(JS)], input=json.dumps(cases), capture_output=True,
                            text=True, check=True)
    actual = json.loads(result.stdout)
    diffs = [abs(a["yield_kg_ha"] - e["yield_kg_ha"]) for a,e in zip(actual, expected)]
    range_diffs = [max(abs(x-y) for x,y in zip(a["interval_90_kg_ha"],e["interval_90_kg_ha"]))
                   for a,e in zip(actual, expected)]
    max_diff, max_range_diff = max(diffs), max(range_diffs)
    print(f"Compared {len(cases)} Python/browser predictions")
    print(f"Max absolute yield difference: {max_diff:.12g} kg/ha")
    print(f"Max interval endpoint difference: {max_range_diff:.12g} kg/ha")
    if max_diff > 1e-6 or max_range_diff > 1e-6:
        bad = int(np.argmax(diffs))
        raise SystemExit(f"Parity check failed at case {bad}: Python={expected[bad]}, browser={actual[bad]}")
    print("PASS: yield and interval parity within 1e-6 kg/ha")


if __name__ == "__main__":
    main()
