/* Shelf service worker — offline support for /store only.
 *
 * Strategy:
 *  - Precache the store-mode app shell at install.
 *  - Cache-first for precached static assets (they're version-stamped by
 *    CACHE name — bump SW_VERSION when any precached file changes).
 *  - Network-first with cache fallback for /store navigations, so the page
 *    stays fresh online but still loads with no signal.
 *  - Everything else passes through untouched: the service worker must never
 *    interfere with the main app or its API calls.
 */
const SW_VERSION = 'v2';
const CACHE = `shelf-store-${SW_VERSION}`;

const PRECACHE = [
    '/static/css/app.css',
    '/static/js/store.js',
    '/static/vendor/html5-qrcode-2.3.8.min.js',
    '/static/manifest.webmanifest',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
];

self.addEventListener('install', function (event) {
    event.waitUntil(
        caches.open(CACHE).then(function (cache) {
            // /store is cached separately: an unauthenticated fetch redirects
            // to /login, and caching THAT as /store would break offline mode.
            const storePage = fetch('/store').then(function (resp) {
                if (resp.ok && !resp.redirected) return cache.put('/store', resp);
            }).catch(function () { /* offline install — runtime handler will fill it */ });
            return Promise.all([cache.addAll(PRECACHE), storePage]);
        }).then(function () { return self.skipWaiting(); })
    );
});

self.addEventListener('activate', function (event) {
    event.waitUntil(
        caches.keys().then(function (names) {
            return Promise.all(
                names.filter(function (n) { return n !== CACHE; })
                     .map(function (n) { return caches.delete(n); })
            );
        }).then(function () { return self.clients.claim(); })
    );
});

self.addEventListener('fetch', function (event) {
    const url = new URL(event.request.url);
    if (url.origin !== self.location.origin || event.request.method !== 'GET') return;

    // /store navigation: network-first, cache fallback
    if (url.pathname === '/store') {
        event.respondWith(
            fetch(event.request).then(function (resp) {
                // Never cache a redirected response (auth redirect = login page)
                if (resp.ok && !resp.redirected) {
                    const copy = resp.clone();
                    caches.open(CACHE).then(function (c) { c.put('/store', copy); });
                }
                return resp;
            }).catch(function () {
                return caches.match('/store');
            })
        );
        return;
    }

    // Precached static assets: cache-first
    if (PRECACHE.indexOf(url.pathname) !== -1) {
        event.respondWith(
            caches.match(url.pathname).then(function (hit) {
                return hit || fetch(event.request);
            })
        );
    }
    // Anything else: no respondWith — browser handles it normally.
});
