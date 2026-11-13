importScripts('/static/model/inference.js');
let modelPromise;
function getModel() {
  if (!modelPromise) modelPromise = fetch('/static/model/yield-model.json').then((response) => {
    if (!response.ok) throw new Error('On-device estimate data is unavailable. Reopen GreenYield while online once.');
    return response.json();
  });
  return modelPromise;
}
self.addEventListener('message', async (event) => {
  const {id, input} = event.data || {};
  try { const model = await getModel(); self.postMessage({id, result: GreenYieldInference.predict(model, input)}); }
  catch (error) { self.postMessage({id, error: error.message || 'Could not estimate this field.'}); }
});
