// Stove Pilot UI service worker: offline shell only, it never caches live stove data.
const CACHE_PREFIX = "stove-pilot-ui-";
const CACHE_NAME = CACHE_PREFIX + "{{ asset_version }}";
const OFFLINE_URL = "{{ offline_url }}";
const PRECACHE = {{ precache | tojson }};
const PRECACHE_SET = new Set(PRECACHE);
const BYPASS_PATHS = new Set({{ bypass_paths | tojson }});

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(PRECACHE.map((url) => new Request(url, { cache: "reload" }))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET" || request.headers.has("HX-Request")) {
    return;
  }
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || BYPASS_PATHS.has(url.pathname)) {
    return;
  }
  if (PRECACHE_SET.has(url.pathname)) {
    event.respondWith(caches.match(url.pathname).then((cached) => cached || fetch(request)));
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
  }
});
