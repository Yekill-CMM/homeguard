// HomeGuard AI — Service Worker
// Habilita: instalación PWA, cache offline, notificaciones push

const CACHE = 'homeguard-v7';
const OFFLINE_URLS = ['/mobile', '/static/manifest.json'];

// ─── Instalación ─────────────────────────────────────────────
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(OFFLINE_URLS))
  );
  self.skipWaiting();
});

// ─── Activación ──────────────────────────────────────────────
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ─── Fetch — network first, cache fallback ───────────────────
self.addEventListener('fetch', e => {
  // Solo cachear GET del mismo origen
  if (e.request.method !== 'GET') return;
  if (!e.request.url.startsWith(self.location.origin)) return;

  // API siempre desde red (datos en tiempo real)
  if (e.request.url.includes('/api/')) return;

  e.respondWith(
    fetch(e.request)
      .then(res => {
        const clone = res.clone();
        caches.open(CACHE).then(cache => cache.put(e.request, clone));
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});

// ─── Push notifications ───────────────────────────────────────
self.addEventListener('push', e => {
  if (!e.data) return;

  let data;
  try { data = e.data.json(); }
  catch { data = { title: 'HomeGuard AI', body: e.data.text() }; }

  const options = {
    body:    data.body || 'Nuevo evento de seguridad',
    icon:    '/static/icon-192.png',
    badge:   '/static/icon-192.png',
    tag:     data.tag || 'homeguard-alert',
    renotify: true,
    vibrate: [200, 100, 200, 100, 400],
    data:    { url: data.url || '/mobile', event_id: data.event_id },
    actions: [
      { action: 'view',    title: '👁 Ver evento' },
      { action: 'dismiss', title: '✕ Ignorar'     },
    ],
  };

  // Estilo según severidad
  if (data.severity === 'critical' || data.severity === 'high') {
    options.requireInteraction = true;  // No auto-cierra
    options.vibrate = [400, 100, 400, 100, 800];
  }

  e.waitUntil(
    self.registration.showNotification(data.title || '🚨 HomeGuard AI', options)
  );
});

// ─── Click en notificación ────────────────────────────────────
self.addEventListener('notificationclick', e => {
  e.notification.close();

  if (e.action === 'dismiss') return;

  const url = e.notification.data?.url || '/mobile';

  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then(clientList => {
        const existing = clientList.find(c => c.url.includes('/mobile'));
        if (existing) { existing.focus(); return; }
        return clients.openWindow(url);
      })
  );
});

// ── WEB PUSH HomeGuard AI ──────────────────────────────────
self.addEventListener('push', function(event) {
    if (!event.data) return;
    var data;
    try { data = event.data.json(); }
    catch(e) { data = {title:'HomeGuard AI', body:event.data.text(), data:{}}; }
    event.waitUntil(
        self.registration.showNotification(data.title || 'HomeGuard AI', {
            body:    data.body  || '',
            icon:    data.icon  || '/static/icon-192.png',
            badge:   '/static/icon-192.png',
            data:    data.data  || {},
            vibrate: [200,100,200]
        })
    );
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    var url = (event.notification.data && event.notification.data.url) ? event.notification.data.url : '/mobile';
    event.waitUntil(
        clients.matchAll({type:'window', includeUncontrolled:true}).then(function(list) {
            for (var i=0; i<list.length; i++) {
                if (list[i].url.indexOf('/mobile') !== -1 && 'focus' in list[i])
                    return list[i].focus();
            }
            if (clients.openWindow) return clients.openWindow(url);
        })
    );
});
