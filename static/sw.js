/* Shelf service worker — offline support for /store only.
 *
 * Strategy:
 *  - Precache the store-mode app shell at install.
 *  - Cache-first for precached static assets (they're version-stamped by
 *    CACHE name).
 *  - Network-first with cache fallback for /store navigations, so the page
 *    stays fresh online but still loads with no signal.
 *  - Everything else passes through untouched: the service worker must never
 *    interfere with the main app or its API calls.
 *
 * Position-independent (#165): Shelf may be served under a sub-path, and the
 * worker's scope is then that sub-path. Every URL is resolved against the
 * scope; PRECACHE keeps its root-path spelling because it is the stamp input.
 */
// GENERATED — do not edit by hand. SW_VERSION is `v` + the first 8 hex chars
// of a sha256 over the PRECACHE paths and their bytes, stamped by `make css`
// (scripts/stamp_sw_version.py) and verified by `make checks-fast`. Changing a
// precached file therefore renames the cache on its own, which is what makes
// activate() purge the stale one.
const SW_VERSION = 'v942b8f23';
const CACHE = `shelf-store-${SW_VERSION}`;

const PRECACHE = [
    '/static/css/app.css',
    '/static/js/store.js',
    '/static/js/shelf-url.js',
    '/static/js/scanner-engine.js',
    '/static/vendor/html5-qrcode-2.3.8.min.js',
    '/static/vendor/zxing-browser-0.1.5.min.js',
    '/static/manifest.webmanifest',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
];

// The worker's own shelfUrl: a root path resolved against the scope it was
// registered with — `<origin><base>/` — so it is right at any sub-path.
function shelfUrl(path) {
    return new URL(path.slice(1), self.registration.scope).href;
}

const PRECACHE_URLS = PRECACHE.map(shelfUrl);
const STORE_URL = shelfUrl('/store');

self.addEventListener('install', function (event) {
    event.waitUntil(
        caches.open(CACHE).then(function (cache) {
            // The store page is cached separately: an unauthenticated fetch
            // redirects to the login page, and caching THAT would break offline mode.
            const storePage = fetch(STORE_URL).then(function (resp) {
                if (resp.ok && !resp.redirected) return cache.put(STORE_URL, resp);
            }).catch(function () { /* offline install — runtime handler will fill it */ });
            return Promise.all([cache.addAll(PRECACHE_URLS), storePage]);
        }).then(function () { return self.skipWaiting(); })
    );
});

self.addEventListener('activate', function (event) {
    event.waitUntil(
        caches.keys().then(function (names) {
            // Only Shelf's own stale caches. Under a sub-path the origin is shared
            // with other apps, and their Cache Storage is not ours to delete.
            return Promise.all(
                names.filter(function (n) { return n.indexOf('shelf-store-') === 0 && n !== CACHE; })
                     .map(function (n) { return caches.delete(n); })
            );
        }).then(function () { return self.clients.claim(); })
    );
});

self.addEventListener('fetch', function (event) {
    const url = new URL(event.request.url);
    if (url.origin !== self.location.origin || event.request.method !== 'GET') return;
    const bare = url.origin + url.pathname;

    // Store page navigation: network-first, cache fallback
    if (bare === STORE_URL) {
        event.respondWith(
            fetch(event.request).then(function (resp) {
                // Never cache a redirected response (auth redirect = login page)
                if (resp.ok && !resp.redirected) {
                    const copy = resp.clone();
                    caches.open(CACHE).then(function (c) { c.put(STORE_URL, copy); });
                }
                return resp;
            }).catch(function () {
                return caches.match(STORE_URL);
            })
        );
        return;
    }

    // Precached static assets: cache-first
    if (PRECACHE_URLS.indexOf(bare) !== -1) {
        event.respondWith(
            caches.match(bare).then(function (hit) {
                return hit || fetch(event.request);
            })
        );
    }
    // Anything else: no respondWith — browser handles it normally.
});
