// Caches the app shell so the PWA opens even while Wi-Fi is off. Never caches /api.
const C = 'chromedrop-v2', SHELL = ['/', '/manifest.webmanifest', '/icon-192.png', '/icon-512.png'];
self.addEventListener('install', e => e.waitUntil(caches.open(C).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener('activate', e => e.waitUntil(
  caches.keys().then(ks => Promise.all(ks.filter(k => k !== C).map(k => caches.delete(k)))).then(() => self.clients.claim())));

async function takeShare(request) {
  const files = (await request.formData()).getAll('file').filter(f => f instanceof File);
  const c = await caches.open('chromedrop-share');
  for (const k of await c.keys()) await c.delete(k);
  for (const [i, f] of files.entries()) {
    await c.put('/shared/' + i, new Response(f, {
      headers: {
        'X-Name': encodeURIComponent(f.name), 'Content-Type': f.type || 'application/octet-stream'
      }
    }));
  }
  return Response.redirect(new URL('/?shared=' + files.length, location.origin).href, 303);
}

self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method === 'POST' && u.origin === location.origin && u.pathname === '/share-target') {
    e.respondWith(takeShare(e.request));
    return;
  }
  if (e.request.method !== 'GET' || u.origin !== location.origin || u.pathname.startsWith('/api/')) return;
  e.respondWith(fetch(e.request).then(r => { const c = r.clone(); caches.open(C).then(x => x.put(e.request, c)); return r; })
    .catch(() => caches.match(e.request)));
});
