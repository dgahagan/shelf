function coverDrop() {
    return {
        dragging: false,
        preview: false,
        handleDrop(e) {
            this.dragging = false;
            var file = e.dataTransfer.files[0];
            if (file && file.type.startsWith('image/')) {
                var dt = new DataTransfer();
                dt.items.add(file);
                this.$refs.coverInput.files = dt.files;
                this.preview = URL.createObjectURL(file);
            }
        },
        handleFile(e) {
            var file = e.target.files[0];
            if (file) this.preview = URL.createObjectURL(file);
        }
    };
}

function isbnCamera() {
    return {
        cameraActive: false,
        scanner: false,
        isZxingFallback: false,
        accepted: false,
        status: 'Point the camera at a 978 or 979 ISBN barcode.',

        async startCamera() {
            if (this.cameraActive) return;
            this.cameraActive = true;
            this.accepted = false;
            this.status = 'Starting camera…';

            try {
                await this.$nextTick();
                this.scanner = window.createBarcodeScanner({
                    html5ElId: 'edit-isbn-camera-reader',
                    videoEl: 'edit-isbn-zxing-video',
                    html5Config: { fps: 10, qrbox: { width: 280, height: 100 }, aspectRatio: 1.5 },
                    onDecode: (decodedText) => this.acceptDecoded(decodedText)
                });
                this.isZxingFallback = this.scanner.engine === 'zxing';
                await this.$nextTick();
                await this.scanner.start();
                this.status = 'Point the camera at a 978 or 979 ISBN barcode.';
            } catch (err) {
                if (this.scanner) await this.scanner.stop();
                this.scanner = false;
                this.cameraActive = false;
                this.isZxingFallback = false;
                if (location.protocol !== 'https:' && location.hostname !== 'localhost') {
                    showToast('Camera requires HTTPS. Access Shelf via https:// and accept the certificate.', 'error');
                } else {
                    showToast('Camera access denied. Check browser permissions for this site.', 'error');
                }
            }
        },

        async stopCamera() {
            if (this.scanner) await this.scanner.stop();
            this.scanner = false;
            this.cameraActive = false;
            this.isZxingFallback = false;
        },

        acceptDecoded(decodedText) {
            if (this.accepted) return;
            var digits = String(decodedText || '').replace(/\D/g, '');
            if (digits.length !== 13 || (digits.slice(0, 3) !== '978' && digits.slice(0, 3) !== '979')) {
                this.status = 'That barcode is not a 978/979 ISBN. Try again.';
                return;
            }

            var input = document.getElementById('isbn');
            if (!input) return;
            this.accepted = true;
            input.value = digits;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            this.stopCamera().then(function () {
                input.focus();
                input.select();
            });
        }
    };
}

function updateEditSectionVisibility(root) {
    var mediaSelect = root.querySelector('#media_type');
    if (!mediaSelect) return;
    var mediaType = mediaSelect.value;

    root.querySelectorAll('[data-media-types]').forEach(function (element) {
        var supported = (element.dataset.mediaTypes || '').split(/\s+/).filter(Boolean);
        var alwaysVisible = element.dataset.alwaysVisible === 'true';
        element.hidden = !alwaysVisible && supported.indexOf(mediaType) === -1;
    });
}

document.addEventListener('DOMContentLoaded', function () {
    var root = document.querySelector('[data-item-edit-sections]');
    if (!root) return;
    var mediaSelect = root.querySelector('#media_type');
    updateEditSectionVisibility(root);
    if (mediaSelect) {
        mediaSelect.addEventListener('change', function () {
            updateEditSectionVisibility(root);
        });
    }
});

// CSP build has no global fallback — register so x-data components resolve.
document.addEventListener('alpine:init', function () {
    Alpine.data('coverDrop', coverDrop);
    Alpine.data('isbnCamera', isbnCamera);
});
