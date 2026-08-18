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
        filtersOpen: false,
        // Bound (x-model) to BOTH the mobile and desktop search inputs, which
        // share name="q". Keeping them in lockstep is what stops hx-include
        // from sending "q=typed&q=" — Starlette's QueryParams.get() returns the
        // LAST duplicate, so an empty second input used to wipe the search.
        searchQuery: '',

        init() {
            this.searchQuery = this.$el.dataset.initialQuery || '';
            // Returning to a bare /browse re-applies the last filter set.
            // Falls through to the sort-only restore when there's nothing stored.
            if (!this.restoreFilters()) {
                // Restore sort preference from localStorage (only if no sort in URL)
                var urlSort = new URLSearchParams(window.location.search).get('sort');
                if (!urlSort) {
                    var saved = localStorage.getItem('shelf-sort');
                    if (saved) {
                        var sortEl = document.querySelector('[name="sort"]');
                        if (sortEl && sortEl.querySelector('option[value="' + saved + '"]')) {
                            sortEl.value = saved;
                            if (saved !== 'newest') htmx.trigger(sortEl, 'change');
                        }
                    }
                }
            }
            this.syncFilters();
            // Sync filter pills and URL after every HTMX swap.
            // afterSwap, not afterSettle: htmx fires afterSettle on a 20ms
            // timer, so navigating away right after changing a filter cancels
            // it and the querystring never reaches sessionStorage — which
            // would silently defeat restoreFilters(). afterSwap fires on the
            // same elements, the same number of times, but synchronously.
            document.body.addEventListener('htmx:afterSwap', () => {
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

        // Names of every filter control that participates in the querystring,
        // in the order updateUrl() writes them.
        filterNames() {
            return ['q', 'media_type_filter', 'location_filter', 'sort', 'reading_status', 'owned', 'lent_out', 'tag'];
        },

        // Write a value into every input sharing this name. Used instead of
        // assigning Alpine state alone because htmx serializes the form
        // synchronously on `change`, before Alpine flushes its DOM effects.
        setControlValue(name, value) {
            document.querySelectorAll('[name="' + name + '"]').forEach(function(el) {
                el.value = value;
            });
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
            this.filterNames().forEach(function(name) {
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
            applied.set('view', this.viewMode);
            // htmx.ajax rather than htmx.trigger on a control: htmx wires its
            // listeners on DOMContentLoaded, which races Alpine's deferred
            // init, so a synthetic 'change' here can land before htmx is
            // listening and be silently dropped. This fires the identical
            // request (same target and swap as the filter controls) and the
            // response's OOB swaps still refresh the filter-count dropdowns.
            htmx.ajax('GET', '/api/search?' + applied.toString(), {target: '#item-grid', swap: 'innerHTML'});
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
            var filterDefs = [
                {name: 'q', prefix: 'Search'},
                {name: 'media_type_filter', prefix: 'Type'},
                {name: 'location_filter', prefix: 'Location'},
                {name: 'owned', prefix: ''},
                {name: 'lent_out', prefix: ''},
                {name: 'reading_status', prefix: 'Status'},
                {name: 'tag', prefix: 'Tag'},
                {name: 'sort', prefix: 'Sort', skip: 'newest'},
            ];
            filterDefs.forEach(function(def) {
                var el = document.querySelector('[name="' + def.name + '"]');
                if (!el || !el.value || el.value === (def.skip || '')) return;
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
            this.filterNames().forEach(function(name) {
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
            var el = document.querySelector('[name="' + name + '"]');
            if (el) {
                this.setControlValue(name, name === 'sort' ? 'newest' : '');
                htmx.trigger(el, el.tagName === 'SELECT' ? 'change' : 'keyup');
            }
        },

        clearAllFilters() {
            var self = this;
            this.searchQuery = '';
            this.filterNames().forEach(function(name) {
                if (name !== 'sort') self.setControlValue(name, '');
            });
            this.setControlValue('sort', 'newest');
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
