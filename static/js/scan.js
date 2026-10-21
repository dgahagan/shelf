function scanPage() {
    return {
        mode: localStorage.getItem('shelf_scan_mode') || 'add',
        // Auto for a *new* user. A stored value is deliberately never
        // migrated: "book" is also what someone who scans books chose, and
        // reinterpreting that as "no choice" is guessing at intent. The
        // barcode rule (§1) is what reaches those users instead. The one
        // exception is `kids_book`: that option no longer exists, so a
        // device whose cache still holds it is normalized to `book` here
        // and the stored value corrected, not merely reinterpreted.
        mediaType: (function () {
            var v = localStorage.getItem('shelf_media_type') || 'auto';
            if (v === 'kids_book') {
                v = 'book';
                localStorage.setItem('shelf_media_type', v);
            }
            return v;
        })(),
        platform: localStorage.getItem('shelf_platform') || '',
        location: localStorage.getItem('shelf_location') || '',
        readingStatus: (function () {
            var v = localStorage.getItem('shelf_reading_status') || '';
            return ['', 'want_to_read', 'reading', 'read'].indexOf(v) === -1 ? '' : v;
        })(),
        // The default-status words for the current media type, and whether
        // that type carries a status at all (refreshStatusLabels).
        statusLabelSets: {},
        statusWant: 'Want to',
        statusDoing: 'In progress',
        statusDone: 'Finished',
        statusTypeOk: true,
        statusDisabled: false,
        borrowerId: '',
        cameraActive: false,
        scanPaused: false,
        scanLoading: false,
        scanResult: false,
        scanner: false,
        lastScanned: '',
        lastScanTime: 0,
        // The open audit session's id ('' when none), mirrored to
        // localStorage so a reload resumes it. The strip is the authority:
        // syncAuditFromStrip() overwrites this from every fragment it renders.
        auditId: (function() {
            try { return localStorage.getItem('shelf_audit_id') || ''; } catch (e) { return ''; }
        })(),
        isZxingFallback: false,
        manualOpen: false,
        recentScansRequest: 0,

        modes: [
            {id: 'add', label: 'Add'},
            {id: 'wishlist', label: 'Wishlist'},
            {id: 'lend', label: 'Lend'},
            {id: 'return', label: 'Return'},
            {id: 'move', label: 'Move'},
            {id: 'inventory', label: 'Inventory'},
            {id: 'lookup', label: 'Lookup'},
            {id: 'quick_rate', label: 'Quick Rate'},
        ],

        get modeConfig() {
            var configs = {
                add: {heading: 'Add Items', description: 'Scan barcodes to add items to your collection.'},
                wishlist: {heading: 'Add to Wishlist', description: 'Scan to save items you want but haven\'t bought yet.'},
                lend: {heading: 'Lend Items', description: 'Select a borrower, then scan items to check them out.'},
                'return': {heading: 'Return Items', description: 'Scan items to check them back in.'},
                move: {heading: 'Move Items', description: 'Select a target location, then scan items to move them.'},
                inventory: {heading: 'Inventory Audit', description: 'Select a location and scan every item you find there. Start an audit to get a report.'},
                lookup: {heading: 'Lookup', description: 'Scan to check if an item is in your collection. No changes are made.'},
                quick_rate: {heading: 'Quick Rate', description: 'Scan to mark items as read or completed.'},
            };
            return configs[this.mode] || configs.add;
        },

        loadRecentScans(m) {
            var self = this;
            var request = ++this.recentScansRequest;
            var results = document.getElementById('scan-results');
            var firstResult = results.firstChild;
            fetch('/api/recent-scans?mode=' + encodeURIComponent(m))
                .then(function(r) {
                    if (!r.ok) throw new Error('HTTP ' + r.status);
                    return r.text();
                })
                .then(function(html) {
                    // A newer history load or an inserted scan card makes this snapshot stale.
                    if (request === self.recentScansRequest && results.firstChild === firstResult) {
                        results.innerHTML = html;
                        htmx.process(results);
                    }
                })
                .catch(function() {
                    showToast('Could not load recent scans', 'error');
                });
        },

        toggleManual() {
            this.manualOpen = !this.manualOpen;
        },

        setMode(m) {
            this.mode = m;
            localStorage.setItem('shelf_scan_mode', m);
            this.loadRecentScans(m);
            if (m === 'inventory') this.refreshAudit();
            var si = document.getElementById('title-search-input');
            if (si) si.value = '';
            var sr = document.getElementById('title-search-results');
            if (sr) sr.innerHTML = '';
        },

        // @change handlers (CSP build: no localStorage/document in templates)
        persistMediaType() {
            localStorage.setItem('shelf_media_type', this.mediaType);
            var si = document.getElementById('title-search-input');
            if (si) si.value = '';
            var sr = document.getElementById('title-search-results');
            if (sr) sr.innerHTML = '';
        },

        persistLocation() {
            localStorage.setItem('shelf_location', this.location);
        },

        persistPlatform() {
            localStorage.setItem('shelf_platform', this.platform);
        },

        persistReadingStatus() {
            localStorage.setItem('shelf_reading_status', this.readingStatus);
        },

        // Auto gets the neutral words; a type outside the map has no status,
        // so the control is disabled and keeps the neutral words.
        refreshStatusLabels() {
            var sets = this.statusLabelSets;
            var words = sets[this.mediaType] || sets.auto ||
                ['Want to', 'In progress', 'Finished'];
            this.statusTypeOk = this.mediaType === 'auto' || !!sets[this.mediaType];
            if (!this.statusTypeOk) words = sets.auto || ['Want to', 'In progress', 'Finished'];
            this.statusWant = words[0];
            this.statusDoing = words[1];
            this.statusDone = words[2];
            this.statusDisabled = (this.mode !== 'add' && this.mode !== 'wishlist') || !this.statusTypeOk;
        },

        // Client-side validation before form submit
        init() {
            var self = this;

            // Lending off (or any feature whose scan_modes the registry
            // disabled) removes its modes from the row. Read through the
            // root's data attribute, tolerating its absence or an empty
            // value (G91: no throw inside init()). If the mode restored
            // from localStorage above is no longer offered, fall back to
            // 'add' the same way the manual-panel normalization below
            // does — this also tidies any other unknown stored value,
            // since an id that matches nothing in `this.modes` is caught
            // by the same check regardless of why it is unknown.
            var offEl = document.querySelector('[data-off-modes]');
            var offModes = (offEl && offEl.dataset.offModes) ? offEl.dataset.offModes.split(' ') : [];
            this.modes = this.modes.filter(function(m) { return offModes.indexOf(m.id) === -1; });
            // An audit report's Add link seeds Add mode and a code on the
            // root. A seeded mode wins over the stored one; the code is
            // put in the input and never submitted. Absent or empty is fine.
            var seedEl = document.querySelector('[data-seed-mode]');
            if (seedEl && seedEl.dataset.seedMode === 'add') {
                this.mode = 'add';
                try { localStorage.setItem('shelf_scan_mode', 'add'); } catch (err) { /* private mode */ }
            }
            var codeEl = document.querySelector('[data-seed-code]');
            var seedCode = codeEl ? (codeEl.dataset.seedCode || '') : '';
            if (!this.modes.some(function(m) { return m.id === self.mode; })) {
                this.mode = 'add';
                localStorage.setItem('shelf_scan_mode', 'add');
            }

            // The manual panel's open state is seeded server-side from
            // ?add=manual, so there is no URL parsing here and no
            // open-then-populate flash.
            var seed = document.querySelector('[data-manual-open]');
            this.manualOpen = !!(seed && seed.dataset.manualOpen === 'true');

            // Default-status words per media type, from the root (G91: absence
            // or a bad value never throws). A watch, not a hook in
            // persistMediaType(): auto-detect and restore change it too.
            var labelsEl = document.querySelector('[data-status-labels]');
            try {
                this.statusLabelSets = JSON.parse((labelsEl && labelsEl.dataset.statusLabels) || '{}') || {};
            } catch (err) {
                this.statusLabelSets = {};
            }

            // ...but `mode` is restored from localStorage above, and six of
            // the eight modes hide the panel AND its only toggle. Without
            // this, every returning user whose last mode was Lookup follows
            // Home's "Add by hand" link and sees nothing change. Normalize
            // before Alpine paints. A seeded 'wishlist' is preserved:
            // arriving by deep link must not move someone off wishlist mode.
            if (this.manualOpen && this.mode !== 'add' && this.mode !== 'wishlist') {
                this.mode = 'add';
                localStorage.setItem('shelf_scan_mode', 'add');
            }
            this.loadRecentScans(this.mode);

            if (seedCode) {
                var isbnInput = document.getElementById('isbn-input');
                if (isbnInput) {
                    isbnInput.value = seedCode;
                    isbnInput.focus();
                }
            }

            // Both inputs to the default-status control change from more than
            // one place (setMode, auto-detect, the restore above), so watch them.
            this.refreshStatusLabels();
            this.$watch('mediaType', function () { self.refreshStatusLabels(); });
            this.$watch('mode', function () { self.refreshStatusLabels(); });

            // One delegated listener for every way in (#120). A button in a
            // swapped-in search-result fragment has no reliable Alpine scope
            // and the CSP forbids inline onclick, so each way in is a plain
            // button carrying three data attributes and no JS of its own —
            // and it survives every HTMX swap.
            document.addEventListener('click', function (e) {
                var btn = e.target.closest ? e.target.closest('[data-manual-add]') : null;
                if (!btn) return;
                e.preventDefault();
                self.manualOpen = true;
                window.dispatchEvent(new CustomEvent('shelf:manual-add', {
                    detail: {
                        title: btn.dataset.manualAddTitle || '',
                        media_type: btn.dataset.manualAddType || ''
                    }
                }));
            });
            var form = document.querySelector('form[hx-post="/api/scan"]');
            if (form) {
                form.addEventListener('htmx:beforeRequest', function(e) {
                    if (self.mode === 'lend' && !self.borrowerId) {
                        e.preventDefault();
                        showToast('Select a borrower first', 'error');
                        return false;
                    }
                    if ((self.mode === 'move' || self.mode === 'inventory') && !self.location) {
                        e.preventDefault();
                        showToast('Select a location first', 'error');
                        return false;
                    }
                });
            }

            // One listener for the audit strip (G6: afterSwap, not
            // afterSettle). (a) A strip fragment landed: sync auditId from it.
            // (b) A scan card landed in Inventory mode with an audit open —
            // typed form or a legacy card's repost, both /api/scan — so the
            // strip's counts are stale. The camera path refreshes itself.
            document.body.addEventListener('htmx:afterSwap', function(e) {
                var detail = e.detail || {};
                var target = detail.target;
                if (target && target.id === 'audit-strip') {
                    self.syncAuditFromStrip(target);
                    return;
                }
                var path = detail.requestConfig && detail.requestConfig.path;
                if (path === '/api/scan' && self.mode === 'inventory' && self.auditId) {
                    self.refreshAudit();
                }
            });
            var strip = document.getElementById('audit-strip');
            if (strip) {
                strip.addEventListener('click', function(e) {
                    if (e.target.closest && e.target.closest('[data-audit-leave]')) self.leaveAudit();
                });
            }
            if (this.mode === 'inventory') this.refreshAudit();
        },

        // The selected location's name, for the whole-library strip: there
        // every scan files the book at this location, so it must stay in view
        // as the user walks from room to room (QA audit-sessions Obs 1).
        get scanLocationName() {
            var id = this.location;
            var select = document.getElementById('location');
            if (!id || !select) return 'no location';
            for (var i = 0; i < select.options.length; i++) {
                if (select.options[i].value === id) return select.options[i].textContent.trim();
            }
            return 'no location';
        },

        // Ask the server for the strip: the open session's summary, or the
        // start-or-join view when auditId is empty, finished or deleted.
        refreshAudit() {
            var url = '/api/audits/summary?audit_id=' + encodeURIComponent(this.auditId || '');
            htmx.ajax('GET', url, {target: '#audit-strip', swap: 'innerHTML'});
        },

        syncAuditFromStrip(strip) {
            var root = strip.firstElementChild;
            if (!root || !root.dataset) return;
            var id = root.dataset.auditId || '';
            var auditLocation = root.dataset.auditLocation || '';
            this.auditId = id;
            try {
                if (id) localStorage.setItem('shelf_audit_id', id);
                else localStorage.removeItem('shelf_audit_id');
            } catch (e) { /* private mode */ }
            // A location audit takes scans only at its own location, so
            // starting, joining or resuming one selects it — through state,
            // so x-model keeps the select in step.
            if (id && auditLocation && this.location !== auditLocation) {
                this.location = auditLocation;
                this.persistLocation();
            }
        },

        // Leave: this device stops scanning into the audit; the audit stays open.
        leaveAudit() {
            this.auditId = '';
            try { localStorage.removeItem('shelf_audit_id'); } catch (e) { /* private mode */ }
            this.refreshAudit();
        },

        async toggleCamera() {
            if (this.cameraActive) {
                await this.stopCamera();
            } else {
                await this.startCamera();
            }
        },

        async startCamera() {
            try {
                this.cameraActive = true;
                this.scanPaused = false;
                this.scanResult = false;

                this.scanner = window.createBarcodeScanner({
                    html5ElId: 'camera-reader',
                    videoEl: 'zxing-video',
                    html5Config: { fps: 10, qrbox: { width: 280, height: 100 }, aspectRatio: 1.5 },
                    onDecode: (decodedText) => this.onScan(decodedText)
                });
                this.isZxingFallback = this.scanner.engine === 'zxing';
                this.scanLoading = this.isZxingFallback;

                // Let the paired x-show containers settle before the engine
                // grabs its target element.
                await this.$nextTick();

                await this.scanner.start();
                this.scanLoading = false;
            } catch (err) {
                this.scanner = false;
                this.cameraActive = false;
                this.scanLoading = false;
                if (!window.isSecureContext) {
                    showToast('Camera requires HTTPS. Open Shelf through an HTTPS reverse proxy, or leave SHELF_TLS on.', 'error');
                } else {
                    showToast('Camera access denied. Check browser permissions for this site.', 'error');
                }
            }
        },

        async stopCamera() {
            if (this.scanner) {
                await this.scanner.stop();
                this.scanner = false;
            }
            this.cameraActive = false;
            this.scanPaused = false;
            this.scanResult = false;
        },

        async resumeScanning() {
            this.scanResult = false;
            this.scanLoading = false;
            this.lastScanned = '';
            try {
                if (this.scanner) await this.scanner.resume();
            } catch (err) {}
            this.scanPaused = false;
        },

        async onScan(code) {
            if (this.scanPaused) return;

            // Client-side validation
            if (this.mode === 'lend' && !this.borrowerId) {
                showToast('Select a borrower first', 'error');
                return;
            }
            if ((this.mode === 'move' || this.mode === 'inventory') && !this.location) {
                showToast('Select a location first', 'error');
                return;
            }

            var now = Date.now();
            if (code === this.lastScanned && now - this.lastScanTime < 3000) return;
            this.lastScanned = code;
            this.lastScanTime = now;

            // Pause scanner immediately
            this.scanPaused = true;
            this.scanLoading = true;
            this.scanResult = false;
            if (this.scanner) {
                try { await this.scanner.pause(); } catch(e) {}
            }

            // Beep
            try {
                var ctx = new (window.AudioContext || window.webkitAudioContext)();
                var osc = ctx.createOscillator();
                var gain = ctx.createGain();
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.frequency.value = 880;
                gain.gain.value = 0.3;
                osc.start();
                osc.stop(ctx.currentTime + 0.1);
            } catch(e) {}

            // Submit scan via fetch so we can capture the result
            var form = document.querySelector('form[hx-post="/api/scan"]');
            var formData = new FormData(form);
            formData.set('isbn', code);
            formData.set('mode', this.mode);
            if (this.mode === 'lend') formData.set('borrower_id', this.borrowerId);
            try {
                var resp = await fetch('/api/scan', { method: 'POST', headers: { 'X-CSRF-Token': window.csrfToken() }, body: formData });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                var html = await resp.text();

                // Insert result into the scan results list
                var results = document.getElementById('scan-results');
                results.insertAdjacentHTML('afterbegin', html);
                htmx.process(results.firstElementChild);

                // Parse the result to show in overlay. scanCardOutcome (app.js)
                // reads the card's own data-scan-* attributes; it is the same
                // reader the typed/Enter toast uses, so the two paths cannot
                // drift apart the way two class-substring parsers did.
                var tmp = document.createElement('div');
                tmp.innerHTML = html;
                var outcome = scanCardOutcome(tmp.querySelector('.scan-result'));

                // Both legacy cards require the user's interaction with the
                // card below the camera — an ambiguous choice, or the five
                // typed digits. Stop scanning so another frame cannot enqueue
                // a second unresolved choice and swap the card out from
                // under the user.
                if (outcome && (outcome.status === 'legacy_ambiguous' || outcome.status === 'legacy_incomplete')) {
                    if (this.scanner) {
                        try { await this.scanner.stop(); } catch (e) {}
                        this.scanner = false;
                    }
                    this.cameraActive = false;
                    this.scanPaused = false;
                    this.scanLoading = false;
                    this.scanResult = false;
                    showToast(outcome.status === 'legacy_ambiguous' ? 'Choose the matching book below' : 'Type the five digits below', 'warning');
                    return;
                }

                this.scanResult = {
                    ok: outcome.ok,
                    warn: outcome.warn,
                    info: outcome.info,
                    label: outcome.label || 'done',
                    title: outcome.title,
                    authors: outcome.authors,
                    cover: outcome.cover ? outcome.cover.replace(/^\//, '') : null,
                    isbn: code
                };

                // The server recorded the scan into the open audit; refresh
                // the strip's counts (the typed path does this on afterSwap).
                if (this.mode === 'inventory' && this.auditId) this.refreshAudit();
            } catch (err) {
                this.scanResult = { ok: false, warn: false, info: false, label: 'error', title: 'Scan failed', isbn: code };
            }
            this.scanLoading = false;
        }
    }
}

// CSP build has no global fallback — register so x-data="scanPage" resolves.
document.addEventListener('alpine:init', function () {
    Alpine.data('scanPage', scanPage);
});
