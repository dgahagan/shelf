// Registered Alpine components (CSP-build compatible) — item/series/intake pages.
//
// Same rules as components.js: the Alpine CSP build cannot evaluate arrow
// functions, template literals, or globals (fetch/window/document/JSON/...)
// in template attributes, so that logic lives here. Jinja-templated initial
// state is passed via data-* attributes on the component root and read in
// init() from this.$el.dataset.
//
// Reaching the component's root element: $el is whichever element the running
// directive sits on, so it is the root only while a directive on the root
// itself evaluates — i.e. in init(). A method reached from a child's @click
// sees that child instead, and $root is no better here (it resolves to the
// nearest element carrying a data stack, which inside an x-for is the loop
// element). Both were live bugs: the copy-from picker silently copied nothing
// and the series filter chips filtered nothing. So components that need their
// root later capture it in init() via a closure variable from the Alpine.data
// factory, which runs once per component instance.

document.addEventListener('alpine:init', function () {

    // series.html — per-series card: Hardcover completeness check + add-to-wishlist
    Alpine.data('seriesCard', function () {
        // Alpine.data's factory runs once per component instance, so this is
        // per-card state that init() can fill and later methods can trust.
        var rootEl = null;
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
            // Completeness (issue #15): manual override, then the stored
            // Hardcover check. Both arrive as data-* attributes; '' means
            // "unknown", which is not the same as 0 or false.
            complete: false, hcMissing: null, hcCheckedAt: '', markingComplete: false,
            init() {
                rootEl = this.$el;
                this.seriesName = this.$el.dataset.seriesName || '';
                this.description = this.$el.dataset.description || '';
                this.itemCount = parseInt(this.$el.dataset.itemCount || '0', 10);
                this.complete = this.$el.dataset.complete === '1';
                var missing = this.$el.dataset.hcMissing;
                this.hcMissing = missing === '' || missing === undefined
                    ? null : parseInt(missing, 10);
                this.hcCheckedAt = this.$el.dataset.hcCheckedAt || '';
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
            // Cheapest truth first: a manual override wins, then a stored
            // Hardcover check. Local gap detection stays server-rendered as the
            // fallback, and an unknown series claims nothing either way.
            get isComplete() {
                return this.complete || this.hcMissing === 0;
            },
            get showMissing() {
                return !this.complete && this.hcMissing > 0;
            },
            get missingLabel() {
                var label = this.hcMissing + ' missing';
                // Stored counts age; saying when it was checked keeps an old
                // number from reading as current truth.
                return this.hcCheckedAt ? label + ' · checked ' + this.hcCheckedAt.slice(0, 10) : label;
            },
            get completeLabel() {
                if (this.markingComplete) return 'Saving…';
                return this.complete ? 'Unmark complete' : 'Mark complete';
            },
            toggleComplete() {
                this.markingComplete = true;
                var self = this;
                var target = this.complete ? '0' : '1';
                var body = new URLSearchParams();
                body.set('complete', target);
                fetch('/api/series/' + encodeURIComponent(this.seriesName) + '/complete', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRF-Token': window.csrfToken() },
                    body: body.toString()
                })
                    .then(r => r.json())
                    .then(d => {
                        self.markingComplete = false;
                        self.menuOpen = false;
                        if (!d.ok) { showToast(d.message || 'Failed', 'error'); return; }
                        showToast(d.complete ? 'Marked complete' : 'Completeness cleared');
                        // Reload for the same reason rename/disband do: the
                        // badge and the filter both read server-rendered state.
                        setTimeout(() => location.reload(), 600);
                    })
                    .catch(() => { self.markingComplete = false; showToast('Failed', 'error'); });
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
                var self = this;
                fetch('/api/series/check?name=' + encodeURIComponent(this.seriesName))
                    .then(r => r.json())
                    .then(d => {
                        self.checking = false;
                        if (!d.ok) { self.error = d.message; return; }
                        self.result = d;
                        // The server just cached this result, so reflect it on
                        // the badge now rather than making the user reload. The
                        // dataset is updated too — the filter chips read the
                        // card elements, not this component's state.
                        self.hcMissing = d.missing;
                        self.hcCheckedAt = '';
                        if (rootEl) {
                            rootEl.dataset.hcMissing = String(d.missing);
                            rootEl.dataset.hcTotal = String(d.total);
                        }
                    })
                    .catch(() => { self.checking = false; self.error = 'Check failed'; });
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

    // series.html — All | Complete | Incomplete chips (issue #15). The page
    // already renders every series, so this filters the rendered cards rather
    // than round-tripping to the server. Each card is its own Alpine component,
    // so the filter reads completeness off the card elements' data-* attributes
    // instead of reaching into their state.
    Alpine.data('seriesFilter', function () {
        var rootEl = null;
        return {
            filter: 'all',
            init() {
                rootEl = this.$el;
            },
            setFilter(value) {
                this.filter = value;
                if (!rootEl) return;
                var cards = rootEl.querySelectorAll('[data-testid="series-card"]');
                Array.prototype.forEach.call(cards, function (card) {
                    var missing = card.dataset.hcMissing;
                    // Unknown counts as incomplete: the library has no evidence
                    // the series is done, and claiming otherwise is the one
                    // thing the three-state model must never do.
                    var complete = card.dataset.complete === '1' ||
                        (missing !== '' && missing !== undefined && parseInt(missing, 10) === 0);
                    var show = value === 'all' || (value === 'complete' ? complete : !complete);
                    card.style.display = show ? '' : 'none';
                });
                // The Unassigned block (issue #31) is not a series and makes no
                // completeness claim either way, so it shows under All only — never
                // filed under Incomplete by the unknown-counts-as-incomplete rule above.
                var unassigned = rootEl.querySelector('[data-testid="unassigned-card"]');
                if (unassigned) unassigned.style.display = value === 'all' ? '' : 'none';
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

    // fragments/scan_result.html (status == 'not_found') — the manual entry
    // form, plus the "Copy from…" picker from issue #19. Several scan results
    // can sit on the page at once, so every DOM lookup is scoped to this
    // component's own root rather than the document.
    Alpine.data('manualAddForm', function () {
        var rootEl = null;
        return {
            showForm: true,
            copyQuery: '', suggestions: [], copiedFrom: '', copyError: '',
            searchTimer: 0,
            init() {
                rootEl = this.$el;
            },
            onCopyInput() {
                var self = this;
                clearTimeout(this.searchTimer);
                this.copyError = '';
                var q = this.copyQuery.trim();
                if (q.length < 2) { this.suggestions = []; return; }
                this.searchTimer = setTimeout(function () { self.search(q); }, 200);
            },
            search(q) {
                var self = this;
                fetch('/api/items/suggest?q=' + encodeURIComponent(q), {
                    headers: { 'X-CSRF-Token': window.csrfToken() }
                })
                    .then(r => r.json())
                    .then(d => { self.suggestions = Array.isArray(d) ? d : []; })
                    .catch(() => { self.suggestions = []; });
            },
            pick(s) {
                var self = this;
                this.suggestions = [];
                this.copyQuery = '';
                this.copyError = '';
                fetch('/api/items/' + s.id + '/copy-template', {
                    headers: { 'X-CSRF-Token': window.csrfToken() }
                })
                    .then(r => r.json())
                    .then(d => {
                        if (!d || d.error) { self.copyError = 'Could not copy from that item.'; return; }
                        self.applyTemplate(d);
                        self.copiedFrom = s.title;
                    })
                    .catch(() => { self.copyError = 'Could not copy from that item.'; });
            },
            applyTemplate(d) {
                var root = rootEl;
                if (!root) return;
                // media_type is deliberately not applied: it is a hidden field
                // set by the scanner for THIS scan, and the platform select is
                // rendered from it server-side, so overwriting it here would
                // silently change the saved type with nothing on screen to show
                // it. Everything else in the copy-template payload prefills.
                var names = ['authors', 'publisher', 'publish_year', 'platform',
                             'series_name', 'location_id'];
                names.forEach(function (name) {
                    if (d[name] === null || d[name] === undefined) return;
                    var el = root.querySelector('[name="' + name + '"]');
                    if (el) el.value = d[name];
                });
            }
        };
    });

});
