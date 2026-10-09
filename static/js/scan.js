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
        inventoryScannedIds: [],
        isZxingFallback: false,
        manualOpen: false,

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
                inventory: {heading: 'Inventory Audit', description: 'Select a location and scan every item you find there. Then check for missing items.'},
                lookup: {heading: 'Lookup', description: 'Scan to check if an item is in your collection. No changes are made.'},
                quick_rate: {heading: 'Quick Rate', description: 'Scan to mark items as read or completed.'},
            };
            return configs[this.mode] || configs.add;
        },

        loadRecentScans(m) {
            fetch('/api/recent-scans?mode=' + encodeURIComponent(m))
                .then(function(r) {
                    if (!r.ok) throw new Error('HTTP ' + r.status);
                    return r.text();
                })
                .then(function(html) {
                    document.getElementById('scan-results').innerHTML = html;
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
            document.getElementById('inventory-missing').innerHTML = '';
            this.inventoryScannedIds = [];
            this.loadRecentScans(m);
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

            // A legacy ambiguity is submitted by a nested HTMX form rather
            // than by onScan's fetch. The replacement card carries the item
            // link only after the user confirms it, so account for that
            // response here before the inventory missing check runs.
            document.body.addEventListener('htmx:afterSwap', function(e) {
                var target = e.detail && e.detail.target;
                var card = target && target.matches &&
                    target.matches('.scan-result[data-scan-inventory-confirmed]')
                    ? target
                    : document.querySelector('.scan-result[data-scan-inventory-confirmed]');
                if (!card) return;
                self.recordInventoryItem(card);
                card.removeAttribute('data-scan-inventory-confirmed');
            });
        },

        recordInventoryItem(root) {
            if (this.mode !== 'inventory' || !root) return;
            var linkEl = root.querySelector('a[href^="/item/"]');
            if (!linkEl) return;
            var match = linkEl.getAttribute('href').match(/\/item\/(\d+)/);
            if (!match) return;
            var itemId = parseInt(match[1]);
            if (this.inventoryScannedIds.indexOf(itemId) === -1) {
                this.inventoryScannedIds.push(itemId);
            }
        },

        async showMissing() {
            var form = new FormData();
            form.set('location_id', this.location);
            form.set('scanned_ids', this.inventoryScannedIds.join(','));
            try {
                var resp = await fetch('/api/inventory/missing', {
                    method: 'POST',
                    headers: {'X-CSRF-Token': window.csrfToken()},
                    body: form
                });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                var html = await resp.text();
                document.getElementById('inventory-missing').innerHTML = html;
            } catch (e) {
                showToast('Could not check missing items', 'error');
            }
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
                    label: outcome.label || 'done',
                    title: outcome.title,
                    authors: outcome.authors,
                    cover: outcome.cover ? outcome.cover.replace(/^\//, '') : null,
                    isbn: code
                };

                // Track item IDs for inventory mode
                this.recordInventoryItem(tmp);
            } catch (err) {
                this.scanResult = { ok: false, warn: false, label: 'error', title: 'Scan failed', isbn: code };
            }
            this.scanLoading = false;
        }
    }
}

// CSP build has no global fallback — register so x-data="scanPage" resolves.
document.addEventListener('alpine:init', function () {
    Alpine.data('scanPage', scanPage);
});
