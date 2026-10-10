// Makes a static host cross-origin isolated, which the emulator's threads need
// (SharedArrayBuffer): a service worker re-serves every response with COOP and
// COEP headers. Hosts that already send them (tools/serve_web.py) skip it.
if (typeof window === 'undefined') {
  self.addEventListener('install', () => self.skipWaiting());
  self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));
  self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.cache === 'only-if-cached' && request.mode !== 'same-origin') return;
    event.respondWith(fetch(request).then((response) => {
      if (response.status === 0) return response;
      const headers = new Headers(response.headers);
      headers.set('Cross-Origin-Embedder-Policy', 'require-corp');
      headers.set('Cross-Origin-Opener-Policy', 'same-origin');
      return new Response(response.body, { status: response.status, statusText: response.statusText, headers });
    }));
  });
} else if (!window.crossOriginIsolated && window.isSecureContext && 'serviceWorker' in navigator) {
  navigator.serviceWorker.register(document.currentScript.src).then((registration) => {
    const reload = () => window.location.reload();
    if (registration.active && !navigator.serviceWorker.controller) {
      reload();
    } else {
      registration.addEventListener('updatefound', () => {
        registration.installing.addEventListener('statechange', (event) => {
          if (event.target.state === 'activated') reload();
        });
      });
    }
  });
}
