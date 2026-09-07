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

function installUpcEditor(root) {
    var form = root.querySelector('#item-edit-form');
    var section = root.querySelector('#edit-identifiers');
    var navLink = root.querySelector('[data-section-nav="identifiers"]');
    var mediaSelect = root.querySelector('#media_type');
    if (!form || !section || !mediaSelect || section.querySelector('[data-upc-editor]')) return;

    var match = form.getAttribute('action').match(/\/api\/items\/(\d+)$/);
    if (!match) return;
    var itemId = match[1];
    var upcMediaTypes = ['dvd', 'cd', 'comic', 'video_game'];
    var isbnMediaTypes = ['book', 'kids_book', 'audiobook', 'ebook', 'comic'];

    fetch('/api/items/' + itemId + '/barcode-context')
        .then(function (response) {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.json();
        })
        .then(function (context) {
            var combined = Array.from(new Set(isbnMediaTypes.concat(upcMediaTypes))).join(' ');
            section.dataset.mediaTypes = combined;
            if (navLink) navLink.dataset.mediaTypes = combined;
            if (context.upc) {
                section.dataset.alwaysVisible = 'true';
                if (navLink) navLink.dataset.alwaysVisible = 'true';
            }

            var isbnInput = section.querySelector('#isbn');
            if (isbnInput && isbnInput.parentElement) {
                isbnInput.parentElement.dataset.mediaTypes = isbnMediaTypes.join(' ');
                isbnInput.parentElement.dataset.alwaysVisible = isbnInput.value ? 'true' : 'false';
                var isbnButton = isbnInput.parentElement.nextElementSibling;
                if (isbnButton && isbnButton.tagName === 'BUTTON') {
                    isbnButton.dataset.mediaTypes = isbnMediaTypes.join(' ');
                    isbnButton.dataset.alwaysVisible = isbnInput.value ? 'true' : 'false';
                }
            }

            var editor = document.createElement('div');
            editor.dataset.upcEditor = 'true';
            editor.dataset.mediaTypes = upcMediaTypes.join(' ');
            editor.dataset.alwaysVisible = context.upc ? 'true' : 'false';
            editor.innerHTML =
                '<div>' +
                '<label for="upc" class="block text-sm font-medium text-shelf-muted mb-1">UPC / EAN</label>' +
                '<input id="upc" name="upc" value="' + String(context.upc || '').replace(/&/g, '&amp;').replace(/"/g, '&quot;') + '" placeholder="UPC-A or EAN-13" class="w-full bg-shelf-bg border border-shelf-border rounded-lg px-3 py-2 text-shelf-text focus:ring-2 focus:ring-shelf-accent focus:border-transparent outline-none">' +
                '</div>' +
                '<button type="button" data-upc-scan class="px-4 py-2 bg-shelf-hover text-shelf-text border border-shelf-border rounded-lg text-sm hover:border-shelf-accent/50 transition-colors">Scan UPC / EAN</button>' +
                '<div data-upc-modal class="fixed inset-0 z-50 bg-black/80 p-4 flex items-center justify-center" hidden>' +
                '<div class="w-full max-w-md bg-shelf-card rounded-xl border border-shelf-border p-4">' +
                '<div class="flex items-center justify-between gap-3 mb-3"><h3 class="text-lg font-semibold">Scan UPC / EAN</h3><button type="button" data-upc-close aria-label="Close scanner" class="text-shelf-muted hover:text-shelf-text text-2xl leading-none">&times;</button></div>' +
                '<div id="edit-upc-camera-reader" class="rounded-xl overflow-hidden border border-shelf-border bg-black"></div>' +
                '<div id="edit-upc-zxing-container" class="rounded-xl overflow-hidden border border-shelf-border bg-black" hidden><video id="edit-upc-zxing-video" class="w-full" autoplay muted playsinline></video></div>' +
                '<p data-upc-status class="text-xs text-shelf-muted text-center mt-2">Point the camera at a UPC-A or EAN-13 retail barcode.</p>' +
                '</div></div>';
            section.appendChild(editor);

            var input = editor.querySelector('#upc');
            var modal = editor.querySelector('[data-upc-modal]');
            var status = editor.querySelector('[data-upc-status]');
            var html5 = editor.querySelector('#edit-upc-camera-reader');
            var zxing = editor.querySelector('#edit-upc-zxing-container');
            var scanner = null;
            var accepted = false;

            async function stop() {
                if (scanner) await scanner.stop();
                scanner = null;
                modal.hidden = true;
                accepted = false;
            }

            editor.querySelector('[data-upc-close]').addEventListener('click', function () { stop(); });
            editor.querySelector('[data-upc-scan]').addEventListener('click', async function () {
                if (scanner) return;
                modal.hidden = false;
                status.textContent = 'Starting camera…';
                try {
                    scanner = window.createBarcodeScanner({
                        html5ElId: 'edit-upc-camera-reader',
                        videoEl: 'edit-upc-zxing-video',
                        html5Config: { fps: 10, qrbox: { width: 280, height: 100 }, aspectRatio: 1.5 },
                        onDecode: function (decodedText) {
                            if (accepted) return;
                            var canonical = canonicalEditUpc(decodedText);
                            if (!canonical) {
                                status.textContent = 'That is not a valid UPC-A / EAN-13 retail barcode. Try again.';
                                return;
                            }
                            accepted = true;
                            input.value = canonical;
                            input.dispatchEvent(new Event('input', { bubbles: true }));
                            input.dispatchEvent(new Event('change', { bubbles: true }));
                            stop().then(function () { input.focus(); input.select(); });
                        }
                    });
                    if (scanner.engine === 'zxing') {
                        html5.hidden = true;
                        zxing.hidden = false;
                    } else {
                        html5.hidden = false;
                        zxing.hidden = true;
                    }
                    await scanner.start();
                    status.textContent = 'Point the camera at a UPC-A or EAN-13 retail barcode.';
                } catch (err) {
                    await stop();
                    showToast('Camera access failed. Check browser permissions and HTTPS.', 'error');
                }
            });

            updateEditSectionVisibility(root);
        })
        .catch(function () {
            // Barcode editing is an enhancement; failure to load it must not
            // prevent the established item-edit form from working.
        });
}

document.addEventListener('DOMContentLoaded', function () {
    var root = document.querySelector('[data-item-edit-sections]');
    if (!root) return;
    var mediaSelect = root.querySelector('#media_type');
    updateEditSectionVisibility(root);
    installUpcEditor(root);
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
