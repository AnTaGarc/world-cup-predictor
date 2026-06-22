// Minimal service worker — Chrome requires a registered SW with a fetch
// handler for PWA install eligibility. We don't cache anything (Streamlit's
// content is dynamic) but the handler must exist and be non-empty.
self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  // Pass-through. The presence of this handler is what matters for the
  // installability criteria; we don't intercept or cache anything.
  return;
});
