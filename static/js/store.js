/* Store Mode — offline "do I own this?" checks. See .devdocs/archive/completed/PWA_STORE_MODE.md.
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
    var currentCode = null;    // the code the verdict card is showing

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

    // One flush in flight at a time. A caller that arrives while it is out
    // gets the same promise; a scan made mid-flight is enqueued and picked up
    // by the next round. The drain stops on an empty queue, a failed round,
    // or a round that removed nothing (so a code the server answers but the
    // client cannot match never loops).
    var inflight = null;

    function flushQueue() {
        if (inflight) return inflight;
        if (!getQueue().length || !navigator.onLine) return Promise.resolve();
        $('sync-hint').classList.add('hidden');

        var totals = { unreadable: 0, restored: 0, rounds: 0 };
        var settle;
        inflight = new Promise(function (resolve) { settle = resolve; });

        function stop() {
            // Cleared here, in the callback that decides to stop, so a scan
            // event never sees a settled promise still parked in `inflight`.
            inflight = null;
            if (totals.rounds) {
                showSyncResult(totals.unreadable, totals.restored);
                updateStatus();
            } else {
                renderQueue();
            }
            settle();
        }

        function round() {
            var q = getQueue();
            if (!q.length || !navigator.onLine) { stop(); return; }
            flushRound(q.slice(0, 50), totals).then(function (removed) {
                totals.rounds++;
                if (removed > 0 && getQueue().length) round();
                else stop();
            }, stop);
        }

        round();
        return inflight;
    }

    // Send one batch and index what the server says about each code. The
    // server echoes the string sent as `code` and the row's real `owned` on
    // every result, so there is no per-status branch here: every answered
    // code is indexed under what was sent and under its ISBN-13, and dropped
    // from the queue by what was sent. A failed round throws before anything
    // is written (G137), so the index only ever holds answers.
    function flushRound(batch, totals) {
        var isbns = batch.map(function (e) { return e.isbn; });
        return fetch('/api/store/queue', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
            body: JSON.stringify({ isbns: isbns }),
        }).then(function (r) {
            if (r.status === 401 || r.status === 403) {
                var hint = $('sync-hint');
                return r.json().catch(function () { return null; }).then(function (body) {
                    hint.textContent = (r.status === 403 && body && body.error === 'feature_disabled')
                        ? 'Store Mode is turned off on this Shelf. Your queued scans are kept.'
                        : 'Sign in to Shelf to sync your queued scans.';
                    hint.classList.remove('hidden');
                    throw new Error('auth');
                });
            }
            if (!r.ok) throw new Error(r.status);
            return r.json();
        }).then(function (data) {
            var handled = {};
            (data.results || []).forEach(function (res) {
                if (res.status === 'unreadable') totals.unreadable++;
                if (res.status === 'restored') totals.restored++;
                var key = normalizeCode(res.code || res.isbn);
                var entry = {
                    title: res.title || ('ISBN ' + res.isbn),
                    authors: null,
                    owned: !!res.owned,
                };
                handled[key] = true;
                if (key) index[key] = entry;
                var isbnKey = normalizeCode(res.isbn);
                if (isbnKey) index[isbnKey] = entry;
            });
            var before = getQueue();
            var after = before.filter(function (e) { return !handled[normalizeCode(e.isbn)]; });
            setQueue(after);
            // The card still showing a code this round answered now shows
            // the server's answer. A card moved on to another code is left
            // alone (G39: prove ownership before writing).
            if (currentCode && handled[currentCode] && index[currentCode]) {
                renderVerdict(currentCode);
            }
            return before.length - after.length;
        });
    }

    // A barcode that failed its check digit is still saved, as a titled
    // wishlist row with no ISBN. Say so: the queue block that would otherwise
    // carry the news hides itself the moment the flush empties it.
    function showSyncResult(unreadable, restored) {
        var el = $('sync-result');
        if (!el) return;
        var parts = [];
        if (unreadable) {
            parts.push(unreadable === 1
                ? "1 barcode didn't scan cleanly — saved to your wishlist to fix later."
                : unreadable + " barcodes didn't scan cleanly — saved to your wishlist to fix later.");
        }
        if (restored) {
            parts.push(restored === 1
                ? '1 restored from Trash.'
                : restored + ' restored from Trash.');
        }
        if (!parts.length) { el.classList.add('hidden'); el.textContent = ''; return; }
        el.textContent = parts.join(' ');
        el.classList.remove('hidden');
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
        currentCode = code;
        if (renderVerdict(code)) return;

        var note = $('verdict-note');
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

    // Paint the card for `code` from the index. No queueing, no flush —
    // safe to call again when a flush answers the code on screen. Returns
    // whether the code is in the library.
    function renderVerdict(code) {
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
        }
        return !!hit;
    }

    // --- Camera -----------------------------------------------------------

    function toggleCamera() {
        var btn = $('camera-toggle');
        var readerEl = $('reader');
        var zxingEl = $('store-zxing-container');
        if (scanner) {
            scanner.stop().catch(function () {}).then(function () {
                scanner = null;
                readerEl.classList.add('hidden');
                zxingEl.classList.add('hidden');
                btn.textContent = 'Scan with Camera';
            });
            return;
        }
        scanner = window.createBarcodeScanner({
            html5ElId: 'reader',
            videoEl: 'store-zxing-video',
            html5Config: { fps: 10, qrbox: { width: 240, height: 140 } },
            onDecode: function (decoded) {
                var now = Date.now();
                if (decoded === lastScan.code && now - lastScan.at < 3000) return;
                lastScan = { code: decoded, at: now };
                if (navigator.vibrate) navigator.vibrate(60);
                showVerdict(decoded);
            }
        });
        var activeEl = scanner.engine === 'zxing' ? zxingEl : readerEl;
        activeEl.classList.remove('hidden');
        btn.textContent = 'Stop Camera';
        scanner.start().catch(function () {
            readerEl.classList.add('hidden');
            zxingEl.classList.add('hidden');
            btn.textContent = 'Scan with Camera';
            scanner = null;
            var note = $('verdict-note');
            var card = $('verdict');
            card.classList.remove('hidden');
            card.classList.add('border-shelf-border');
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
