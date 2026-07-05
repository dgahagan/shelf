function browsePage() {
    return {
        selectMode: false,
        selectedIds: [],
        bulkLocationVal: '',
        bulkTypeVal: '',
        bulkStatusVal: '',
        filterPills: [],
        viewMode: localStorage.getItem('shelf-view') || 'grid',
        filtersOpen: false,

        init() {
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
            this.syncFilters();
            // Sync filter pills and URL after every HTMX swap
            document.body.addEventListener('htmx:afterSettle', () => {
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
        },

        destroy() {
            if (this._keyHandler) document.removeEventListener('keydown', this._keyHandler);
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
            var names = ['q', 'media_type_filter', 'location_filter', 'sort', 'reading_status', 'owned', 'lent_out', 'tag'];
            names.forEach(function(name) {
                var el = document.querySelector('[name="' + name + '"]');
                if (!el) return;
                if (name === 'sort' && el.value === 'newest') return;
                if (el.value) params.set(name, el.value);
            });
            var qs = params.toString();
            var url = window.location.pathname + (qs ? '?' + qs : '');
            history.replaceState(null, '', url);
        },

        clearFilter(name) {
            var el = document.querySelector('[name="' + name + '"]');
            if (el) {
                el.value = name === 'sort' ? 'newest' : '';
                htmx.trigger(el, el.tagName === 'SELECT' ? 'change' : 'keyup');
            }
        },

        clearAllFilters() {
            var names = ['q', 'media_type_filter', 'location_filter', 'reading_status', 'owned', 'lent_out', 'tag'];
            names.forEach(function(name) {
                var el = document.querySelector('[name="' + name + '"]');
                if (el) el.value = '';
            });
            var sort = document.querySelector('[name="sort"]');
            if (sort) sort.value = 'newest';
            var trigger = document.querySelector('[name="media_type_filter"]') || document.querySelector('[name="q"]');
            if (trigger) htmx.trigger(trigger, 'change');
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
