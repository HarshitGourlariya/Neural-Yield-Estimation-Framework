# Public deployment note

The current `*.netlify.app` link returns Netlify's 404 because this repository is a Flask server-rendered app, not a static site with a root `index.html`. Netlify is serving the repository as static files, so it cannot execute `app.py`, the prediction service, weather routes, or report APIs.

Use the included `render.yaml` to deploy the complete Docker app as one HTTPS service:

1. Push the project to a GitHub repository (do not commit `.env`, API keys, or local database files).
2. In Render, create a Blueprint from that repository and confirm the `greenyield` web service and persistent disk.
3. Wait for the deploy health check to pass at `/health`. Render supplies a public HTTPS `onrender.com` URL.
4. Set `GEMINI_API_KEY` in the Render service environment only if you want AI summaries. Redeploy after setting it.
5. Open the Render URL in a phone browser and install it from Chrome (Android) or Safari (iPhone). Use this Render URL as the farmer link. Netlify's old URL will remain a 404 unless it is separately configured as a proxy to this backend.

The persistent disk is mounted at `/var/data`; the source CSVs and model remain under `/app/data` and `/app/models`, so mounting report storage does not hide the app's bundled inputs. Keep one web instance for this SQLite-based report store.

The Flask API and pages share the same origin, so this setup does not require permissive CORS. The `/health` endpoint is the host health check; basic request throttling and security response headers are enabled in Flask. For higher traffic or multiple instances, move reports and request throttling to shared database/cache services.
