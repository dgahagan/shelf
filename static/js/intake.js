// Placeholder plan so Alpine expressions never dereference null while the
// tiling card mounts/unmounts (the CSP build logs errors on null access).
function emptyPlan() {
    return { factor: 1, tiles: [], preview: { w: 0, h: 0 }, cost_as_is_usd: null, cost_tiled_usd: null };
}

function intakePage() {
    return {
        file: false,
        preview: false,
        analyzing: false,
        confirming: false,
        error: false,
        books: [],
        result: false,
        locationId: '',
        owned: true,
        // High-res tiling: filled from POST /api/intake/plan when the photo
        // would be significantly downscaled by the provider.
        plan: emptyPlan(),
        needsChoice: false,
        planning: false,
        imageEl: null,
        // Camera capture. `supportsCapture` is HTML Media Capture (phones open
        // the camera app and return a full-res still); where it is absent we
        // offer a getUserMedia viewfinder instead (desktop webcam). Both paths
        // end in setPhoto(), so nothing downstream forks.
        cameraAvailable: false,
        supportsCapture: false,
        viewfinder: false,
        capture: false,
        source: 'file',
        // Low-resolution advisory, decided server-side by /api/intake/plan.
        lowRes: false,
        photoW: 0,
        photoH: 0,
        // Monotonic token for the async plan. Every continuation in setPhoto(),
        // planPhoto() and analyze() must prove it still owns the current photo
        // before writing shared state — otherwise a slow plan for photo A lands
        // on photo B and analyze() crops and sends A's pixels under B's name,
        // or A's rows land under B's preview.
        photoGeneration: 0,

        init() {
            // Capability, not user-agent: an Android tablet in desktop mode and
            // a touchscreen Chromebook both get the right path this way.
            this.supportsCapture = 'capture' in document.createElement('input');
            this.cameraAvailable = this.supportsCapture ||
                !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
            var saved = localStorage.getItem('shelf.intake.locationId');
            if (saved) {
                // Only restore if the location still exists — the select's
                // options are the source of truth for valid ids.
                this.$nextTick(() => {
                    var sel = this.$refs.locationSelect;
                    if (sel && Array.from(sel.options).some(o => o.value === saved)) {
                        this.locationId = saved;
                    }
                });
            }
            this.$watch('locationId', v => localStorage.setItem('shelf.intake.locationId', v || ''));
        },

        onFileChosen(e) {
            // Which input fired decides what Retake re-opens later.
            this.source = (e.target === this.$refs.captureInput) ? 'camera' : 'file';
            this.setPhoto(e.target.files[0] || false);
        },

        // The one funnel every photo enters by, whether it came from the
        // library input, the camera-app input, or a viewfinder grab.
        async setPhoto(file) {
            this.file = file;
            this.error = false;
            this.books = [];
            this.result = false;
            this.plan = emptyPlan();
            this.needsChoice = false;
            this.imageEl = null;
            this.lowRes = false;
            this.analyzing = false;
            this.photoW = 0;
            this.photoH = 0;
            if (this.preview) URL.revokeObjectURL(this.preview);
            this.preview = this.file ? URL.createObjectURL(this.file) : false;
            // Clear both inputs so re-choosing the same file re-fires `change`.
            this.clearInputs();
            var gen = ++this.photoGeneration;
            // Never leave a stream running once a photo has been chosen.
            await this.closeViewfinder();
            // A's capture.stop() can resolve after B's whole plan round-trip:
            // planPhoto(oldGen) would then set `planning` and return stale
            // without clearing it, leaving Read Photo disabled forever.
            if (gen !== this.photoGeneration) return;
            if (this.file) this.planPhoto(gen);
        },

        clearInputs() {
            if (this.$refs.photoInput) this.$refs.photoInput.value = '';
            if (this.$refs.captureInput) this.$refs.captureInput.value = '';
        },

        async planPhoto(gen) {
            this.planning = true;
            try {
                var img = await this.loadImage(this.preview);
                // A late continuation from a previous photo must not write to
                // shared state, and must not clear `planning` out from under
                // the photo that is actually current.
                if (gen !== this.photoGeneration) return;
                this.imageEl = img;
                this.photoW = img.naturalWidth;
                this.photoH = img.naturalHeight;
                var resp = await fetch('/api/intake/plan', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': window.csrfToken(),
                    },
                    body: JSON.stringify({ width: img.naturalWidth, height: img.naturalHeight }),
                });
                var data = await resp.json();
                if (gen !== this.photoGeneration) return;
                if (data.ok) {
                    this.plan = data;
                    this.needsChoice = data.needs_choice;
                    this.lowRes = !!data.low_res;
                    if (this.needsChoice) this.$nextTick(() => this.drawModelPreview());
                }
                // Plan failures are non-fatal: fall back to the plain flow.
            } catch (e) {
                if (gen !== this.photoGeneration) return;
                this.plan = emptyPlan();
                this.needsChoice = false;
                this.lowRes = false;
            }
            this.planning = false;
        },

        // Routed at click time, not at load: the capture input on phones,
        // the in-page viewfinder on a desktop with a webcam.
        takePhoto() {
            if (this.supportsCapture) {
                this.$refs.captureInput.click();
                return;
            }
            this.openViewfinder();
        },

        async openViewfinder() {
            // Single entry: `viewfinder` is set synchronously below, so it also
            // covers the window while getUserMedia is still in flight. Two
            // rapid clicks must never acquire two streams.
            if (this.viewfinder) return;
            this.viewfinder = true;
            this.error = false;
            // Framing a new photo shouldn't leave the old photo's advisory up.
            this.lowRes = false;
            if (!this.capture) {
                this.capture = window.createPhotoCapture({
                    videoEl: document.getElementById('intake-video'),
                });
            }
            try {
                await this.capture.start();
            } catch (err) {
                this.viewfinder = false;
                showToast(err && err.name === 'NotFoundError'
                    ? 'No camera found. Use Choose photo instead.'
                    : 'Camera access denied. Check browser permissions for this site.',
                    'error');
            }
        },

        async grabFrame() {
            var blob;
            try {
                blob = await this.capture.grab();
            } catch (e) {
                showToast('Could not capture a frame \u2014 try again.', 'error');
                return;
            }
            await this.closeViewfinder();
            this.source = 'camera';
            await this.setPhoto(new File([blob], 'capture.jpg', { type: 'image/jpeg' }));
        },

        // The single teardown funnel. Capture, Cancel, choosing another file,
        // analyzing and reset all route through it, so no exit from the
        // viewfinder leaves a track live and the camera LED on.
        async closeViewfinder() {
            if (this.capture) await this.capture.stop();
            this.viewfinder = false;
        },

        cancelViewfinder() {
            return this.closeViewfinder();
        },

        loadImage(url) {
            return new Promise((resolve, reject) => {
                var img = new Image();
                img.onload = () => resolve(img);
                img.onerror = reject;
                img.src = url;
            });
        },

        drawModelPreview() {
            var canvas = this.$refs.modelPreview;
            if (!canvas || !this.imageEl || !this.plan) return;
            // Same resampler the as-is upload is encoded from, at the same
            // dimensions — this canvas is what the user reads to decide whether
            // to pay for tiles, so it must not judge the model more harshly
            // than the provider's own resizer would.
            var src = this.resampleImage(this.plan.preview.w, this.plan.preview.h);
            canvas.width = src.width;
            canvas.height = src.height;
            canvas.getContext('2d').drawImage(src, 0, 0);
        },

        fmtCost(usd) {
            if (usd === null || usd === undefined) return 'free · local';
            return '~$' + usd.toFixed(2);
        },

        tileCount() {
            return this.plan ? this.plan.tiles.length : 0;
        },

        // Downscale imageEl to exactly w x h by repeated halving. A single-step
        // drawImage from 8160 -> 2576 aliases, and spine text is exactly the
        // fine detail that damages — each step stays within 2x so the browser's
        // bilinear filter has something to average. Not
        // createImageBitmap(file, {resizeWidth, ...}): Safari ignores the resize
        // options, and its imageOrientation defaults have moved between Chrome
        // versions, whereas <img> + drawImage applies EXIF orientation the same
        // way makeTileBlobs() already relies on. Known ceiling, recorded not
        // handled: older iOS Safari refuses canvases above ~16 MP of area, so a
        // source above ~67 MP (108 MP phone modes) makes the first ceil(cur/2)
        // step exceed it and draws nothing — see the design plan's Out of scope
        // (revisit trigger: a blank-photo report from an iPhone).
        resampleImage(w, h) {
            var srcW = this.imageEl.naturalWidth;
            var srcH = this.imageEl.naturalHeight;
            var src = this.imageEl;
            while (srcW > 2 * w || srcH > 2 * h) {
                var stepW = Math.max(w, Math.ceil(srcW / 2));
                var stepH = Math.max(h, Math.ceil(srcH / 2));
                var step = document.createElement('canvas');
                step.width = stepW;
                step.height = stepH;
                var stepCtx = step.getContext('2d');
                stepCtx.imageSmoothingEnabled = true;
                stepCtx.imageSmoothingQuality = 'high';
                stepCtx.drawImage(src, 0, 0, stepW, stepH);
                // Release the previous intermediate; peak extra memory stays at
                // one half-size canvas.
                if (src !== this.imageEl) src.width = 0;
                src = step;
                srcW = stepW;
                srcH = stepH;
            }
            var out = document.createElement('canvas');
            out.width = w;
            out.height = h;
            var ctx = out.getContext('2d');
            ctx.imageSmoothingEnabled = true;
            ctx.imageSmoothingQuality = 'high';
            ctx.drawImage(src, 0, 0, w, h);
            if (src !== this.imageEl) src.width = 0;
            return out;
        },

        async makeTileBlobs() {
            var blobs = [];
            for (var t of this.plan.tiles) {
                var canvas = document.createElement('canvas');
                canvas.width = t.w;
                canvas.height = t.h;
                canvas.getContext('2d').drawImage(
                    this.imageEl, t.x, t.y, t.w, t.h, 0, 0, t.w, t.h);
                blobs.push(await new Promise(resolve =>
                    canvas.toBlob(resolve, 'image/jpeg', 0.92)));
            }
            return blobs;
        },

        async analyze(tiled) {
            // Single-flight per photo: the busy flag is set before the first
            // suspension point, so Send as-is and Send high-res clicked in one
            // tick produce one request rather than two.
            if (!this.file || this.analyzing) return;
            // The photo this run belongs to. Every continuation below proves it
            // still owns the page before writing shared state, and a loser
            // returns without clearing `analyzing` — the replacer clears it.
            var gen = this.photoGeneration;
            this.analyzing = true;
            this.error = false;
            this.books = [];
            this.result = false;
            // Analysis can run for a minute; the camera must not stay lit.
            await this.closeViewfinder();
            if (gen !== this.photoGeneration) return;
            try {
                var form = new FormData();
                if (tiled && this.plan.tiles.length && this.imageEl) {
                    var blobs = await this.makeTileBlobs();
                    if (gen !== this.photoGeneration) return;
                    blobs.forEach((b, i) => form.append('photos', b, 'tile-' + i + '.jpg'));
                } else {
                    // Never upload more pixels than the provider will ingest:
                    // /plan's preview dims are exactly what it resizes to.
                    // factor is rounded (a 2586px photo reports 1.0), preview
                    // is exact — and emptyPlan()'s {w:0,h:0} falls through to
                    // the original bytes, so a failed plan stays non-fatal.
                    var p = this.plan.preview;
                    var shrink = p.w > 0 && (p.w < this.photoW || p.h < this.photoH) && this.imageEl;
                    if (shrink) {
                        var src = this.resampleImage(p.w, p.h);
                        var blob = await new Promise(resolve =>
                            src.toBlob(resolve, 'image/jpeg', 0.92));
                        if (gen !== this.photoGeneration) return;
                        if (blob) form.append('photos', blob, 'photo.jpg');
                        else form.append('photos', this.file);
                    } else {
                        form.append('photos', this.file);
                    }
                }
                var resp = await fetch('/api/intake/analyze', {
                    method: 'POST',
                    headers: { 'X-CSRF-Token': window.csrfToken() },
                    body: form,
                });
                if (gen !== this.photoGeneration) return;
                var data = await resp.json();
                if (gen !== this.photoGeneration) return;
                if (data.ok) {
                    this.needsChoice = false;
                    this.books = data.books.map(b => ({
                        title: b.title, authors: b.authors || '', isbn: b.isbn || '',
                        source: b.source || 'read', media_type: 'book', include: true,
                    }));
                } else {
                    this.error = data.message || 'Analysis failed';
                }
            } catch (e) {
                if (gen !== this.photoGeneration) return;
                this.error = 'Analysis failed: ' + e.message;
            }
            this.analyzing = false;
        },

        selectedCount() {
            return this.books.filter(b => b.include).length;
        },

        // Called from @change/@input in the review list: the Alpine CSP build
        // cannot evaluate the nested assignment x-model="book.title" would need.
        toggleInclude(i) {
            this.books[i].include = !this.books[i].include;
        },

        setBookTitle(i, value) {
            this.books[i].title = value;
        },

        setBookAuthors(i, value) {
            this.books[i].authors = value;
        },

        setBookIsbn(i, value) {
            this.books[i].isbn = value;
        },

        setBookMediaType(i, value) {
            this.books[i].media_type = value;
        },

        selectAll() {
            this.books.forEach(b => b.include = true);
        },

        deselectAll() {
            this.books.forEach(b => b.include = false);
        },

        async confirm() {
            this.confirming = true;
            this.error = false;
            try {
                var resp = await fetch('/api/intake/confirm', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': window.csrfToken(),
                    },
                    body: JSON.stringify({
                        // `source` is deliberately not sent: it drives the
                        // review-row hint only, and the server has no use for it.
                        books: this.books.filter(b => b.include).map(b => ({
                            title: b.title, authors: b.authors || null,
                            isbn: b.isbn || null, media_type: b.media_type,
                        })),
                        location_id: this.locationId ? parseInt(this.locationId) : null,
                        owned: this.owned,
                    }),
                });
                var data = await resp.json();
                if (data.ok) {
                    this.result = data;
                    this.books = [];
                    showToast('Added ' + data.added.length + ' items');
                } else {
                    this.error = data.message || 'Add failed';
                }
            } catch (e) {
                this.error = 'Add failed: ' + e.message;
            }
            this.confirming = false;
        },

        async reset() {
            this.file = false;
            if (this.preview) URL.revokeObjectURL(this.preview);
            this.preview = false;
            this.books = [];
            this.result = false;
            this.error = false;
            // emptyPlan(), not null: the CSP evaluator throws on a null
            // dereference in a template guard, and the tiling card's
            // x-text="plan.factor" would hit exactly that.
            this.plan = emptyPlan();
            this.needsChoice = false;
            this.imageEl = null;
            this.lowRes = false;
            this.analyzing = false;
            this.source = 'file';
            this.photoW = 0;
            this.photoH = 0;
            // A plan still in flight must not write back into a reset page.
            this.photoGeneration++;
            this.clearInputs();
            await this.closeViewfinder();
        },
    };
}

// CSP build has no global fallback — register so x-data="intakePage" resolves.
document.addEventListener('alpine:init', function () {
    Alpine.data('intakePage', intakePage);
});
