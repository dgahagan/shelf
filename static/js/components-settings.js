// Registered Alpine components for settings.html (CSP-build compatible).
//
// The Alpine CSP build cannot evaluate arrow functions, template literals,
// or globals (fetch/window/document/JSON/Math/localStorage/...) in template
// attributes — any logic needing those lives here as Alpine.data components,
// and templates reference plain method/property names. Keep new template
// expressions CSP-safe: scripts/check_alpine_csp.py fails the build otherwise.
//
// Jinja-templated initial state is passed via data-* attributes on the
// component root and read in init() from this.$el.dataset.

document.addEventListener('alpine:init', function () {

    // settings.html — tab bar (persists active tab in localStorage)
    Alpine.data('settingsTabs', function () {
        return {
            tab: 'library',
            init() {
                this.tab = localStorage.getItem('shelf_settings_tab') || 'library';
            },
            setTab(name) {
                this.tab = name;
                localStorage.setItem('shelf_settings_tab', name);
            }
        };
    });

    // settings.html — Lending card (notification test button)
    Alpine.data('lendingPanel', function () {
        return {
            ntTesting: false, ntStatus: false,
            notifySaved: false,
            init() {
                this.notifySaved = this.$el.dataset.notifySaved === '1';
            },
            testNotify() {
                if (!this.notifySaved && !this.$refs.notifyUrl.value) return;
                this.ntTesting = true; this.ntStatus = false;
                fetch('/api/settings/notify-test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window.csrfToken() },
                    body: JSON.stringify({ url: this.$refs.notifyUrl.value, format: this.$refs.notifyFormat.value })
                }).then(r => r.json())
                  .then(d => { this.ntStatus = d; this.ntTesting = false; })
                  .catch(() => { this.ntStatus = { ok: false, message: 'Request failed' }; this.ntTesting = false; });
            }
        };
    });

    // settings.html — Audiobookshelf sync card
    Alpine.data('absSync', function () {
        return {
            syncing: false, result: false, absStatus: false, absTesting: false, showAbsHelp: false,
            absUrl: '', absToken: '', absSaved: false,
            syncCurrent: 0, syncTotal: 0, syncLastTitle: '', syncLog: [], showSyncLog: false,
            init() {
                this.absUrl = this.$el.dataset.absUrl || '';
                this.absSaved = this.$el.dataset.absSaved === '1';
            },
            get syncPct() { return Math.round(this.syncCurrent / this.syncTotal * 100) + '%'; },
            testAbs() {
                if (!this.absUrl || (!this.absToken && !this.absSaved)) return;
                this.absTesting = true; this.absStatus = false;
                fetch('/api/sync/audiobookshelf/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window.csrfToken() },
                    body: JSON.stringify({ url: this.absUrl, token: this.absToken })
                }).then(r => r.json())
                  .then(d => { this.absStatus = d; this.absTesting = false; })
                  .catch(() => { this.absStatus = { ok: false, message: 'Connection failed' }; this.absTesting = false; });
            },
            startSync() {
                this.syncing = true; this.result = false; this.syncCurrent = 0; this.syncTotal = 0;
                this.syncLastTitle = ''; this.syncLog = []; this.showSyncLog = false;
                var self = this;
                var es = new EventSource('/api/sync/audiobookshelf/stream');
                es.onmessage = function (e) {
                    var d = JSON.parse(e.data);
                    if (d.type === 'progress') {
                        self.syncCurrent = d.current;
                        self.syncTotal = d.total;
                        self.syncLastTitle = d.title;
                        self.syncLog.push({i: d.current, t: d.title, s: d.status});
                    } else if (d.type === 'done') {
                        self.result = d; self.syncing = false; es.close();
                    } else if (d.type === 'error') {
                        self.result = {error: d.message}; self.syncing = false; es.close();
                    }
                };
                es.onerror = function () { self.result = {error: 'Connection lost'}; self.syncing = false; es.close(); };
            }
        };
    });

    // settings.html — Audiobookshelf library selection (nested inside absSync)
    Alpine.data('absLibraries', function () {
        return {
            libs: false, libsLoading: false, libsError: false, libsSaving: false, cleaning: false, cleanResult: false,
            excludedIds() { return this.libs.filter(l => !l.included).map(l => l.id); },
            loadLibs() {
                this.libsLoading = true; this.libsError = false;
                fetch('/api/sync/audiobookshelf/libraries')
                    .then(r => r.json())
                    .then(d => { if (d.ok) { this.libs = d.libraries } else { this.libsError = d.message } this.libsLoading = false })
                    .catch(() => { this.libsError = 'Failed to load libraries'; this.libsLoading = false });
            },
            saveLibs() {
                this.libsSaving = true;
                fetch('/api/sync/audiobookshelf/libraries', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': window.csrfToken()}, body: JSON.stringify({excluded: this.excludedIds()})})
                    .then(r => r.json())
                    .then(d => { this.libsSaving = false; if (d.ok) showToast('Library selection saved'); else showToast(d.message || 'Save failed', 'error') })
                    .catch(() => { this.libsSaving = false; showToast('Save failed', 'error') });
            },
            cleanup() {
                if (!confirm('Remove all Shelf items that came from unchecked libraries? Audiobookshelf itself is not touched, and re-checking a library re-imports them on the next sync.')) return;
                this.cleaning = true; this.cleanResult = false;
                // Persist the current selection first so the cleanup matches what's on screen
                fetch('/api/sync/audiobookshelf/libraries', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': window.csrfToken()}, body: JSON.stringify({excluded: this.excludedIds()})})
                    .then(() => fetch('/api/sync/audiobookshelf/libraries/cleanup', {method: 'POST', headers: {'X-CSRF-Token': window.csrfToken()}}))
                    .then(r => r.json())
                    .then(d => { this.cleaning = false; this.cleanResult = d; if (d.ok) showToast('Removed ' + d.deleted + ' items') })
                    .catch(() => { this.cleaning = false; showToast('Cleanup failed', 'error') });
            }
        };
    });

    // settings.html — Hardcover card (test / import / export)
    Alpine.data('hardcoverPanel', function () {
        return {
            hcStatus: false, hcTesting: false, showHcHelp: false,
            hcToken: '', hcSaved: false,
            importing: false, importResult: false,
            importCurrent: 0, importTotal: 0, importLastTitle: '',
            importLog: [], showImportLog: false,
            importOverwrite: false,
            hcStatuses: { 1: true, 2: true, 3: true, 4: true, 5: true },
            exporting: false, exportResult: false,
            exportCurrent: 0, exportTotal: 0, exportLastTitle: '',
            exportLog: [], showExportLog: false,
            exportOwnedOnly: true,
            init() {
                this.hcSaved = this.$el.dataset.hcSaved === '1';
            },
            get importPct() { return Math.round(this.importCurrent / this.importTotal * 100) + '%'; },
            get exportPct() { return Math.round(this.exportCurrent / this.exportTotal * 100) + '%'; },
            testHc() {
                if (!this.hcToken && !this.hcSaved) return;
                this.hcTesting = true; this.hcStatus = false;
                fetch('/api/hardcover/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window.csrfToken() },
                    body: JSON.stringify({ token: this.hcToken })
                }).then(r => r.json())
                  .then(d => { this.hcStatus = d; this.hcTesting = false; })
                  .catch(() => { this.hcStatus = { ok: false, message: 'Connection failed' }; this.hcTesting = false; });
            },
            startExport() {
                this.exporting = true; this.exportResult = false;
                this.exportCurrent = 0; this.exportTotal = 0;
                this.exportLastTitle = ''; this.exportLog = []; this.showExportLog = false;
                var url = '/api/hardcover/export/stream?owned=' + (this.exportOwnedOnly ? '1' : '');
                var self = this;
                var es = new EventSource(url);
                es.onmessage = function (e) {
                    var d = JSON.parse(e.data);
                    if (d.type === 'progress') {
                        self.exportCurrent = d.current;
                        self.exportTotal = d.total;
                        self.exportLastTitle = d.title;
                        self.exportLog.push({i: d.current, t: d.title, s: d.status});
                    } else if (d.type === 'done') {
                        self.exportResult = d; self.exporting = false; es.close();
                    } else if (d.type === 'error') {
                        self.exportResult = {error: d.message}; self.exporting = false; es.close();
                    }
                };
                es.onerror = function () { self.exportResult = {error: 'Connection lost'}; self.exporting = false; es.close(); };
            },
            startImport() {
                this.importing = true; this.importResult = false;
                this.importCurrent = 0; this.importTotal = 0;
                this.importLastTitle = ''; this.importLog = []; this.showImportLog = false;
                var sel = Object.entries(this.hcStatuses).filter(e => e[1]).map(e => e[0]).join(',');
                var url = '/api/hardcover/import/stream?statuses=' + sel + '&overwrite=' + this.importOverwrite;
                var self = this;
                var es = new EventSource(url);
                es.onmessage = function (e) {
                    var d = JSON.parse(e.data);
                    if (d.type === 'progress') {
                        self.importCurrent = d.current;
                        self.importTotal = d.total;
                        self.importLastTitle = d.title;
                        if (d.current > 0) {
                            self.importLog.push({i: d.current, t: d.title, s: d.status});
                        }
                    } else if (d.type === 'done') {
                        self.importResult = d; self.importing = false; es.close();
                    } else if (d.type === 'error') {
                        self.importResult = {error: d.message}; self.importing = false; es.close();
                    }
                };
                es.onerror = function () { self.importResult = {error: 'Connection lost'}; self.importing = false; es.close(); };
            }
        };
    });

    // settings.html — Collection Valuation card (ISBNdb)
    Alpine.data('valuationPanel', function () {
        return {
            valuating: false, valResult: false, keyStatus: false, testing: false, showHelp: false,
            apiKey: '', apiKeySaved: false,
            valCurrent: 0, valTotal: 0, valLastTitle: '', valLog: [], showValLog: false,
            init() {
                this.apiKeySaved = this.$el.dataset.apiKeySaved === '1';
            },
            get valPct() { return Math.round(this.valCurrent / this.valTotal * 100) + '%'; },
            testKey() {
                if (!this.apiKey && !this.apiKeySaved) return;
                this.testing = true; this.keyStatus = false;
                fetch('/api/valuate/test-key', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window.csrfToken() },
                    body: JSON.stringify({ key: this.apiKey })
                }).then(r => r.json())
                  .then(d => { this.keyStatus = d; this.testing = false; })
                  .catch(() => { this.keyStatus = { ok: false, message: 'Connection failed' }; this.testing = false; });
            },
            startValuation() {
                this.valuating = true; this.valResult = false; this.valCurrent = 0; this.valTotal = 0;
                this.valLastTitle = ''; this.valLog = []; this.showValLog = false;
                var self = this;
                var es = new EventSource('/api/valuate/stream');
                es.onmessage = function (e) {
                    var d = JSON.parse(e.data);
                    if (d.type === 'progress') {
                        self.valCurrent = d.current; self.valTotal = d.total;
                        self.valLastTitle = d.title;
                        self.valLog.push({i: d.current, t: d.title, s: d.status});
                    } else if (d.type === 'done') {
                        self.valResult = d; self.valuating = false; es.close();
                    } else if (d.type === 'error') {
                        self.valResult = {message: d.message}; self.valuating = false; es.close();
                    }
                };
                es.onerror = function () { self.valResult = {message: 'Connection lost'}; self.valuating = false; es.close(); };
            }
        };
    });

    // settings.html — TMDb card (test key button)
    Alpine.data('tmdbPanel', function () {
        return {
            tmdbStatus: false, tmdbTesting: false, showTmdbHelp: false,
            tmdbKey: '', tmdbSaved: false,
            init() {
                this.tmdbSaved = this.$el.dataset.tmdbSaved === '1';
            },
            testTmdb() {
                if (!this.tmdbKey && !this.tmdbSaved) return;
                this.tmdbTesting = true; this.tmdbStatus = false;
                fetch('/api/tmdb/test-key', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window.csrfToken() },
                    body: JSON.stringify({ key: this.tmdbKey })
                }).then(r => r.json())
                  .then(d => { this.tmdbStatus = d; this.tmdbTesting = false; })
                  .catch(() => { this.tmdbStatus = { ok: false, message: 'Connection failed' }; this.tmdbTesting = false; });
            }
        };
    });

    // settings.html — IGDB card (test credentials button)
    Alpine.data('igdbPanel', function () {
        return {
            igdbStatus: false, igdbTesting: false, showIgdbHelp: false,
            igdbId: '', igdbSecret: '', igdbSaved: false,
            init() {
                this.igdbSaved = this.$el.dataset.igdbSaved === '1';
            },
            testIgdb() {
                if ((!this.igdbId || !this.igdbSecret) && !this.igdbSaved) return;
                this.igdbTesting = true; this.igdbStatus = false;
                fetch('/api/igdb/test-key', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': window.csrfToken() },
                    body: JSON.stringify({ client_id: this.igdbId, client_secret: this.igdbSecret })
                }).then(r => r.json())
                  .then(d => { this.igdbStatus = d; this.igdbTesting = false; })
                  .catch(() => { this.igdbStatus = { ok: false, message: 'Connection failed' }; this.igdbTesting = false; });
            }
        };
    });

    // settings.html — Maintenance card (cover retry + synopsis backfill)
    Alpine.data('maintenancePanel', function () {
        return {
            retrying: false, retryResult: false,
            retryCurrent: 0, retryTotal: 0, retryLastTitle: '', retryLog: [], showRetryLog: false,
            synFetching: false, synResult: false,
            synCurrent: 0, synTotal: 0, synLastTitle: '', synLog: [], showSynLog: false,
            get retryPct() { return Math.round(this.retryCurrent / this.retryTotal * 100) + '%'; },
            get synPct() { return Math.round(this.synCurrent / this.synTotal * 100) + '%'; },
            startRetry() {
                this.retrying = true; this.retryResult = false; this.retryCurrent = 0; this.retryTotal = 0;
                this.retryLastTitle = ''; this.retryLog = []; this.showRetryLog = false;
                var self = this;
                var es = new EventSource('/api/covers/bulk-retry/stream');
                es.onmessage = function (e) {
                    var d = JSON.parse(e.data);
                    if (d.type === 'progress') {
                        self.retryCurrent = d.current; self.retryTotal = d.total;
                        self.retryLastTitle = d.title;
                        self.retryLog.push({i: d.current, t: d.title, s: d.status});
                    } else if (d.type === 'done') {
                        self.retryResult = d; self.retrying = false; es.close();
                    } else if (d.type === 'error') {
                        self.retryResult = {error: d.message}; self.retrying = false; es.close();
                    }
                };
                es.onerror = function () { self.retryResult = {error: 'Connection lost'}; self.retrying = false; es.close(); };
            },
            startSynopses() {
                this.synFetching = true; this.synResult = false; this.synCurrent = 0; this.synTotal = 0;
                this.synLastTitle = ''; this.synLog = []; this.showSynLog = false;
                var self = this;
                var es = new EventSource('/api/synopses/backfill/stream');
                es.onmessage = function (e) {
                    var d = JSON.parse(e.data);
                    if (d.type === 'progress') {
                        self.synCurrent = d.current; self.synTotal = d.total;
                        self.synLastTitle = d.title;
                        self.synLog.push({i: d.current, t: d.title, s: d.status});
                    } else if (d.type === 'done') {
                        self.synResult = d; self.synFetching = false; es.close();
                    } else if (d.type === 'error') {
                        self.synResult = {error: d.message}; self.synFetching = false; es.close();
                    }
                };
                es.onerror = function () { self.synResult = {error: 'Connection lost'}; self.synFetching = false; es.close(); };
            }
        };
    });

    // settings.html — CSV import card
    Alpine.data('csvImportPanel', function () {
        return {
            importResult: false, importing: false,
            doImport(e) {
                this.importing = true; this.importResult = false;
                fetch('/api/import/csv', { method: 'POST', body: new FormData(e.target), headers: { 'X-CSRF-Token': window.csrfToken() } })
                    .then(r => r.json())
                    .then(d => { this.importResult = d; this.importing = false; })
                    .catch(() => { this.importResult = { error: 'Import failed' }; this.importing = false; });
            }
        };
    });

    // settings.html — Sharing card (copy-link buttons)
    Alpine.data('sharePanel', function () {
        return {
            copied: false,
            copyLink(token, id) {
                var self = this;
                navigator.clipboard.writeText(location.origin + '/share/' + token).then(function () {
                    self.copied = id;
                    setTimeout(function () { self.copied = false; }, 1500);
                });
            }
        };
    });

    // settings.html — Backup & Restore card
    Alpine.data('backupRestore', function () {
        return {
            restoreResult: false, restoring: false,
            doRestore(e) {
                this.restoring = true; this.restoreResult = false;
                fetch('/api/settings/restore', { method: 'POST', headers: { 'X-CSRF-Token': window.csrfToken() }, body: new FormData(e.target) })
                    .then(r => r.json())
                    .then(d => { this.restoreResult = d; this.restoring = false; })
                    .catch(() => { this.restoreResult = { error: 'Restore failed' }; this.restoring = false; });
            }
        };
    });

    // settings.html — Users tab (user management)
    Alpine.data('usersPanel', function () {
        return {
            users: [],
            newUser: { username: '', display_name: '', password: '', role: 'viewer' },
            addResult: false,
            async loadUsers() {
                const r = await fetch('/api/users');
                this.users = await r.json();
            },
            async addUser() {
                this.addResult = false;
                const form = new FormData();
                form.append('username', this.newUser.username);
                form.append('display_name', this.newUser.display_name);
                form.append('password', this.newUser.password);
                form.append('role', this.newUser.role);
                const r = await fetch('/api/users', { method: 'POST', headers: { 'X-CSRF-Token': window.csrfToken() }, body: form });
                const d = await r.json();
                this.addResult = d;
                if (d.ok) {
                    this.newUser = { username: '', display_name: '', password: '', role: 'viewer' };
                    await this.loadUsers();
                }
            },
            async updateRole(id, role) {
                const form = new FormData();
                form.append('role', role);
                const r = await fetch('/api/users/' + id + '/role', { method: 'POST', headers: { 'X-CSRF-Token': window.csrfToken() }, body: form });
                const d = await r.json();
                if (!d.ok) alert(d.message);
                else await this.loadUsers();
            },
            async resetPassword(id) {
                const pw = prompt('Enter new password (min 8 characters):');
                if (!pw) return;
                const form = new FormData();
                form.append('password', pw);
                const r = await fetch('/api/users/' + id + '/password', { method: 'POST', headers: { 'X-CSRF-Token': window.csrfToken() }, body: form });
                const d = await r.json();
                alert(d.message);
            },
            async deleteUser(id, name) {
                if (!confirm('Delete user ' + name + '?')) return;
                const r = await fetch('/api/users/' + id, { method: 'DELETE', headers: { 'X-CSRF-Token': window.csrfToken() } });
                const d = await r.json();
                if (!d.ok) alert(d.message);
                else await this.loadUsers();
            }
        };
    });

});
