# GreenYield PWA deployment

GreenYield is a Flask application with a plain HTML/CSS/JavaScript interface. This change adds an installable PWA and offline prediction/report path without replacing the existing backend.

## Model and execution

The existing estimator is a scikit-learn `HistGradientBoostingRegressor` pipeline trained on `log1p(Yield)`. Its pipeline applies median imputation and ordinal category encoding; prediction inputs include State, District, Crop, Season, Soil Type, Temperature, Rainfall, Humidity, pH, Fertilizer and Pesticide. The Python prediction wrapper adds the existing physical stress factors and conformal interval. The server still handles online reports, weather, AI narrative, validation, and ESP32 Mode B. A new exporter writes the already-fitted estimator and preprocessing metadata to `static/model/yield-model.json`; a dependency-free JavaScript port runs the same bins/trees and output transforms inside a Web Worker. The service worker caches that model.

The automated parity script compares 128 real training-row samples plus six extreme cases (134 total). Latest result: max point-yield difference `2.27e-13 kg/ha`; max interval-endpoint difference `4.55e-13 kg/ha` (floating point roundoff). Run `python test_browser_model_parity.py` after changing the Python artifact or exporter.

## Install and offline behavior

Serve the app at the root of an HTTPS origin (localhost is allowed for development). Open it online once so the service worker can cache the shell/model and the app can cache district/crop metadata. Install from Android Chrome's install prompt; in iPhone Safari use Share → Add to Home Screen. The model is about 405 KB; the bundled Devanagari font is about 642 KB. Total static assets are roughly 1.16 MB before transfer compression.

Offline, the browser model can estimate yield and range, and the offline report page renders risk guidance, farmer-facing actions, what-if controls and charts. The report page can create a raster PDF locally; supported browsers can share the PDF file. Reports are stored in IndexedDB, and successful online reports are copied to the device after creation. The explicit “Sync saved reports” action uploads local reports to the server. Use the sensor panel's JSON import for ESP32 Mode A readings. Direct PWA-to-hotspot live access is intentionally absent; Mode B via the Flask server works online. Web Bluetooth/Serial is not implemented.

Weather is never bundled: offline district changes leave weather empty for manual entry. Previously entered values are preserved if connectivity changes; only an explicit online-fill action requests live weather.

## Run locally

```powershell
python -m pip install -r requirements.txt
gunicorn --bind 127.0.0.1:5000 --workers 1 --threads 4 app:app
```

Or use Docker:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

The app needs the checked-in model, report JSON and source data folders. Keep `data/` on a persistent volume because it contains the shareable-report SQLite database. Set AI provider keys only in the server environment; no API key is included in browser assets. Keep a single application worker while using the current SQLite report store unless you move it to a shared database.

## Hosting and QR

Recommended simple pilot: deploy this Docker image as a Railway service with a persistent volume mounted at `/app/data`, expose its HTTPS domain, set the variables from `.env.example`, and test `/health`. Railway currently lists Hobby at $5/month minimum usage (usage above included credits can cost more); custom domains cost separately. Render offers a free tier for experiments, but a paid instance is preferable for a responsive farmer-facing service; check its live pricing before launch. Cloudflare Pages is not a fit for this Flask/Python backend as a static-only host. Sources: [Railway pricing](https://railway.com/pricing), [Render pricing](https://render.com/pricing), [Cloudflare Pages](https://developers.cloudflare.com/pages/).

After deployment, generate a QR code for the final HTTPS URL (example: `python -m pip install qrcode[pil]` then `python -c "import qrcode; qrcode.make('https://YOUR-DOMAIN').save('greenyield-qr.png')"`). The final farmer link cannot be supplied until the owner creates the hosting account/domain and deploys the service.

## Remaining verification

The central translation dictionary covers the current form/PWA chrome and report-specific translations, but should be reviewed by a fluent Hindi speaker; location and crop names remain in their common official names. The offline PDF is image-based (its text is not selectable). Persistent-storage permission is requested but the browser decides whether to grant it. Real Android/iPhone install and airplane-mode checks, Lighthouse PWA/HTTPS scores, low-memory timing, a deployed URL, and ESP32 hardware checks require the deployment and devices. See README_DEPLOY.md for the Flask hosting steps; Netlify static hosting alone does not run this backend.
