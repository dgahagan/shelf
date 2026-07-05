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

        onFileChosen(e) {
            this.file = e.target.files[0] || null;
            this.error = false;
            this.books = [];
            this.result = false;
            this.plan = emptyPlan();
            this.needsChoice = false;
            this.imageEl = null;
            if (this.preview) URL.revokeObjectURL(this.preview);
            this.preview = this.file ? URL.createObjectURL(this.file) : false;
            if (this.file) this.planPhoto();
        },

        async planPhoto() {
            this.planning = true;
            try {
                var img = await this.loadImage(this.preview);
                this.imageEl = img;
                var resp = await fetch('/api/intake/plan', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': window.csrfToken(),
                    },
                    body: JSON.stringify({ width: img.naturalWidth, height: img.naturalHeight }),
                });
                var data = await resp.json();
                if (data.ok) {
                    this.plan = data;
                    this.needsChoice = data.needs_choice;
                    if (this.needsChoice) this.$nextTick(() => this.drawModelPreview());
                }
                // Plan failures are non-fatal: fall back to the plain flow.
            } catch (e) {
                this.plan = emptyPlan();
                this.needsChoice = false;
            }
            this.planning = false;
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
            canvas.width = this.plan.preview.w;
            canvas.height = this.plan.preview.h;
            canvas.getContext('2d').drawImage(this.imageEl, 0, 0, canvas.width, canvas.height);
        },

        fmtCost(usd) {
            if (usd === null || usd === undefined) return 'free · local';
            return '~$' + usd.toFixed(2);
        },

        tileCount() {
            return this.plan ? this.plan.tiles.length : 0;
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
            if (!this.file) return;
            this.analyzing = true;
            this.error = false;
            this.books = [];
            this.result = false;
            try {
                var form = new FormData();
                if (tiled && this.plan.tiles.length && this.imageEl) {
                    var blobs = await this.makeTileBlobs();
                    blobs.forEach((b, i) => form.append('photos', b, 'tile-' + i + '.jpg'));
                } else {
                    form.append('photos', this.file);
                }
                var resp = await fetch('/api/intake/analyze', {
                    method: 'POST',
                    headers: { 'X-CSRF-Token': window.csrfToken() },
                    body: form,
                });
                var data = await resp.json();
                if (data.ok) {
                    this.needsChoice = false;
                    this.books = data.books.map(b => ({
                        title: b.title, authors: b.authors || '', include: true,
                    }));
                } else {
                    this.error = data.message || 'Analysis failed';
                }
            } catch (e) {
                this.error = 'Analysis failed: ' + e.message;
            }
            this.analyzing = false;
        },

        selectedCount() {
            return this.books.filter(b => b.include).length;
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
                        books: this.books.filter(b => b.include).map(b => ({
                            title: b.title, authors: b.authors || null,
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

        reset() {
            this.file = false;
            if (this.preview) URL.revokeObjectURL(this.preview);
            this.preview = false;
            this.books = [];
            this.result = false;
            this.error = false;
            this.plan = null;
            this.needsChoice = false;
            this.imageEl = null;
            if (this.$refs.photoInput) this.$refs.photoInput.value = '';
        },
    };
}

// CSP build has no global fallback — register so x-data="intakePage" resolves.
document.addEventListener('alpine:init', function () {
    Alpine.data('intakePage', intakePage);
});
