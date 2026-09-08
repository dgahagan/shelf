function shelfFillPage() {
    return {
        location: localStorage.getItem('shelf_fill_location') || '',
        mediaType: localStorage.getItem('shelf_fill_media_type') || 'auto',
        platform: localStorage.getItem('shelf_fill_platform') || '',
        cameraActive: false,
        scanPaused: false,
        scanLoading: false,
        scanResult: false,
        scanner: false,
        isZxingFallback: false,
        lastScanned: '',
        lastScanTime: 0,

        init() {
            var self = this;
            var form = document.getElementById('shelf-fill-form');
            if (form) {
                form.addEventListener('htmx:beforeRequest', function (e) {
                    if (!self.location) {
                        e.preventDefault();
                        showToast('Choose a shelf location first', 'error');
                        return false;
                    }
                });
            }
        },

        persistLocation() { localStorage.setItem('shelf_fill_location', this.location); },
        persistMediaType() { localStorage.setItem('shelf_fill_media_type', this.mediaType); },
        persistPlatform() { localStorage.setItem('shelf_fill_platform', this.platform); },

        async toggleCamera() {
            if (this.cameraActive) await this.stopCamera();
            else await this.startCamera();
        },

        async startCamera() {
            if (!this.location) {
                showToast('Choose a shelf location first', 'error');
                return;
            }
            try {
                this.cameraActive = true;
                this.scanPaused = false;
                this.scanResult = false;
                this.scanner = window.createBarcodeScanner({
                    html5ElId: 'shelf-fill-camera-reader',
                    videoEl: 'shelf-fill-zxing-video',
                    html5Config: {fps: 10, qrbox: {width: 280, height: 100}, aspectRatio: 1.5},
                    onDecode: (decodedText) => this.onScan(decodedText)
                });
                this.isZxingFallback = this.scanner.engine === 'zxing';
                await this.$nextTick();
                await this.scanner.start();
            } catch (err) {
                this.scanner = false;
                this.cameraActive = false;
                showToast('Camera access failed. Check browser permissions and HTTPS.', 'error');
            }
        },

        async stopCamera() {
            if (this.scanner) {
                await this.scanner.stop();
                this.scanner = false;
            }
            this.cameraActive = false;
            this.scanPaused = false;
            this.scanLoading = false;
            this.scanResult = false;
        },

        async resumeScanning() {
            this.scanResult = false;
            this.scanLoading = false;
            this.lastScanned = '';
            try { if (this.scanner) await this.scanner.resume(); } catch (err) {}
            this.scanPaused = false;
        },

        async onScan(code) {
            if (this.scanPaused || !this.location) return;
            var now = Date.now();
            if (code === this.lastScanned && now - this.lastScanTime < 3000) return;
            this.lastScanned = code;
            this.lastScanTime = now;
            this.scanPaused = true;
            this.scanLoading = true;
            this.scanResult = false;
            try { if (this.scanner) await this.scanner.pause(); } catch (err) {}

            var form = document.getElementById('shelf-fill-form');
            var formData = new FormData(form);
            formData.set('isbn', code);
            formData.set('location_id', this.location);
            formData.set('media_type', this.mediaType);
            formData.set('platform', this.platform);
            try {
                var resp = await fetch('/api/shelf-fill/scan', {
                    method: 'POST',
                    headers: {'X-CSRF-Token': window.csrfToken()},
                    body: formData
                });
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                var html = await resp.text();
                var results = document.getElementById('shelf-fill-results');
                results.insertAdjacentHTML('afterbegin', html);
                if (results.firstElementChild) htmx.process(results.firstElementChild);

                var tmp = document.createElement('div');
                tmp.innerHTML = html;
                var outcome = scanCardOutcome(tmp.querySelector('.scan-result'));
                this.scanResult = {
                    title: outcome && outcome.title,
                    label: outcome && outcome.label,
                    code: code
                };
            } catch (err) {
                this.scanResult = {title: 'Scan failed', label: 'error', code: code};
            }
            this.scanLoading = false;
        }
    };
}

document.addEventListener('alpine:init', function () {
    Alpine.data('shelfFillPage', shelfFillPage);
});
