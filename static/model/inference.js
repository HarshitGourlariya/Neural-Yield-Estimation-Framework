/* Exact browser port of the exported sklearn HGB pipeline and stress factors. */
(function (root) {
  'use strict';
  function binarySearchRight(values, target) {
    let lo = 0, hi = values.length;
    while (lo < hi) { const mid = (lo + hi) >>> 1; if (target <= values[mid]) hi = mid; else lo = mid + 1; }
    return lo;
  }
  function encode(model, input) {
    const cats = model.features.categorical.map((name, index) => {
      const value = input[name] == null || input[name] === '' ? null : String(input[name]);
      const values = model.features.category_values[name];
      const found = value == null ? -1 : values.indexOf(value);
      return found < 0 ? model.features.category_fallback[name] : found;
    });
    const nums = model.features.numeric.map((name, index) => {
      const value = Number(input[name]);
      return Number.isFinite(value) ? value : model.features.numeric_fallback[index];
    });
    const values = cats.concat(nums), bins = [];
    for (let i = 0; i < cats.length; i++) bins.push(cats[i]);
    for (let i = 0; i < nums.length; i++) bins.push(binarySearchRight(model.bins.numeric_thresholds[i], nums[i]));
    return {values, bins};
  }
  function predictRaw(model, input) {
    const encoded = encode(model, input);
    let logYield = model.booster.baseline;
    for (const tree of model.booster.trees) {
      const nodes = tree.nodes;
      let nodeIndex = 0;
      while (!nodes[nodeIndex][4]) {
        const node = nodes[nodeIndex], feature = node[2], bin = encoded.bins[feature];
        let left;
        if (node[5]) {
          const word = tree.category_bitsets[node[6]][bin >>> 5] || 0;
          left = ((word >>> (bin & 31)) & 1) === 1;
        } else {
          left = bin <= node[3];
        }
        nodeIndex = node[left ? 0 : 1];
      }
      logYield += nodes[nodeIndex][7];
    }
    return Math.expm1(logYield);
  }
  function stressFactors(input, refs) {
    const r = refs[input.Crop] || refs.__global__;
    const rain = Math.max(0, Number(input.Rainfall));
    const adequate = Math.max(250, r.rain_q75);
    let water = Math.min(1, rain / adequate);
    const floodStart = Math.max(adequate * 2.2, r.rain_q95);
    if (rain > floodStart) water *= Math.max(.15, 1 - .7 * (rain - floodStart) / Math.max(floodStart, 1));
    const temp = Number(input.Temperature), opt = r.temp_median;
    const heat = temp <= opt + 2 ? 1 : Math.max(.05, 1 - (temp - opt - 2) / 18);
    const cold = temp >= opt - 10 ? 1 : Math.max(.05, 1 - (opt - 10 - temp) / 20);
    const fertilizer = .25 + .75 * Math.min(Math.max(0, Number(input.Fertilizer)) / Math.max(20, r.fert_q75), 1);
    const soilPh = Math.max(.15, 1 - .18 * Math.abs(Number(input['pH']) - r.ph_median));
    const soilClass = {Mollisols:1,Alfisols:.94,Inceptisols:.90,Vertisols:.86,Entisols:.82,Aridisols:.72}[input['Soil Type']] ?? .85;
    const pestExcess = Math.max(0, Number(input.Pesticide) - r.pest_q90);
    const pesticide = Math.max(.7, 1 - .15 * pestExcess / Math.max(r.pest_q90, .01));
    return {water,temperature:Math.min(heat,cold),fertilizer,soil_ph:soilPh,soil_class:soilClass,pesticide};
  }
  function predict(model, input) {
    const refs = model.refs[input.Crop] || model.refs.__global__;
    const modelInput = {...input, Temperature: Math.max(refs.temp_q01, Math.min(refs.temp_q99, Number(input.Temperature)))};
    const raw = predictRaw(model, modelInput);
    const factors = stressFactors(input, model.refs);
    const stress = Object.values(factors).reduce((a,b) => a*b, 1);
    const yieldKgHa = Math.max(0, Math.min(model.physical_ceiling, raw * stress));
    return {yield_kg_ha:yieldKgHa,
      interval_90_kg_ha:[Math.max(0,yieldKgHa-model.conformal_q90),Math.min(model.physical_ceiling,yieldKgHa+model.conformal_q90)],
      stress_factors:factors,pre_stress_model_estimate:raw};
  }
  const api = {encode,predictRaw,stressFactors,predict};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.GreenYieldInference = api;
})(typeof self !== 'undefined' ? self : globalThis);
