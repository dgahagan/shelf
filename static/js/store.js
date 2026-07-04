/* Store Mode — offline "do I own this?" checks. See docs/plans/PWA_STORE_MODE.md.
 *
 * Library data lives in localStorage (fetched from /api/store/data whenever
 * online); scans are matched against it entirely client-side. Unknown scans
 * queue in localStorage and flush to /api/store/queue when back online.
 */
(function () {
    'use strict';

    var DATA_KEY = 'shelf-store-data';
    var QUEUE_KEY = 'shelf-store-queue';

    var index = {};            // normalized code -> {title, authors, owned}
    var dataMeta = { generated_at: null, count: 0 };
    var scanner = null;
    var lastScan = { code: null, at: 0 };
    var deferredInstall = null;

    function $(id) { return document.getElementById(id); }

    function csrfToken() {
        var m = document.cookie.split('; ').find(function (r) { return r.indexOf('csrf_token=') === 0; });
        return m ? decodeURIComponent(m.split('=')[1]) : '';
    }

    function normalizeCode(raw) {
        return (raw || '').toUpperCase().replace(/[^0-9X]/g, '');
    }

    // --- Data cache -------------------------------------------------------

    function buildIndex(data) {
        index = {};
        (data.items || []).forEach(function (item) {
            (item.codes || []).forEach(function (code) {
                index[code] = item;
            });
        });
        dataMeta = { generated_at: data.generated_at, count: data.count || 0 };
    }

    function loadLocalData() {
        try {
            var raw = localStorage.getItem(DATA_KEY);
            if (raw) buildIndex(JSON.parse(raw));
        } catch (e) { /* corrupted cache — refresh will replace it */ }
    }

    function refreshData() {
        if (!navigator.onLine) return Promise.resolve();
        return fetch('/api/store/data', { headers: { 'Accept': 'application/json' } })
            .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
            .then(function (data) {
                localStorage.setItem(DATA_KEY, JSON.stringify(data));
                buildIndex(data);
                updateStatus();
            })
            .catch(function () { updateStatus(); });
    }

    // --- Queue ------------------------------------------------------------

    function getQueue() {
        try { return JSON.parse(localStorage.getItem(QUEUE_KEY)) || []; }
        catch (e) { return []; }
    }

    function setQueue(q) {
        localStorage.setItem(QUEUE_KEY, JSON.stringify(q));
        renderQueue();
    }

    function isQueued(code) {
        return getQueue().some(function (e) { return e.isbn === code; });
    }

    function enqueue(code) {
        var q = getQueue();
        if (q.some(function (e) { return e.isbn === code; })) return;
        q.push({ isbn: code, at: new Date().toISOString() });
        setQueue(q);
    }

    function flushQueue() {
        var q = getQueue();
        if (!q.length || !navigator.onLine) return Promise.resolve();
        var isbns = q.map(function (e) { return e.isbn; });
        $('sync-hint').classList.add('hidden');
        return fetch('/api/store/queue', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
            body: JSON.stringify({ isbns: isbns.slice(0, 50) }),
        }).then(function (r) {
            if (r.status === 401 || r.status === 403) {
                var hint = $('sync-hint');
                hint.textContent = 'Sign in to Shelf to sync your queued scans.';
                hint.classList.remove('hidden');
                throw new Error('auth');
            }
            if (!r.ok) throw new Error(r.status);
            return r.json();
        }).then(function (data) {
            var handled = {};
            (data.results || []).forEach(function (res) {
                handled[normalizeCode(res.isbn)] = true;
                // Make freshly wishlisted books match on an immediate re-scan
                if (res.status === 'wishlisted' || res.status === 'added_bare' || res.status === 'duplicate') {
                    index[normalizeCode(res.isbn)] = {
                        title: res.title || ('ISBN ' + res.isbn),
                        authors: null,
                        owned: res.status === 'duplicate',
                    };
                }
            });
            setQueue(getQueue().filter(function (e) { return !handled[normalizeCode(e.isbn)]; }));
            updateStatus();
        }).catch(function () { renderQueue(); });
    }

    function renderQueue() {
        var q = getQueue();
        $('queue-section').classList.toggle('hidden', q.length === 0);
        $('queue-count').textContent = q.length;
        var list = $('queue-list');
        list.textContent = '';
        q.forEach(function (e) {
            var li = document.createElement('li');
            li.textContent = e.isbn;
            list.appendChild(li);
        });
    }

    // --- Verdict ----------------------------------------------------------

    function showVerdict(rawCode) {
        var code = normalizeCode(rawCode);
        if (!code) return;

        var card = $('verdict');
        var label = $('verdict-label');
        var title = $('verdict-title');
        var authors = $('verdict-authors');
        var note = $('verdict-note');

        card.classList.remove('hidden', 'border-shelf-success', 'border-shelf-warning', 'border-shelf-border',
                              'bg-shelf-success/10', 'bg-shelf-warning/10', 'bg-shelf-card');
        label.classList.remove('text-shelf-success', 'text-shelf-warning', 'text-shelf-muted');
        title.textContent = ''; authors.textContent = ''; note.textContent = '';

        var hit = index[code];
        if (hit && hit.owned) {
            card.classList.add('border-shelf-success', 'bg-shelf-success/10');
            label.classList.add('text-shelf-success');
            label.textContent = 'OWNED';
            title.textContent = hit.title;
            authors.textContent = hit.authors || '';
        } else if (hit) {
            card.classList.add('border-shelf-warning', 'bg-shelf-warning/10');
            label.classList.add('text-shelf-warning');
            label.textContent = 'ON WISHLIST';
            title.textContent = hit.title;
            authors.textContent = hit.authors || '';
        } else {
            card.classList.add('border-shelf-border', 'bg-shelf-card');
            label.classList.add('text-shelf-muted');
            label.textContent = 'NOT IN LIBRARY';
            title.textContent = 'ISBN ' + code;
            if (isQueued(code)) {
                note.textContent = 'Already queued for your wishlist.';
            } else {
                enqueue(code);
                note.textContent = navigator.onLine
                    ? 'Added to sync queue — syncing now…'
                    : 'Queued — will be added to your wishlist when back online.';
                if (navigator.onLine) flushQueue();
            }
        }
    }

    // --- Camera -----------------------------------------------------------

    function toggleCamera() {
        var btn = $('camera-toggle');
        var readerEl = $('reader');
        if (scanner) {
            scanner.stop().catch(function () {}).then(function () {
                scanner = null;
                readerEl.classList.add('hidden');
                btn.textContent = 'Scan with Camera';
            });
            return;
        }
        readerEl.classList.remove('hidden');
        btn.textContent = 'Stop Camera';
        scanner = new Html5Qrcode('reader');
        scanner.start(
            { facingMode: 'environment' },
            { fps: 10, qrbox: { width: 240, height: 140 } },
            function (decoded) {
                var now = Date.now();
                if (decoded === lastScan.code && now - lastScan.at < 3000) return;
                lastScan = { code: decoded, at: now };
                if (navigator.vibrate) navigator.vibrate(60);
                showVerdict(decoded);
            },
            function () { /* per-frame decode misses are normal */ }
        ).catch(function () {
            readerEl.classList.add('hidden');
            btn.textContent = 'Scan with Camera';
            scanner = null;
            var note = $('verdict-note');
            $('verdict').classList.remove('hidden');
            note.textContent = 'Camera unavailable — use manual entry.';
        });
    }

    // --- PWA install ------------------------------------------------------
    // Chrome never reliably shows its own install banner; capture the event
    // and surface a button instead. Browsers without the event (iOS Safari,
    // already-installed, untrusted origin) simply never reveal the button.

    window.addEventListener('beforeinstallprompt', function (e) {
        e.preventDefault();
        deferredInstall = e;
        var btn = $('install-app');
        if (btn) btn.classList.remove('hidden');
    });

    window.addEventListener('appinstalled', function () {
        deferredInstall = null;
        var btn = $('install-app');
        if (btn) btn.classList.add('hidden');
    });

    function promptInstall() {
        if (!deferredInstall) return;
        var evt = deferredInstall;
        deferredInstall = null;
        $('install-app').classList.add('hidden');
        evt.prompt();
    }

    // --- Status -----------------------------------------------------------

    function updateStatus() {
        var online = navigator.onLine;
        var dot = $('net-dot');
        dot.classList.toggle('bg-shelf-success', online);
        dot.classList.toggle('bg-shelf-error', !online);
        dot.classList.remove('bg-shelf-muted');

        var parts = [];
        parts.push(dataMeta.count > 0 ? dataMeta.count + ' titles cached' : 'No library data cached yet');
        if (dataMeta.generated_at) {
            parts.push('synced ' + new Date(dataMeta.generated_at).toLocaleString());
        }
        parts.push(online ? 'online' : 'offline');
        $('status-line').textContent = parts.join(' · ');
        renderQueue();
    }

    // --- Init -------------------------------------------------------------

    document.addEventListener('DOMContentLoaded', function () {
        loadLocalData();
        updateStatus();

        $('manual-form').addEventListener('submit', function (e) {
            e.preventDefault();
            var v = $('isbn-input').value;
            if (v.trim()) { showVerdict(v); $('isbn-input').value = ''; }
        });
        $('camera-toggle').addEventListener('click', toggleCamera);
        $('sync-now').addEventListener('click', function () { flushQueue(); });
        $('install-app').addEventListener('click', promptInstall);

        window.addEventListener('online', function () { updateStatus(); flushQueue().then(refreshData); });
        window.addEventListener('offline', updateStatus);

        flushQueue().then(refreshData, refreshData);

        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/sw.js').catch(function () {
                // Untrusted cert or unsupported browser — page still works online
            });
        }
    });
})();
