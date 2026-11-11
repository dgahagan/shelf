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
        status: t('Point the camera at a 978 or 979 ISBN barcode.'),

        async startCamera() {
            if (this.cameraActive) return;
            this.cameraActive = true;
            this.accepted = false;
            this.status = t('Starting camera…');
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
                this.status = t('Point the camera at a 978 or 979 ISBN barcode.');
            } catch (err) {
                if (this.scanner) await this.scanner.stop();
                this.scanner = false;
                this.cameraActive = false;
                this.isZxingFallback = false;
                if (!window.isSecureContext) {
                    showToast(t('Camera requires HTTPS. Open Shelf through an HTTPS reverse proxy, or leave SHELF_TLS on.'), 'error');
                } else {
                    showToast(t('Camera access denied. Check browser permissions for this site.'), 'error');
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
                this.status = t('That barcode is not a 978/979 ISBN. Try again.');
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

function validEan13(code) {
    if (!/^\d{13}$/.test(code)) return false;
    var total = 0;
    for (var i = 0; i < 12; i += 1) total += parseInt(code[i], 10) * (i % 2 ? 3 : 1);
    return ((10 - (total % 10)) % 10) === parseInt(code[12], 10);
}

function validUpcA(code) {
    if (!/^\d{12}$/.test(code)) return false;
    var total = 0;
    for (var i = 0; i < 11; i += 1) total += parseInt(code[i], 10) * (i % 2 ? 1 : 3);
    return ((10 - (total % 10)) % 10) === parseInt(code[11], 10);
}

function canonicalEditUpc(raw) {
    var digits = String(raw || '').replace(/\D/g, '');
    if (digits.length === 12 && validUpcA(digits)) return '0' + digits;
    if (digits.length === 13 && digits.slice(0, 3) !== '978' && digits.slice(0, 3) !== '979' && validEan13(digits)) return digits;
    return '';
}

function upcCamera() {
    return {
        cameraActive: false,
        scanner: false,
        isZxingFallback: false,
        accepted: false,
        status: t('Point the camera at a UPC-A or EAN-13 retail barcode.'),

        async startCamera() {
            if (this.cameraActive) return;
            this.cameraActive = true;
            this.accepted = false;
            this.status = t('Starting camera…');
            try {
                await this.$nextTick();
                this.scanner = window.createBarcodeScanner({
                    html5ElId: 'edit-upc-camera-reader',
                    videoEl: 'edit-upc-zxing-video',
                    html5Config: { fps: 10, qrbox: { width: 280, height: 100 }, aspectRatio: 1.5 },
                    onDecode: (decodedText) => this.acceptDecoded(decodedText)
                });
                this.isZxingFallback = this.scanner.engine === 'zxing';
                await this.$nextTick();
                await this.scanner.start();
                this.status = t('Point the camera at a UPC-A or EAN-13 retail barcode.');
            } catch (err) {
                if (this.scanner) await this.scanner.stop();
                this.scanner = false;
                this.cameraActive = false;
                this.isZxingFallback = false;
                if (!window.isSecureContext) {
                    showToast(t('Camera requires HTTPS. Open Shelf through an HTTPS reverse proxy, or leave SHELF_TLS on.'), 'error');
                } else {
                    showToast(t('Camera access denied. Check browser permissions for this site.'), 'error');
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
            var canonical = canonicalEditUpc(decodedText);
            if (!canonical) {
                this.status = t('That is not a valid UPC-A / EAN-13 retail barcode. Try again.');
                return;
            }
            var input = document.getElementById('upc');
            if (!input) return;
            this.accepted = true;
            input.value = canonical;
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

// The edit form's two ownership checkboxes (#125). Owned and wishlisted are
// independent, except that an owned item cannot be on the wishlist: the
// wishlist box is disabled while "I own this" is checked, and checking it
// clears the wishlist box. Initial state comes from data attributes, read
// synchronously (G2).
function ownershipBoxes() {
    return {
        owned: false,
        wishlisted: false,
        init() {
            this.owned = this.$el.dataset.owned === '1';
            this.wishlisted = this.$el.dataset.wishlisted === '1';
            this.$watch('owned', (value) => {
                if (value) this.wishlisted = false;
            });
        }
    };
}

// CSP build has no global fallback — register so x-data components resolve.
document.addEventListener('alpine:init', function () {
    Alpine.data('coverDrop', coverDrop);
    Alpine.data('isbnCamera', isbnCamera);
    Alpine.data('upcCamera', upcCamera);
    Alpine.data('ownershipBoxes', ownershipBoxes);
});
