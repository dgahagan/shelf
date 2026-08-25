function browsePage() {
    return {
        selectMode: false,
        selectedIds: [],
        showSelectTip: false,
        bulkLocationVal: '',
        bulkTypeVal: '',
        bulkStatusVal: '',
        bulkSeriesVal: '',
        filterPills: [],
        viewMode: localStorage.getItem('shelf-view') || 'grid',
        // viewMode is the only thing that decides whether the list view
        // exists at all; visibleCols only decides which cells of an existing
        // list are shown. Keep that distinction straight -- it is easy to
        // gate the wrong behaviour on the wrong flag.
        visibleCols: {},
        columnsOpen: false,
        filtersOpen: false,
        // Bound (x-model) to BOTH the mobile and desktop search inputs, which
        // share name="q". Keeping them in lockstep is what stops hx-include
        // from sending "q=typed&q=" — Starlette's QueryParams.get() returns the
        // LAST duplicate, so an empty second input used to wipe the search.
        searchQuery: '',

        init() {
            // Must run before anything else touches the DOM. visibleCols needs
            // a key for EVERY registered column before Alpine's first render:
            // a missing key makes visibleCols.foo evaluate undefined (renders
            // the column hidden -- wrong, but silent), while a missing object
            // throws on every row, because the Alpine CSP build's evaluator
            // dereferences a member on something that is == null.
            // loadColumns() always returns a complete object, so it must run
            // first rather than being populated lazily.
            this.visibleCols = this.loadColumns();
            this.searchQuery = this.$el.dataset.initialQuery || '';
            // Returning to a bare /browse re-applies the last filter set.
            // Falls through to the sort-only restore when there's nothing stored
            // (sessionStorage is per-tab, so a new tab always lands here).
            if (!this.restoreFilters()) this.restoreSort();
            this.syncFilters();
            // Sync filter pills and URL after every HTMX swap.
            // afterSwap, not afterSettle: htmx fires afterSettle on a 20ms
            // timer, so navigating away right after changing a filter cancels
            // it and the querystring never reaches sessionStorage — which
            // would silently defeat restoreFilters(). afterSwap fires on the
            // same elements, the same number of times, but synchronously.
            document.body.addEventListener('htmx:afterSwap', () => {
                // htmx does not re-process the filter selects swapped in via
                // hx-swap-oob — their change triggers die with the replaced
                // node, so every dropdown change after the first would
                // silently do nothing. Re-process any filter control htmx
                // doesn't know about (guarded, so live controls are
                // untouched).
                this.filterNames().forEach(function(name) {
                    document.querySelectorAll('[name="' + name + '"]').forEach(function(el) {
                        if (!el['htmx-internal-data']) htmx.process(el);
                    });
                });
                this.syncFilters();
                this.updateUrl();
            });
            // Persist sort preference on change
            document.querySelector('[name="sort"]')?.addEventListener('change', function(e) {
                localStorage.setItem('shelf-sort', e.target.value);
            });
            // Show keyboard shortcut hint on first visit
            if (!localStorage.getItem('shelf-shortcuts-seen')) {
                localStorage.setItem('shelf-shortcuts-seen', '1');
                setTimeout(function() { showToast('Press ? for keyboard shortcuts', 'info'); }, 1500);
            }
            // Browse-page keyboard shortcuts
            this._keyHandler = (e) => this.handleKey(e);
            document.addEventListener('keydown', this._keyHandler);
            this.watchGridForHtmx();
        },

        // Both branches of item_grid.html live inside <template x-if="viewMode
        // === ...">. Alpine clones that content into the DOM at runtime, and
        // htmx does not observe DOM mutations — it only wires elements it swaps
        // itself or that htmx.process() is called on. Without this, the
        // load-more sentinel's hx-trigger="revealed" is never registered and
        // infinite scroll silently does nothing, in EITHER view.
        watchGridForHtmx() {
            var grid = document.getElementById('item-grid');
            if (!grid || !window.MutationObserver) return;
            var observer = new MutationObserver(function(records) {
                records.forEach(function(rec) {
                    rec.addedNodes.forEach(function(node) {
                        // ELEMENT_NODE only; htmx.process ignores text nodes.
                        // Re-processing an already-wired element is a no-op for
                        // htmx, so overlapping mutations are safe.
                        if (node.nodeType === 1) htmx.process(node);
                    });
                });
            });
            observer.observe(grid, {childList: true, subtree: true});
            this._gridObserver = observer;
        },

        destroy() {
            if (this._keyHandler) document.removeEventListener('keydown', this._keyHandler);
            if (this._gridObserver) this._gridObserver.disconnect();
        },

        handleKey(e) {
            var tag = document.activeElement.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
                // Escape blurs the focused input
                if (e.key === 'Escape') { document.activeElement.blur(); e.preventDefault(); }
                return;
            }
            if (e.key === 'Escape') {
                if (this.selectMode) { this.selectMode = false; this.selectedIds = []; e.preventDefault(); }
            } else if (e.key === 'e') {
                this.selectMode = !this.selectMode;
                if (!this.selectMode) this.selectedIds = [];
                e.preventDefault();
            } else if (e.key === 'g') {
                this.setView(this.viewMode === 'grid' ? 'list' : 'grid');
                e.preventDefault();
            } else if (e.key === 'f') {
                this.filtersOpen = !this.filtersOpen;
                e.preventDefault();
            } else if (e.key === 'x' && !e.ctrlKey && !e.metaKey) {
                this.clearAllFilters();
                e.preventDefault();
            }
        },

        // The filter set, read from the JSON block browse.html renders from
        // app/browse_filters.py. It used to be a literal here, and had already
        // drifted -- it was missing 'view', so the view mode was dropped from
        // the URL and from restoreFilters(). Server and client now read the
        // same declaration.
        filterDefs() {
            if (this._filterDefs) return this._filterDefs;
            var el = document.getElementById('browse-filter-config');
            this._filterDefs = el ? JSON.parse(el.textContent) : [];
            return this._filterDefs;
        },

        // Every filter control, for the htmx re-process loop.
        filterNames() {
            return this.filterDefs().map(function(def) { return def.name; });
        },

        // The Browse list-view column set, read from the JSON block
        // browse.html renders from app/browse_columns.py. Same shape, same
        // cache-on-this, same reason as filterDefs() above -- one shared
        // declaration instead of a second hand-maintained list that drifts.
        columnDefs() {
            if (this._columnDefs) return this._columnDefs;
            var el = document.getElementById('browse-column-config');
            this._columnDefs = el ? JSON.parse(el.textContent) : [];
            return this._columnDefs;
        },

        // {name: bool} covering every registered column. Locked columns are
        // always true. A stored value only wins when the blob parses to a
        // plain object AND holds an actual boolean for that name -- anything
        // else (missing key, wrong type, unknown name) falls back to the
        // column's defaultOn. Unknown names in the stored blob are simply
        // never looked at.
        loadColumns() {
            var stored = null;
            try {
                var raw = localStorage.getItem('shelf-columns');
                var parsed = raw ? JSON.parse(raw) : null;
                // A blob that fails to parse, or parses to something other
                // than a plain object (null, an array, a string), is exactly
                // the failure class this is guarding against -- treat it the
                // same as "nothing stored" rather than letting it throw.
                if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) stored = parsed;
            } catch (e) { /* corrupt blob -- fall through to defaults */ }
            var cols = {};
            this.columnDefs().forEach(function(def) {
                if (def.locked) { cols[def.name] = true; return; }
                var val = stored ? stored[def.name] : undefined;
                cols[def.name] = typeof val === 'boolean' ? val : def.defaultOn;
            });
            return cols;
        },

        // Only the non-locked names are worth persisting -- locked columns
        // are always true and are cheaper to reconstruct than to store.
        //
        // Persists the object it is HANDED, not this.visibleCols, because a
        // second Browse tab is a concurrent writer of the same key: writing a
        // snapshot this tab loaded minutes ago would silently undo whatever
        // the other tab has changed since. toggleCol() passes a state built on
        // the latest stored value; resetColumns() deliberately replaces the
        // whole key and does not come through here at all.
        saveColumns(cols) {
            var out = {};
            this.columnDefs().forEach(function(def) {
                if (!def.locked) out[def.name] = cols[def.name];
            });
            localStorage.setItem('shelf-columns', JSON.stringify(out));
        },

        toggleCol(name) {
            var def = this.columnDefs().find(function(d) { return d.name === name; });
            if (!def || def.locked) return;
            // Flip against the LATEST stored state, not this tab's in-memory
            // copy -- two open Browse tabs each hold a complete object, so
            // toggling from a stale snapshot resurrects a column the other tab
            // hid. loadColumns() re-reads localStorage and already falls back
            // to defaults for anything missing or corrupt.
            var latest = this.loadColumns();
            var next = Object.assign({}, latest, {[name]: !latest[name]});
            // Reassign the whole object rather than mutate a key in place --
            // Alpine's reactivity only picks up a change when the object
            // identity itself changes.
            this.visibleCols = next;
            this.saveColumns(next);
        },

        resetColumns() {
            var cols = {};
            this.columnDefs().forEach(function(def) {
                cols[def.name] = def.locked ? true : def.defaultOn;
            });
            this.visibleCols = cols;
            // What was just built is definitionally the all-default set, so
            // there is nothing here worth persisting -- an absent key already
            // means "use defaults" in loadColumns(), and writing the defaults
            // out explicitly would just be a slower way of saying that.
            localStorage.removeItem('shelf-columns');
        },

        // Only those mirrored into the address bar, in the order updateUrl()
        // writes them. 'view' is excluded -- localStorage owns it.
        urlFilterNames() {
            return this.filterDefs()
                .filter(function(def) { return def.inUrl; })
                .map(function(def) { return def.name; });
        },

        // Write a value into every input sharing this name. Used instead of
        // assigning Alpine state alone because htmx serializes the form
        // synchronously on `change`, before Alpine flushes its DOM effects.
        setControlValue(name, value) {
            document.querySelectorAll('[name="' + name + '"]').forEach(function(el) {
                el.value = value;
            });
        },

        // Issue #13: the sort-only fallback set the select's value but fired the
        // request with htmx.trigger, which restoreFilters() had already learned
        // is unreliable at init time (see its comment below) -- so the dropdown
        // showed the saved sort while the rows stayed in the server's default
        // newest-first order. Same htmx.ajax route as restoreFilters().
        restoreSort() {
            // A sort in the URL was already honoured by the server-rendered page.
            if (new URLSearchParams(window.location.search).get('sort')) return;
            var saved = localStorage.getItem('shelf-sort');
            if (!saved || saved === 'newest') return;
            var sortEl = document.querySelector('[name="sort"]');
            if (!sortEl || !sortEl.querySelector('option[value="' + saved + '"]')) return;
            this.setControlValue('sort', saved);
            this.runSearch(new URLSearchParams({sort: saved}));
        },

        // Shared by restoreSort() and restoreFilters(). `view` is mandatory:
        // without it the server renders grid cards that get swapped into a list
        // table (the #7 bug).
        runSearch(params) {
            params.set('view', this.viewMode);
            htmx.ajax('GET', '/api/search?' + params.toString(),
                      {target: '#item-grid', swap: 'innerHTML'});
        },

        // Issue #8: filters lived only in DOM controls + history.replaceState,
        // so leaving Browse and coming back via a bare href="/browse" showed
        // stale control values with nothing re-applying them. updateUrl()
        // mirrors the querystring into sessionStorage; this replays it.
        // Returns true when a restore was performed (and a search fired).
        restoreFilters() {
            if (window.location.search) return false;
            var stored = sessionStorage.getItem('shelf-browse-qs');
            if (!stored) return false;
            var params = new URLSearchParams(stored);
            var applied = new URLSearchParams();
            var any = false;
            var self = this;
            this.urlFilterNames().forEach(function(name) {
                var val = params.get(name);
                if (val === null || val === '') return;
                if (name === 'q') {
                    self.searchQuery = val;
                    self.setControlValue('q', val);
                    applied.set('q', val);
                    any = true;
                    return;
                }
                var el = document.querySelector('[name="' + name + '"]');
                if (!el) return;
                // Skip stale values whose option no longer exists (deleted tag,
                // removed location) — otherwise the select silently blanks.
                if (el.tagName === 'SELECT') {
                    var match = Array.prototype.some.call(el.options, function(o) { return o.value === val; });
                    if (!match) return;
                }
                self.setControlValue(name, val);
                applied.set(name, val);
                any = true;
            });
            if (!any) return false;
            // htmx.ajax rather than htmx.trigger on a control: htmx wires its
            // listeners on DOMContentLoaded, which races Alpine's deferred
            // init, so a synthetic 'change' here can land before htmx is
            // listening and be silently dropped. This fires the identical
            // request (same target and swap as the filter controls) and the
            // response's OOB swaps still refresh the filter-count dropdowns.
            this.runSearch(applied);
            return true;
        },

        setView(mode) {
            this.viewMode = mode;
            localStorage.setItem('shelf-view', mode);
            // Re-trigger search to get correct template
            var trigger = document.querySelector('[name="media_type_filter"]') || document.querySelector('[name="q"]');
            if (trigger) htmx.trigger(trigger, 'change');
        },

        syncFilters() {
            var pills = [];
            this.filterDefs().forEach(function(def) {
                if (!def.chip) return;
                var el = document.querySelector('[name="' + def.name + '"]');
                if (!el || !el.value || el.value === (def.default || '')) return;
                var label;
                if (el.tagName === 'SELECT') {
                    var opt = el.options[el.selectedIndex];
                    label = opt ? opt.text.replace(/ \(\d+\)$/, '') : el.value;
                } else {
                    label = def.prefix ? def.prefix + ': ' + el.value : el.value;
                }
                if (def.prefix && el.tagName === 'SELECT') label = def.prefix + ': ' + label;
                pills.push({name: def.name, label: label});
            });
            this.filterPills = pills;
        },

        updateUrl() {
            var params = new URLSearchParams();
            this.urlFilterNames().forEach(function(name) {
                var el = document.querySelector('[name="' + name + '"]');
                if (!el) return;
                if (name === 'sort' && el.value === 'newest') return;
                if (el.value) params.set(name, el.value);
            });
            var qs = params.toString();
            var url = window.location.pathname + (qs ? '?' + qs : '');
            history.replaceState(null, '', url);
            // Session-scoped so filters survive a trip to /series and back,
            // but not a new browser session. restoreFilters() replays this.
            if (qs) sessionStorage.setItem('shelf-browse-qs', qs);
            else sessionStorage.removeItem('shelf-browse-qs');
        },

        clearFilter(name) {
            if (name === 'q') this.searchQuery = '';
            var def = this.filterDefs().find(function(d) { return d.name === name; });
            var el = document.querySelector('[name="' + name + '"]');
            if (el) {
                this.setControlValue(name, def ? def.clearTo : '');
                htmx.trigger(el, el.tagName === 'SELECT' ? 'change' : 'keyup');
            }
        },

        clearAllFilters() {
            var self = this;
            this.searchQuery = '';
            // clearTo is null for controls clearing must not touch (the
            // grid/list view), and 'newest' for sort -- which is why there is
            // no longer a hand-written exception for sort here.
            this.filterDefs().forEach(function(def) {
                if (def.clearTo === null) return;
                self.setControlValue(def.name, def.clearTo);
            });
            sessionStorage.removeItem('shelf-browse-qs');
            var trigger = document.querySelector('[name="media_type_filter"]') || document.querySelector('[name="q"]');
            if (trigger) htmx.trigger(trigger, 'change');
        },

        toggleSelectMode() {
            this.selectMode = !this.selectMode;
            if (!this.selectMode) this.selectedIds = [];
            localStorage.setItem('shelf-select-used', '1');
        },

        maybeShowSelectTip() {
            this.showSelectTip = !localStorage.getItem('shelf-select-used');
        },

        // item_row / item_card fragments: tap toggles selection in select
        // mode, otherwise navigates to the item detail page. Ctrl/cmd-click
        // opens a new tab instead (middle-click is handled natively by the
        // anchor markup, which never reaches this handler).
        openOrToggle(id, url, event) {
            if (this.selectMode) { this.toggleItem(id); return; }
            if (event && (event.ctrlKey || event.metaKey)) window.open(url, '_blank');
            else window.location = url;
        },

        toggleItem(id) {
            var idx = this.selectedIds.indexOf(id);
            if (idx >= 0) this.selectedIds.splice(idx, 1);
            else this.selectedIds.push(id);
        },

        selectAll() {
            var self = this;
            document.querySelectorAll('[data-item-id]').forEach(function(el) {
                var id = parseInt(el.dataset.itemId);
                if (self.selectedIds.indexOf(id) < 0) self.selectedIds.push(id);
            });
        },

        deselectAll() {
            this.selectedIds = [];
        },

        async bulkUpdate(updates) {
            if (!this.selectedIds.length) return;
            try {
                var resp = await fetch('/api/items/bulk-update', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': window.csrfToken()},
                    body: JSON.stringify({item_ids: this.selectedIds, updates: updates})
                });
                var data = await resp.json();
                if (data.ok) {
                    showToast('Updated ' + data.updated + ' items', 'success');
                    this.selectedIds = [];
                    location.reload();
                } else {
                    showToast(data.message || 'Update failed', 'error');
                }
            } catch (e) {
                showToast('Update failed: ' + e.message, 'error');
            }
        },

        async bulkDelete() {
            if (!confirm('Delete ' + this.selectedIds.length + ' items?')) return;
            for (var id of this.selectedIds) {
                await fetch('/api/items/' + id, {method: 'DELETE', headers: {'X-CSRF-Token': window.csrfToken()}});
            }
            showToast('Deleted ' + this.selectedIds.length + ' items', 'success');
            this.selectedIds = [];
            location.reload();
        }
    }
}

// CSP build has no global fallback — register so x-data="browsePage" resolves.
document.addEventListener('alpine:init', function () {
    Alpine.data('browsePage', browsePage);
});
