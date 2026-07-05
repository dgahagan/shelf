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

        onFileChosen(e) {
            this.file = e.target.files[0] || null;
            this.error = false;
            this.books = [];
            this.result = false;
            if (this.preview) URL.revokeObjectURL(this.preview);
            this.preview = this.file ? URL.createObjectURL(this.file) : false;
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

        async analyze() {
            if (!this.file) return;
            this.analyzing = true;
            this.error = false;
            this.books = [];
            this.result = false;
            try {
                var form = new FormData();
                form.append('photo', this.file);
                var resp = await fetch('/api/intake/analyze', {
                    method: 'POST',
                    headers: { 'X-CSRF-Token': window.csrfToken() },
                    body: form,
                });
                var data = await resp.json();
                if (data.ok) {
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
            if (this.$refs.photoInput) this.$refs.photoInput.value = '';
        },
    };
}

// CSP build has no global fallback — register so x-data="intakePage" resolves.
document.addEventListener('alpine:init', function () {
    Alpine.data('intakePage', intakePage);
});
