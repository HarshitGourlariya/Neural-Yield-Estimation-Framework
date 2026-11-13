/* GreenYield offline shell. Network-first pages, versioned immutable assets. */
const VERSION = 'greenyield-pwa-v5';
const SHELL = `${VERSION}-shell`;
const DATA = `${VERSION}-data`;
const PRECACHE = [
  '/', '/offline-report.html', '/manifest.webmanifest', '/static/styles.css', '/static/app.js',
  '/static/pwa.js', '/static/i18n.js', '/static/report.js', '/static/offline-report.js', '/static/model/inference.js',
  '/static/model/worker.js', '/static/model/yield-model.json', '/static/icons/icon-192.png',
  '/static/icons/icon-512.png', '/static/fonts/NotoSansDevanagari.ttf',
];
self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key.startsWith('greenyield-') && key !== SHELL && key !== DATA).map((key) => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('message', (event) => { if (event.data === 'SKIP_WAITING') self.skipWaiting(); });
self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.method !== 'GET') return;
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).then((response) => {
      if (response.ok) caches.open(SHELL).then((cache) => cache.put(request, response.clone()));
      return response;
    }).catch(async () => {
      const exact = await caches.match(request);
      if (exact) return exact;
      if (/\/report\//.test(url.pathname)) return (await caches.match('/offline-report.html')) || Response.error();
      return (await caches.match('/')) || new Response('GreenYield is offline. Open it once while connected to prepare offline use.', {status:503,headers:{'Content-Type':'text/plain; charset=utf-8'}});
    }));
    return;
  }
  if (url.pathname.startsWith('/api/')) {
    // API responses are deliberately not cached as current weather or reports.
    event.respondWith(fetch(request).catch(() => new Response(JSON.stringify({error:'You are offline. Enter the values manually; your local estimate is still available.'}), {status:503,headers:{'Content-Type':'application/json'}})));
    return;
  }
  if (request.destination === 'script' || request.destination === 'style' || request.destination === 'font' || request.destination === 'image' || url.pathname.endsWith('.webmanifest')) {
    event.respondWith(caches.match(request).then((cached) => {
      const update = fetch(request).then((response) => { if (response.ok) caches.open(SHELL).then((cache) => cache.put(request, response.clone())); return response; }).catch(() => null);
      return cached || update || new Response('', {status:504});
    }));
  }
});
