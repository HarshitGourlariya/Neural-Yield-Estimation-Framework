(() => {
  let report = window.GREENYIELD_REPORT;
  let previewTimer;
  let previewRequestId = 0;
  const $ = (id) => document.getElementById(id);
  const setText = (id, value) => { const node = $(id); if (node) node.textContent = value ?? ''; };
  const safeText = (value, limit = 145) => {
    const text = String(value ?? '').trim();
    return text.length > limit ? `${text.slice(0, limit - 1).trimEnd()}…` : text;
  };
  const number = (value, decimals = 1) => Number(value).toLocaleString(undefined, {maximumFractionDigits: decimals});

  function makeCurve(containerId, scenario) {
    const container = $(containerId);
    if (!container) return;
    const points = scenario?.curve?.filter((point) => Number.isFinite(Number(point.input_value ?? point.input_total_kg)) &&
      Number.isFinite(Number(point.yield_kg_ha)));
    if (!points?.length) {
      container.textContent = 'Chart not available.';
      return;
    }

    const width = 560, height = 330;
    const left = 72, right = 22, top = 24, bottom = 66;
    const plotWidth = width - left - right, plotHeight = height - top - bottom;
    const valueOf = (point) => Number(point.input_value ?? point.input_total_kg);
    const unit = scenario.unit || 'kg total for field';
    const current = Number(scenario.current_value ?? scenario.current_total_kg);
    const currentX = Number.isFinite(current) ? [current] : [];
    const minX = unit === 'pH' ? Math.min(...points.map(valueOf), ...currentX) : Math.min(0, ...points.map(valueOf), ...currentX);
    const maxX = Math.max(...points.map(valueOf), ...currentX, 1);
    const maxY = Math.max(...points.map((point) => Number(point.yield_kg_ha)), 1) * 1.12;
    const x = (value) => left + (Number(value) - minX) / (maxX - minX || 1) * plotWidth;
    const y = (value) => top + plotHeight - Number(value) / maxY * plotHeight;
    const fmt = (value) => Number(value).toLocaleString(undefined, {maximumFractionDigits: 1});
    const xTicks = Array.from({length: 5}, (_, index) => minX + (maxX - minX) * index / 4);
    const yTicks = Array.from({length: 5}, (_, index) => maxY * index / 4);
    const grid = yTicks.map((tick) => {
      const yy = y(tick);
      return `<line class="chart-gridline" x1="${left}" y1="${yy}" x2="${width-right}" y2="${yy}"/>
        <text class="chart-tick" x="${left-10}" y="${yy+4}" text-anchor="end">${fmt(tick)}</text>`;
    }).join('') + xTicks.map((tick) => {
      const xx = x(tick);
      return `<line class="chart-tick-mark" x1="${xx}" y1="${top+plotHeight}" x2="${xx}" y2="${top+plotHeight+5}"/>
        <text class="chart-tick" x="${xx}" y="${top+plotHeight+21}" text-anchor="middle">${fmt(tick)}</text>`;
    }).join('');
    const line = points.map((point, index) => `${index ? 'L' : 'M'} ${x(valueOf(point))} ${y(point.yield_kg_ha)}`).join(' ');
    const dots = points.map((point) => `<circle class="chart-point" cx="${x(valueOf(point))}" cy="${y(point.yield_kg_ha)}" r="4">
      <title>${fmt(valueOf(point))} ${unit} → ${fmt(point.yield_kg_ha)} kg/ha</title></circle>`).join('');
    let marker = '';
    if (Number.isFinite(current)) {
      const nearest = points.reduce((best, point) => Math.abs(valueOf(point)-current) < Math.abs(valueOf(best)-current) ? point : best, points[0]);
      marker = `<line class="chart-current-line" x1="${x(current)}" y1="${top}" x2="${x(current)}" y2="${top+plotHeight}"/>
        <circle class="chart-current-point" cx="${x(current)}" cy="${y(nearest.yield_kg_ha)}" r="6"><title>Your current value: ${fmt(current)} ${unit}</title></circle>`;
    }
    container.innerHTML = `<svg class="yield-curve" viewBox="0 0 ${width} ${height}" role="img" aria-label="Estimated yield as fertilizer amount changes">
      ${grid}<line class="chart-axis" x1="${left}" y1="${top}" x2="${left}" y2="${top+plotHeight}"/><line class="chart-axis" x1="${left}" y1="${top+plotHeight}" x2="${width-right}" y2="${top+plotHeight}"/>
      <path class="chart-line" d="${line}"/>${dots}${marker}
      <text class="chart-axis-title" x="${left+plotWidth/2}" y="${height-10}" text-anchor="middle">${unit === 'pH' ? 'Soil pH' : `Amount (${unit})`}</text>
      <text class="chart-axis-title" transform="translate(18 ${top+plotHeight/2}) rotate(-90)" text-anchor="middle">Yield (kg/ha)</text></svg>
      <div class="chart-legend"><span><i class="legend-yield"></i>Yield outlook</span><span><i class="legend-current"></i>Your current amount</span></div>`;
  }

  function render(reportData) {
    report = reportData;
    const analysis = report.analysis, estimate = analysis.estimate, location = analysis.location, risk = analysis.risk;
    setText('report-title', `${location.crop} field outlook`);
    setText('report-yield', number(estimate.yield_kg_ha));
    setText('report-range', estimate.likely_range_kg_ha.map((value) => number(value)).join('–'));
    setText('report-total', number(estimate.total_tonnes, 3));
    setText('report-risk-badge', risk.status);
    const cropTitle = $('side-crop'); if (cropTitle) cropTitle.textContent = `${location.crop} · ${location.season}`;
    setText('side-location', `${location.district}, ${location.state} · ${estimate.area_acres} acres`);

    setText('summary-source', report.preview ? (report.language === 'Hindi' ? 'What-if का नया अनुमान' : 'Updated what-if estimate')
      : report.ai_provider === 'Gemini' ? 'Gemini AI summary' : report.ai_provider === 'Anthropic' ? 'AI summary' : 'Field summary');
    setText('narrative-summary', report.preview
      ? (report.language === 'Hindi'
        ? `आपके बदले हुए मानों पर अनुमानित उपज ${number(estimate.yield_kg_ha)} kg/ha है। यह सिर्फ़ तुलना के लिए है; असली नतीजा अलग हो सकता है।`
        : `With the changed values, estimated yield is ${number(estimate.yield_kg_ha)} kg/ha. This is a comparison preview; actual field results may differ.`)
      : safeText(report.narrative.summary));
    const findings = $('key-findings');
    if (findings) {
      const relevant = report.preview ? [] : (report.narrative.key_findings || []).filter((item) => !/kg\/ha|tonne/i.test(item)).slice(0, 3);
      findings.replaceChildren(...relevant.map((item) => { const li = document.createElement('li'); li.textContent = safeText(item, 110); return li; }));
    }

    const riskContainer = $('risk-factors');
    if (riskContainer) riskContainer.replaceChildren(...risk.drivers.map((driver) => {
      const card = document.createElement('article'); card.className = 'risk-card';
      const heading = document.createElement('div'); heading.className = 'risk-card-heading';
      const title = document.createElement('h3'); title.textContent = driver.title;
      const level = document.createElement('span'); level.className = `risk-level risk-${String(driver.level || 'low').toLowerCase()}`; level.textContent = driver.level || 'Low';
      heading.append(title, level); card.append(heading);
      if (driver.value) { const value = document.createElement('strong'); value.className = 'risk-card-value'; value.textContent = driver.value; card.append(value); }
      const reason = document.createElement('p'); reason.textContent = safeText(driver.explanation, 115); card.append(reason);
      return card;
    }));

    const recommendations = $('recommendations');
    if (recommendations) recommendations.replaceChildren(...(report.narrative.recommendations || []).map((item) => {
      const card = document.createElement('article'); card.className = 'action-card';
      const title = document.createElement('h3'); title.textContent = item.title;
      const action = document.createElement('p'); action.textContent = safeText(item.action, 130);
      card.append(title, action); return card;
    }));
    const nextSteps = $('next-steps');
    if (nextSteps) nextSteps.replaceChildren(...(report.narrative.next_steps || []).slice(0, 2).map((item) => {
      const li = document.createElement('li'); li.textContent = safeText(item, 120); return li;
    }));

    const detailGrid = $('input-summary');
    if (detailGrid) detailGrid.replaceChildren(...analysis.input_summary.map((item) => {
      const card = document.createElement('article'); card.className = 'field-detail-card';
      const label = document.createElement('span'); label.textContent = item.name;
      const value = document.createElement('strong'); value.textContent = `${item.value ?? '—'} ${item.unit || ''}`.trim();
      card.append(label, value); return card;
    }));
    const warnings = $('quality-warnings');
    if (warnings) warnings.replaceChildren(...analysis.check_your_inputs.map((item) => {
      const node = document.createElement('p'); node.textContent = item; return node;
    }));

    makeCurve('chart-fertilizer', analysis.what_if.scenarios.fertilizer);
    makeCurve('chart-pesticide', analysis.what_if.scenarios.pesticide);
    makeCurve('chart-ph', analysis.what_if.scenarios['soil ph']);
    setText('chart-explanation', report.language === 'Hindi'
      ? 'नीचे की रेखा (X) आपके खेत में खाद की मात्रा दिखाती है। बाईं ओर (Y) अनुमानित उपज kg/ha में है। हर बिंदु बाकी दर्ज जानकारी समान रखकर बनाया गया अनुमान है—यह खाद की सुझाई मात्रा नहीं है। खाद की मात्रा मिट्टी-जाँच या खाद के लेबल के अनुसार तय करें।'
      : 'The bottom axis (X) shows fertilizer amount for your field. The side axis (Y) shows estimated yield in kg/ha. Each point is an estimate with your other entries held the same. This is not a fertilizer dose recommendation; follow soil-test advice or the product label.');
    setText('pesticide-chart-explanation', report.language === 'Hindi'
      ? 'X अक्ष पर कीटनाशक की मात्रा और Y अक्ष पर अनुमानित उपज है। बाकी दर्ज मान समान रखे गए हैं। यह केवल मॉडल का अनुमान है—छिड़काव की सलाह या सुरक्षित मात्रा नहीं। कीट/रोग पहचानें और दवा का लेबल मानें।'
      : 'X shows pesticide amount; Y shows estimated yield, with your other entries held the same. This is only a model preview, not spray advice or a safe dose. Identify the pest or disease and follow the product label.');
    setText('ph-chart-explanation', report.language === 'Hindi'
      ? 'X अक्ष पर मिट्टी का pH और Y अक्ष पर अनुमानित उपज है। यह दिखाता है कि दर्ज pH बदलने पर मॉडल का अनुमान कैसे बदलता है; खेत का pH बदलने की मात्रा या उपाय मिट्टी-जाँच से तय करें।'
      : 'X shows soil pH; Y shows estimated yield. The curve shows how the model estimate changes when the entered pH changes. A soil test is needed to choose a real soil amendment and amount.');
    for (const [id, key] of [['whatif-fertilizer','fertilizer_kg'],['whatif-pesticide','pesticide_kg'],['whatif-ph','ph'],
      ['whatif-rainfall','rainfall'],['whatif-temperature','temperature'],['whatif-humidity','humidity']]) {
      const input = $(id); if (input && document.activeElement !== input) input.value = report.inputs[key];
    }
    const language = $('advice-language'); if (language) language.value = report.inputs.language || 'English';
    const previewResult = $('whatif-result');
    if (previewResult) {
      previewResult.hidden = !report.preview;
      previewResult.textContent = report.preview
        ? (report.language === 'Hindi' ? `यह सिर्फ़ अनुमान है: ${number(estimate.yield_kg_ha)} kg/ha। असली फसल का नतीजा अलग हो सकता है।`
          : `Estimate preview only: ${number(estimate.yield_kg_ha)} kg/ha. Actual field results may differ.`)
        : '';
    }
    const pdf = $('download-pdf'); if (pdf) pdf.href = `/report/${report.id}/pdf`;
  }

  function whatIfValues() {
    const values = {};
    for (const [id,key] of [['whatif-fertilizer','fertilizer_kg'],['whatif-pesticide','pesticide_kg'],['whatif-ph','ph'],
      ['whatif-rainfall','rainfall'],['whatif-temperature','temperature'],['whatif-humidity','humidity']]) {
      const input = $(id); if (input && input.value !== '') values[key] = Number(input.value);
    }
    return values;
  }
  async function preview() {
    const requestId = ++previewRequestId;
    try {
      const response = await fetch(`/api/reports/${report.id}/what-if`, {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify(whatIfValues())});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not update the preview.');
      if (requestId !== previewRequestId) return;
      data.report.preview = true;
      render(data.report);
    } catch (error) { if (requestId === previewRequestId) console.warn('Could not update the field preview:', error.message); }
  }
  for (const id of ['whatif-fertilizer','whatif-pesticide','whatif-ph','whatif-rainfall','whatif-temperature','whatif-humidity']) $(id)?.addEventListener('input', () => {
    clearTimeout(previewTimer); previewTimer = setTimeout(preview, 350);
  });
  $('regenerate-advice')?.addEventListener('click', async () => {
    const button = $('regenerate-advice'); if (button) button.disabled = true;
    try {
      const response = await fetch(`/api/reports/${report.id}/regenerate`, {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({...whatIfValues(), language:$('advice-language').value})});
      const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Could not save updated advice.');
      render(data.report); history.replaceState({}, '', `/report/${report.id}`);
    } catch (error) { console.warn('Could not save updated advice:', error.message); }
    finally { if (button) button.disabled = false; }
  });
  $('copy-report-link')?.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(location.href); } catch (_) { /* link remains available in the address bar */ }
  });
  render(report);
  if (report && !report.analysis.what_if.scenarios['soil ph']) {
    fetch(`/api/reports/${report.id}/what-if`, {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify(whatIfValues())})
      .then((response) => response.ok ? response.json() : null)
      .then((data) => { if (data?.report) { data.report.preview = false; render(data.report); } })
      .catch((error) => console.warn('Could not refresh field preview curves:', error.message));
  }
})();
