const form = document.querySelector('#yield-form');
const toast = document.querySelector('#toast');
const M = window.GREENYIELD_MESSAGES || {};
let meta = null;
let onlineWeather = {};
let manuallyEdited = {temperature: false, humidity: false, rainfall: false};
let weatherRequestId = 0;
let weatherController = null;
let selectedWeatherLocation = '';
const $ = (id) => {
  const element = document.getElementById(id);
  if (!element) console.warn(`[GreenYield] Missing element #${id}; skipping this UI update.`);
  return element;
};
const select = (selector) => {
  const element = document.querySelector(selector);
  if (!element) console.warn(`[GreenYield] Missing element ${selector}; skipping this UI update.`);
  return element;
};
const weatherRanges = {temperature: [-10, 55], humidity: [0, 100], rainfall: [0, 12000]};

function setText(id, value) { const element = $(id); if (element) element.textContent = value; }
function setHidden(id, value) { const element = $(id); if (element) element.hidden = value; }
function setDisabled(id, value) { const element = $(id); if (element) element.disabled = value; }
function setElementDisabled(element, value, name) {
  if (!element) { console.warn(`[GreenYield] Missing ${name}; skipping disabled state.`); return; }
  element.disabled = value;
}

function notify(message, error = false) {
  if (!toast) { console.warn('[GreenYield] Missing #toast; notification skipped.'); return; }
  toast.textContent = message;
  toast.classList.toggle('error', error);
  toast.classList.add('show');
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => toast.classList.remove('show'), 3600);
}

function populate(id, values, placeholder, labelFor = (value) => value) {
  const select = $(id);
  if (!select) return;
  select.replaceChildren(new Option(placeholder, ''));
  values.forEach((value) => select.add(new Option(labelFor(value), value)));
}

async function init() {
  try {
    let response;
    try { response = await fetch('/api/meta', {cache:'no-store'}); } catch (_) { response = null; }
    if (response?.ok) {
      meta = await response.json();
      try { localStorage.setItem('greenyield-meta-v1', JSON.stringify(meta)); } catch (_) {}
    } else {
      try { meta = JSON.parse(localStorage.getItem('greenyield-meta-v1') || 'null'); } catch (_) { meta = null; }
    }
    if (!meta?.districts_by_state) throw new Error(M.service_error || 'Open GreenYield once while connected to prepare offline use.');
    const t = (value) => window.GreenYieldI18n?.translate(value) || value;
    populate('state', Object.keys(meta.districts_by_state).sort(), t('Choose state'), (value) =>
      t(value.replace('AndhraPradesh', 'Andhra Pradesh').replace(/\s+/g, ' ')));
    populate('district', [], t('Choose state first'));
    const stateSelect = $('state');
    if (stateSelect) stateSelect.addEventListener('change', () => {
      cancelWeatherRequest();
      const state = stateSelect.value;
      populate('district', state ? meta.districts_by_state[state] : [], state ? t('Choose district') : t('Choose state first'), t);
      setDisabled('district', !state);
      selectedWeatherLocation = '';
      resetWeatherFields(true);
    });
    const districtSelect = $('district');
    if (districtSelect) districtSelect.addEventListener('change', () => {
      const state = $('state')?.value || '';
      const district = districtSelect.value;
      const locationKey = `${state}|${district}`;
      if (locationKey !== selectedWeatherLocation) {
        selectedWeatherLocation = locationKey;
        cancelWeatherRequest();
        resetWeatherFields(true);
        if (district) window.GreenYieldLocal?.reachable().then((online) => {
          if (online) refreshWeather();
          else {
            setText('weather-warning', window.GreenYieldI18n?.translate('No internet. Please enter this year’s field weather yourself.') || 'No internet. Please enter this year’s field weather yourself.');
            setHidden('weather-warning', false);
          }
        });
      }
    });
    const refreshButton = $('refresh-weather');
    if (refreshButton) refreshButton.addEventListener('click', () => refreshWeather());
    document.querySelectorAll('.reset-weather').forEach((button) => button.addEventListener('click', () => {
      const field = button.dataset.field;
      if (onlineWeather[field] === undefined) return;
      const input = $(field);
      if (!input) return;
      input.value = onlineWeather[field];
      manuallyEdited[field] = false;
      const t = (value) => window.GreenYieldI18n?.translate(value) || value;
      setText(field + '-source', `${t('Auto-filled from')} ${onlineWeather.source || 'online data'}, ${onlineWeather.fetched_at || ''}`);
      setHidden(field + '-source', false);
      validateWeatherField(field);
    }));
    Object.keys(weatherRanges).forEach((field) => {
      const input = $(field);
      if (!input) return;
      input.addEventListener('input', () => {
        manuallyEdited[field] = true;
        setText(field + '-source', window.GreenYieldI18n?.translate('Edited manually') || 'Edited manually');
        setHidden(field + '-source', false);
        input.classList.remove('weather-loading');
        setHidden(field + '-spinner', true);
        validateWeatherField(field);
      });
    });
    document.querySelectorAll('[name="ph"], [name="fertilizer_kg"], [name="pesticide_kg"]').forEach((input) => {
      const note = document.createElement('small'); note.className = 'range-warning'; note.dataset.fieldCheck = input.name; input.insertAdjacentElement('afterend', note);
      input.addEventListener('change', () => checkFieldInput(input));
    });
    const sensorFile = $('sensor-json-file');
    const sensorImport = $('import-sensor-json');
    const sensorText = $('sensor-json-text');
    const sensorStatus = $('sensor-import-status');
    const applySensorJson = (raw) => {
      const payload = typeof raw === 'string' ? JSON.parse(raw) : raw;
      const readings = payload?.readings || payload?.sensors || payload;
      const aliases = {temperature:['temperature','temp','temperature_c'], humidity:['humidity','rh','humidity_percent'], ph:['ph','soil_ph'], soil_moisture:['soil_moisture','moisture','moisture_percent'], light_lux:['light_lux','light','lux']};
      let count = 0;
      for (const [field, keys] of Object.entries(aliases)) {
        const value = keys.map((key) => readings?.[key]).find((item) => item !== undefined && item !== null && item !== '');
        const input = form.elements.namedItem(field);
        if (value !== undefined && input && Number.isFinite(Number(value))) {
          input.value = Number(value); input.dispatchEvent(new Event('input', {bubbles:true})); input.dispatchEvent(new Event('change', {bubbles:true})); count++;
        }
      }
      if (!count) throw new Error('No supported sensor values found. Expected temperature, humidity, pH, moisture or light readings.');
      if (sensorStatus) sensorStatus.textContent = `${count} sensor reading${count===1?'':'s'} imported.`;
    };
    sensorFile?.addEventListener('change', async () => { try { if (sensorFile.files?.[0]) applySensorJson(await sensorFile.files[0].text()); } catch (error) { if(sensorStatus)sensorStatus.textContent=error.message; } });
    sensorImport?.addEventListener('click', () => { try { applySensorJson(sensorText?.value || ''); } catch (error) { if(sensorStatus)sensorStatus.textContent=error.message || 'Could not read the sensor JSON.'; } });
    populate('season', meta.seasons, t('Choose season'), (value) => t(value === 'Whole Year' ? 'All year' : value));
    populate('crop', [], t('Choose season first'));
    const seasonSelect = $('season');
    const cropSelect = $('crop');
    if (seasonSelect && cropSelect) {
      seasonSelect.addEventListener('change', () => {
        const crops = meta.crops_by_season?.[seasonSelect.value] || [];
        populate('crop', crops, seasonSelect.value ? t('Choose crop for this season') : t('Choose season first'), t);
        cropSelect.disabled = !seasonSelect.value;
      });
    }
    restoreFormDraft();
    window.GreenYieldI18n?.apply();
    document.addEventListener('greenyield:language', () => {
      if (!meta?.districts_by_state) return;
      const t = (value) => window.GreenYieldI18n?.translate(value) || value;
      const stateValue = $('state')?.value || '', districtValue = $('district')?.value || '';
      const seasonValue = $('season')?.value || '', cropValue = $('crop')?.value || '';
      populate('state', Object.keys(meta.districts_by_state).sort(), t('Choose state'), (value) => t(value.replace('AndhraPradesh', 'Andhra Pradesh').replace(/\s+/g, ' ')));
      if ($('state')) $('state').value = stateValue;
      populate('district', stateValue ? meta.districts_by_state[stateValue] || [] : [], t(stateValue ? 'Choose district' : 'Choose state first'), t);
      if ($('district')) $('district').value = districtValue;
      populate('season', meta.seasons, t('Choose season'), (value) => t(value === 'Whole Year' ? 'All year' : value));
      if ($('season')) $('season').value = seasonValue;
      const crops = meta.crops_by_season?.[seasonValue] || [];
      populate('crop', crops, t(seasonValue ? 'Choose crop for this season' : 'Choose season first'), t);
      if ($('crop')) $('crop').value = cropValue;
    });
  } catch (error) {
    notify(M.service_error || 'The estimate service is temporarily unavailable. Please try again shortly.', true);
  }
}

async function checkFieldInput(input) {
  if (!meta || !$('district')?.value || !$('crop')?.value || !input.value) return;
  const note = document.querySelector(`[data-field-check="${input.name}"]`);
  if (!note) return;
  try {
    const response = await fetch('/api/field-check', {method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({state:$('state').value,district:$('district').value,crop:$('crop').value,
        season:$('season').value,field:input.name,value:Number(input.value),area_acres:Number(form.elements.area_acres.value)})});
    if (response.ok) note.textContent=(await response.json()).warning || '';
  } catch (_) { note.textContent=''; }
}

function cancelWeatherRequest() {
  weatherRequestId += 1;
  if (weatherController) weatherController.abort();
  weatherController = null;
  setDisabled('refresh-weather', false);
  const button = $('refresh-weather');
  if (button) button.textContent = 'Refresh data';
}

function setFieldLoading(field, loading, location = '') {
  const input = $(field);
  if (input) {
    input.classList.toggle('weather-loading', loading);
    if (loading) input.placeholder = 'Loading…';
    else input.placeholder = field === 'temperature' ? 'Enter temperature' : field === 'humidity' ? 'Enter humidity' : 'Enter rainfall';
  }
  setHidden(field + '-spinner', !loading);
}

function resetWeatherFields(clearValues) {
  onlineWeather = {};
  Object.keys(weatherRanges).forEach((field) => {
    manuallyEdited[field] = false;
    const input = $(field);
    if (input) {
      if (clearValues) input.value = '';
      input.classList.remove('weather-loading');
    }
    setHidden(field + '-spinner', true);
    setElementDisabled(select(`.reset-weather[data-field="${field}"]`), true, `reset button for ${field}`);
    validateWeatherField(field);
  });
  setHidden('weather-warning', true);
  setText('weather-warning', '');
  setHidden('weather-loading', true);
}

function validateWeatherField(field) {
  const input = $(field);
  const warning = $(field + '-warning');
  if (!input || !warning) return;
  if (input.value === '') {
    warning.textContent = '';
    return;
  }
  const [min, max] = weatherRanges[field];
  const value = Number(input.value);
  warning.textContent = Number.isFinite(value) && (value < min || value > max)
    ? `${window.GreenYieldI18n?.translate('Expected range') || 'Expected range'}: ${min} to ${max}${field === 'temperature' ? ' °C' : field === 'humidity' ? '%' : ' mm/year'}.`
    : '';
}

async function refreshWeather() {
  const stateSelect = $('state');
  const locationSelect = $('district');
  if (!stateSelect || !locationSelect) return;
  const state = stateSelect.value;
  const location = locationSelect.value;
  if (!state || !location) {
    setText('weather-warning', window.GreenYieldI18n?.translate('Choose a state and location to fetch online weather. You can enter the weather manually.') || 'Choose a state and location to fetch online weather. You can enter the weather manually.');
    setHidden('weather-warning', false);
    return;
  }
  if (weatherController) weatherController.abort();
  const requestId = ++weatherRequestId;
  weatherController = typeof AbortController === 'function' ? new AbortController() : null;
  const controller = weatherController;
  const button = $('refresh-weather');
  setElementDisabled(button, true, 'Refresh data button');
  if (button) button.textContent = 'Refreshing…';
  setHidden('weather-warning', true);
  setText('weather-warning', '');
  setHidden('weather-loading', false);
  Object.keys(weatherRanges).forEach((field) => setFieldLoading(field, true, location));
  try {
    const query = new URLSearchParams({state, location});
    let response;
    try {
      response = await fetch(`/api/weather?${query.toString()}`, controller ? {signal: controller.signal} : {});
    } catch (error) {
      if (error.name === 'AbortError') throw error;
      throw new Error(`Weather request failed: ${error.message || 'connection error'}.`);
    }
    let payload;
    try { payload = await response.json(); }
    catch (error) { throw new Error(`Parsing failed while reading the weather response: ${error.message || 'invalid JSON'}.`); }
    if (!response.ok) throw new Error(payload.error || `Weather request failed with HTTP ${response.status}.`);
    const validValues = payload.values && Object.keys(weatherRanges).every((field) => {
      const value = Number(payload.values[field]);
      const [min, max] = weatherRanges[field];
      return Number.isFinite(value) && value >= min && value <= max;
    });
    const validYears = Array.isArray(payload.years) && payload.years.length === 5;
    const validRainfallByYear = payload.rainfall_by_year && typeof payload.rainfall_by_year === 'object'
      && Object.values(payload.rainfall_by_year).length === 5
      && Object.values(payload.rainfall_by_year).every((value) => Number.isFinite(Number(value)) && Number(value) >= 0);
    if (!validValues || !validYears || !validRainfallByYear || !payload.source || !payload.fetched_at) {
      throw new Error('Parsing failed: the weather response is missing valid temperature, humidity, annual rainfall, source, time, or yearly rainfall values.');
    }
    if (requestId !== weatherRequestId || state !== stateSelect.value || location !== locationSelect.value) return;
    onlineWeather = {...payload.values};
    onlineWeather.source = payload.source;
    onlineWeather.period = payload.period;
    onlineWeather.fetched_at = payload.fetched_at;
    Object.keys(weatherRanges).forEach((field) => {
      setFieldLoading(field, false);
      const input = $(field);
      if (!manuallyEdited[field] && input) input.value = payload.values[field];
      const t = (value) => window.GreenYieldI18n?.translate(value) || value;
      setText(field + '-source', manuallyEdited[field] ? t('Edited manually') : `${t('Auto-filled from')} ${payload.source}, ${payload.fetched_at}`);
      setHidden(field + '-source', false);
      setElementDisabled(select(`.reset-weather[data-field="${field}"]`), false, `reset button for ${field}`);
      validateWeatherField(field);
    });
    if (payload.fallback_note) {
      setText('weather-warning', payload.fallback_note);
      setHidden('weather-warning', false);
    }
  } catch (error) {
    if (error.name === 'AbortError' || requestId !== weatherRequestId) return;
    Object.keys(weatherRanges).forEach((field) => {
      setFieldLoading(field, false);
      const input = $(field);
      if (!manuallyEdited[field]) {
        if (input) input.value = '';
      }
      setElementDisabled(select(`.reset-weather[data-field="${field}"]`), true, `reset button for ${field}`);
    });
    onlineWeather = {};
    setHidden('weather-loading', true);
    setText('weather-warning', `${error.message} Prediction remains available after you enter the weather values.`);
    setHidden('weather-warning', false);
  } finally {
    if (requestId === weatherRequestId) {
      setHidden('weather-loading', true);
      setElementDisabled(button, false, 'Refresh data button');
      if (button) button.textContent = 'Refresh data';
      weatherController = null;
    }
  }
}
window.refreshWeather = refreshWeather;

function payloadFromForm() {
  const data = new FormData(form);
  const result = Object.fromEntries(data.entries());
  for (const key of ['temperature', 'humidity', 'rainfall', 'area_acres', 'ph', 'fertilizer_kg', 'pesticide_kg', 'soil_moisture', 'light_lux']) {
    result[key] = Number(result[key]);
  }
  return result;
}

async function saveOfflineEstimate(body) {
  if (!window.GreenYieldLocal) throw new Error('Offline estimate is not ready. Reopen GreenYield while connected once.');
  let soil = meta?.typical_soil_by_region?.[`${body.district}, ${body.state}`];
  if (!soil) {
    const soils = Object.entries(meta?.typical_soil_by_region || {}).filter(([key]) => key.endsWith(`, ${body.state}`)).map(([,value]) => value);
    soil = soils[0] || 'Inceptisols';
  }
  const areaHa = Number(body.area_acres) * .404685642;
  const modelInput = {State:body.state,District:body.district,Crop:body.crop,Season:body.season,'Soil Type':soil,
    Temperature:Number(body.temperature),Rainfall:Number(body.rainfall),Humidity:Number(body.humidity),
    'pH':Number(body.ph),Fertilizer:Number(body.fertilizer_kg)/areaHa,Pesticide:Number(body.pesticide_kg)/areaHa};
  const prediction = await window.GreenYieldLocal.predict(modelInput);
  const id = `offline-${Date.now()}-${Math.random().toString(36).slice(2,8)}`;
  await window.GreenYieldLocal.db.put({id,created_at:new Date().toISOString(),inputs:{...body},model_input:modelInput,
    prediction,ai_provider:'',offline:true});
  window.location.assign(`/report/offline/${encodeURIComponent(id)}`);
}

function saveFormDraft() {
  try {
    const values = Object.fromEntries(new FormData(form).entries());
    const sources = {};
    for (const key of ['temperature', 'humidity', 'rainfall']) {
      sources[key] = onlineWeather[key] !== undefined && !manuallyEdited[key] ? 'online' : 'manual';
    }
    sources.ph = 'manual';
    sources.soil_moisture = 'manual; sensor not connected';
    sources.light_lux = 'manual; sensor not connected';
    sessionStorage.setItem('greenyield-form-draft', JSON.stringify({values, sources, onlineWeather}));
  } catch (error) { console.warn('[GreenYield] Could not preserve form draft:', error); }
}

function restoreFormDraft() {
  try {
    const draft = JSON.parse(sessionStorage.getItem('greenyield-form-draft') || 'null');
    if (!draft?.values) return;
    const state = $('state');
    if (state && draft.values.state) {
      state.value = draft.values.state;
      populate('district', meta.districts_by_state[state.value] || [], 'Choose district');
      setDisabled('district', false);
      $('district').value = draft.values.district || '';
    }
    if (draft.values.season && $('season')) {
      $('season').value = draft.values.season;
      $('season').dispatchEvent(new Event('change'));
    }
    Object.entries(draft.values).forEach(([key, value]) => {
      if (key === 'state' || key === 'district') return;
      const field = form.elements.namedItem(key);
      if (field) field.value = value;
    });
    onlineWeather = draft.onlineWeather || {};
    for (const key of ['temperature', 'humidity', 'rainfall']) {
      const source = draft.sources?.[key] || 'manual';
      manuallyEdited[key] = source === 'manual';
      setElementDisabled(select(`.reset-weather[data-field="${key}"]`), onlineWeather[key] === undefined, `reset button for ${key}`);
    }
  } catch (error) { console.warn('[GreenYield] Could not restore form draft:', error); }
}

if (form) form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!form.reportValidity()) return;
  const button = form.querySelector('button[type=submit]');
  setElementDisabled(button, true, 'Estimate my yield button');
  const buttonText = button && button.querySelector('span');
  if (buttonText) buttonText.textContent = 'Preparing report…';
  const loading = $('report-loading');
  setHidden('report-loading', false);
  const steps = loading ? [...loading.querySelectorAll('li')] : [];
  let activeStep = 0;
  const progressTimer = setInterval(() => {
    activeStep = Math.min(activeStep + 1, steps.length - 1);
    steps.forEach((step, index) => step.classList.toggle('active', index === activeStep));
  }, 1200);
  try {
    saveFormDraft();
    const body = payloadFromForm();
    body.input_sources = {
      temperature: onlineWeather.temperature !== undefined && !manuallyEdited.temperature ? 'online' : 'manual',
      humidity: onlineWeather.humidity !== undefined && !manuallyEdited.humidity ? 'online' : 'manual',
      rainfall: onlineWeather.rainfall !== undefined && !manuallyEdited.rainfall ? 'online' : 'manual',
      ph: 'manual', soil_moisture: 'manual; sensor not connected', light_lux: 'manual; sensor not connected',
    };
    body.language = window.GreenYieldI18n?.lang() === 'hi' ? 'Hindi' : 'English';
    let response;
    try {
      response = await fetch('/api/reports', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    } catch (_) { await saveOfflineEstimate(body); return; }
    let result;
    try { result = await response.json(); } catch (_) { result = {}; }
    if (!response.ok) {
      if (response.status >= 500) { await saveOfflineEstimate(body); return; }
      throw new Error(result.error || 'Please check the entered field values.');
    }
    try {
      const saved = await fetch(`/api/reports/${encodeURIComponent(result.report_id)}`);
      if (saved.ok) {
        const record = await saved.json();
        const fields = record.inputs, place = record.analysis.location, areaHa = Number(fields.area_acres) * .404685642;
        const modelInput = {State:place.state,District:place.district,Crop:place.crop,Season:place.season,'Soil Type':place.soil_type,
          Temperature:Number(fields.temperature),Rainfall:Number(fields.rainfall),Humidity:Number(fields.humidity),
          'pH':Number(fields.ph),Fertilizer:Number(fields.fertilizer_kg)/areaHa,Pesticide:Number(fields.pesticide_kg)/areaHa};
        const prediction = await window.GreenYieldLocal.predict(modelInput);
        await window.GreenYieldLocal.db.put({id:result.report_id,created_at:record.created_at,inputs:fields,model_input:modelInput,
          prediction,ai_provider:record.ai_provider||'',snapshot:{analysis:record.analysis,narrative:record.narrative,language:record.language},offline:false,report_url:result.report_url});
      }
    } catch (error) { console.info('[GreenYield] Online report could not be copied to this phone:', error); }
    window.location.assign(result.report_url);
  } catch (error) {
    setHidden('report-loading', true);
    notify(error.message, true);
  } finally {
    clearInterval(progressTimer);
    setElementDisabled(button, false, 'Estimate my yield button');
    if (buttonText) buttonText.textContent = 'Estimate my yield';
  }
});
else console.warn('[GreenYield] Missing #yield-form; prediction form listener was not attached.');

init();
