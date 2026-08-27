function scanPage() {
    return {
        mode: localStorage.getItem('shelf_scan_mode') || 'add',
        // Auto for a *new* user. A stored value is deliberately never
        // migrated: "book" is also what someone who scans books chose, and
        // reinterpreting that as "no choice" is guessing at intent. The
        // barcode rule (§1) is what reaches those users instead.
        mediaType: localStorage.getItem('shelf_media_type') || 'auto',
        platform: localStorage.getItem('shelf_platform') || '',
        location: localStorage.getItem('shelf_location') || '',
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
                .then(function(r) { return r.text(); })
                .then(function(html) {
                    document.getElementById('scan-results').innerHTML = html;
                });
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

        // Client-side validation before form submit
        init() {
            var self = this;
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
        },

        async showMissing() {
            var form = new FormData();
            form.set('location_id', this.location);
            form.set('scanned_ids', this.inventoryScannedIds.join(','));
            var resp = await fetch('/api/inventory/missing', {method: 'POST', headers: {'X-CSRF-Token': window.csrfToken()}, body: form});
            var html = await resp.text();
            document.getElementById('inventory-missing').innerHTML = html;
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
                if (location.protocol !== 'https:' && location.hostname !== 'localhost') {
                    showToast('Camera requires HTTPS. Access Shelf via https:// and accept the certificate.', 'error');
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
                if (this.mode === 'inventory') {
                    var linkEl = tmp.querySelector('a[href^="/item/"]');
                    if (linkEl) {
                        var match = linkEl.getAttribute('href').match(/\/item\/(\d+)/);
                        if (match) this.inventoryScannedIds.push(parseInt(match[1]));
                    }
                }
            } catch (err) {
                this.scanResult = { ok: false, warn: false, label: 'error', title: 'Network error', isbn: code };
            }
            this.scanLoading = false;
        }
    }
}

// CSP build has no global fallback — register so x-data="scanPage" resolves.
document.addEventListener('alpine:init', function () {
    Alpine.data('scanPage', scanPage);
});
