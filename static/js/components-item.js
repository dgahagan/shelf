// Registered Alpine components (CSP-build compatible) — item/series/intake pages.
//
// Same rules as components.js: the Alpine CSP build cannot evaluate arrow
// functions, template literals, or globals (fetch/window/document/JSON/...)
// in template attributes, so that logic lives here. Jinja-templated initial
// state is passed via data-* attributes on the component root and read in
// init() from this.$el.dataset.

document.addEventListener('alpine:init', function () {

    // series.html — per-series card: Hardcover completeness check + add-to-wishlist
    Alpine.data('seriesCard', function () {
        return {
            checking: false, result: false, error: false, added: {},
            seriesName: '',
            init() {
                this.seriesName = this.$el.dataset.seriesName || '';
            },
            get missingBooks() {
                return this.result ? this.result.books.filter(x => x.status === 'missing') : [];
            },
            check() {
                this.checking = true; this.error = false;
                fetch('/api/series/check?name=' + encodeURIComponent(this.seriesName))
                    .then(r => r.json())
                    .then(d => { this.checking = false; if (d.ok) this.result = d; else this.error = d.message; })
                    .catch(() => { this.checking = false; this.error = 'Check failed'; });
            },
            addToWishlist(b) {
                fetch('/api/hardcover/add-to-shelf', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window.csrfToken() },
                    body: JSON.stringify({ title: b.title, authors: b.authors, cover_url: b.cover_url, hardcover_book_id: b.hardcover_book_id, series_name: b.series_name, series_position: b.series_position })
                })
                    .then(r => r.json())
                    .then(d => { if (d.ok || d.item_id) { this.added[b.hardcover_book_id] = true; showToast('Added to wishlist'); } else { showToast(d.message || 'Failed', 'error'); } })
                    .catch(() => showToast('Failed', 'error'));
            }
        };
    });

    // item_detail.html — "Fetch synopsis" button
    Alpine.data('synopsisFetcher', function () {
        return {
            fetching: false, failed: false,
            itemId: '',
            init() {
                this.itemId = this.$el.dataset.itemId || '';
            },
            fetchSynopsis() {
                this.fetching = true; this.failed = false;
                fetch('/api/items/' + this.itemId + '/fetch-synopsis', { method: 'POST', headers: { 'X-CSRF-Token': window.csrfToken() } })
                    .then(r => r.json())
                    .then(d => { if (d.ok) { location.reload(); } else { this.failed = true; this.fetching = false; } })
                    .catch(() => { this.failed = true; this.fetching = false; });
            }
        };
    });

    // item_detail.html — "Push to Hardcover" button
    Alpine.data('hardcoverPush', function () {
        return {
            hcPushing: false, hcResult: false,
            itemId: '',
            init() {
                this.itemId = this.$el.dataset.itemId || '';
            },
            push() {
                this.hcPushing = true; this.hcResult = false;
                fetch('/api/hardcover/push/' + this.itemId, { method: 'POST', headers: { 'X-CSRF-Token': window.csrfToken() } })
                    .then(r => r.json())
                    .then(d => { this.hcResult = d; this.hcPushing = false; if (d.ok) showToast('Synced to Hardcover'); })
                    .catch(() => { this.hcResult = { ok: false, message: 'Connection failed' }; this.hcPushing = false; });
            }
        };
    });

    // fragments/hardcover_search_results.html — per-result card (swapped into
    // discover.html's #hc-results via hx-get=/api/hardcover/search).
    // The book payload rides on the button's data-book attribute.
    Alpine.data('hcResultCard', function () {
        return {
            adding: false, added: false, error: false,
            init() {
                this.added = this.$el.dataset.added === '1';
            },
            addBook(ev) {
                this.adding = true; this.error = false;
                var d = JSON.parse(ev.currentTarget.dataset.book);
                fetch('/api/hardcover/add-to-shelf', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window.csrfToken() },
                    body: JSON.stringify(d)
                })
                    .then(r => r.json())
                    .then(r => { this.adding = false; if (r.ok) { this.added = true; showToast('Added to wishlist'); } else { this.error = r.message; if (r.item_id) this.added = true; } })
                    .catch(() => { this.adding = false; this.error = 'Failed'; });
            }
        };
    });

});
