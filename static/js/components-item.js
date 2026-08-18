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
            // Series synopsis (issue #6): read/edit state for the inline editor.
            description: '', editing: false, draft: '', saving: false,
            fetching: false, descError: '',
            // Series membership management: rename/merge and disband.
            menuOpen: false, renaming: false, confirmingRemove: false,
            renameDraft: '', renameError: '', renameSaving: false,
            removing: false, removeError: '', itemCount: 0, seriesNames: [],
            init() {
                this.seriesName = this.$el.dataset.seriesName || '';
                this.description = this.$el.dataset.description || '';
                this.itemCount = parseInt(this.$el.dataset.itemCount || '0', 10);
                // The page renders one shared <datalist> of every series name
                // (series.html); reuse it rather than repeating the list on
                // every card as a data-* attribute.
                var list = document.getElementById('series-names');
                this.seriesNames = list
                    ? Array.prototype.map.call(list.options, o => o.value)
                    : [];
            },
            toggleMenu() {
                this.menuOpen = !this.menuOpen;
            },
            startRename() {
                this.menuOpen = false;
                this.confirmingRemove = false;
                this.renameDraft = this.seriesName;
                this.renameError = '';
                this.renaming = true;
            },
            cancelRename() {
                this.renaming = false;
                this.renameError = '';
            },
            get mergeTarget() {
                // NOCASE on the server, so match case-insensitively here too.
                // The card's own name is a no-op rename, not a merge.
                var draft = this.renameDraft.trim().toLowerCase();
                if (!draft || draft === this.seriesName.trim().toLowerCase()) return '';
                return this.seriesNames.find(n => n.trim().toLowerCase() === draft) || '';
            },
            get renameLabel() {
                if (this.renameSaving) return 'Saving…';
                return this.mergeTarget ? 'Merge into ' + this.mergeTarget : 'Rename';
            },
            submitRename() {
                this.renameSaving = true;
                this.renameError = '';
                var self = this;
                var body = new URLSearchParams();
                body.set('new_name', this.renameDraft);
                fetch('/api/series/' + encodeURIComponent(this.seriesName) + '/rename', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRF-Token': window.csrfToken() },
                    body: body.toString()
                })
                    .then(r => r.json())
                    .then(d => {
                        if (!d.ok) {
                            self.renameSaving = false;
                            self.renameError = d.message || 'Rename failed';
                            return;
                        }
                        showToast(d.merged
                            ? 'Merged ' + self._books(d.count) + ' into ' + d.name
                            : 'Series renamed to ' + d.name);
                        // The card list is server-rendered and the grouping
                        // just changed — reload rather than regroup client-side
                        // (same call bulkUpdate() makes).
                        setTimeout(() => location.reload(), 600);
                    })
                    .catch(() => { self.renameSaving = false; self.renameError = 'Rename failed'; });
            },
            startRemoveAll() {
                this.menuOpen = false;
                this.renaming = false;
                this.removeError = '';
                this.confirmingRemove = true;
            },
            cancelRemoveAll() {
                this.confirmingRemove = false;
                this.removeError = '';
            },
            get removeCountLabel() {
                return this._books(this.itemCount);
            },
            submitRemoveAll() {
                this.removing = true;
                this.removeError = '';
                var self = this;
                fetch('/api/series/' + encodeURIComponent(this.seriesName) + '/remove-all', {
                    method: 'POST',
                    headers: { 'X-CSRF-Token': window.csrfToken() }
                })
                    .then(r => r.json())
                    .then(d => {
                        if (!d.ok) {
                            self.removing = false;
                            self.removeError = d.message || 'Remove failed';
                            return;
                        }
                        showToast('Removed ' + self._books(d.count) + ' from the series');
                        setTimeout(() => location.reload(), 600);
                    })
                    .catch(() => { self.removing = false; self.removeError = 'Remove failed'; });
            },
            _books(n) {
                return n + (n === 1 ? ' book' : ' books');
            },
            startEdit() {
                this.draft = this.description;
                this.descError = '';
                this.editing = true;
            },
            cancelEdit() {
                this.editing = false;
                this.descError = '';
            },
            saveDescription() {
                this.saving = true;
                this.descError = '';
                var self = this;
                // Endpoint takes a Form body (mirrors tags.py), not JSON.
                var body = new URLSearchParams();
                body.set('description', this.draft);
                fetch('/api/series/' + encodeURIComponent(this.seriesName) + '/description', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRF-Token': window.csrfToken() },
                    body: body.toString()
                })
                    .then(r => r.json())
                    .then(d => {
                        self.saving = false;
                        if (d.ok) {
                            self.description = d.description || '';
                            self.editing = false;
                            showToast('Synopsis saved');
                        } else {
                            self.descError = d.message || 'Save failed';
                        }
                    })
                    .catch(() => { self.saving = false; self.descError = 'Save failed'; });
            },
            fetchDescription() {
                this.fetching = true;
                var self = this;
                fetch('/api/series/' + encodeURIComponent(this.seriesName) + '/fetch-description', {
                    method: 'POST',
                    headers: { 'X-CSRF-Token': window.csrfToken() }
                })
                    .then(r => r.json())
                    .then(d => {
                        self.fetching = false;
                        if (d.ok) {
                            self.description = d.description || '';
                            showToast('Synopsis fetched from Hardcover');
                        } else if (d.empty) {
                            // Hardcover simply has no description for this
                            // series — a normal outcome, not a failure. Open
                            // the editor so writing one is the obvious step.
                            showToast(d.message, 'info');
                            self.startEdit();
                        } else {
                            showToast(d.message || 'No synopsis found', 'error');
                        }
                    })
                    .catch(() => { self.fetching = false; showToast('Fetch failed', 'error'); });
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
