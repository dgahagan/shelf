(function () {
    function csrfHeaders(extra) {
        return Object.assign({'X-CSRF-Token': window.csrfToken()}, extra || {});
    }

    async function readJSON(url, options) {
        try {
            const response = await fetch(url, options);
            return await response.json();
        } catch (_) {
            return {ok: false, message: 'Request failed'};
        }
    }

    function onReady() {
        const panel = document.getElementById('komga-panel');
        if (!panel) return;

        const url = document.getElementById('komga-url');
        const publicUrl = document.getElementById('komga-public-url');
        const apiKey = document.getElementById('komga-api-key');
        const clearKey = document.getElementById('komga-clear-key');
        const status = document.getElementById('komga-status');
        const libraries = document.getElementById('komga-libraries');
        const saveLibraries = document.getElementById('komga-save-libraries');
        const progress = document.getElementById('komga-progress');
        const progressBar = document.getElementById('komga-progress-bar');
        const result = document.getElementById('komga-result');

        function setStatus(message, ok) {
            status.textContent = message || '';
            status.className = 'text-xs h-5 ' + (ok === true ? 'text-shelf-success' : ok === false ? 'text-shelf-error' : 'text-shelf-muted');
        }

        async function loadStatus() {
            const data = await readJSON('/api/komga/status');
            if (!data.ok) return;
            url.value = data.url || '';
            publicUrl.value = data.public_url || '';
            apiKey.placeholder = data.api_key_saved ? 'Saved — leave blank to keep' : 'Komga API key';
        }

        document.getElementById('komga-save').addEventListener('click', async function () {
            setStatus('Saving…');
            const data = await readJSON('/api/komga/settings', {
                method: 'POST',
                headers: csrfHeaders({'Content-Type': 'application/json'}),
                body: JSON.stringify({
                    url: url.value,
                    public_url: publicUrl.value,
                    api_key: apiKey.value,
                    clear_api_key: clearKey.checked
                })
            });
            setStatus(data.message || (data.ok ? 'Saved' : 'Save failed'), Boolean(data.ok));
            if (data.ok) {
                apiKey.value = '';
                clearKey.checked = false;
                await loadStatus();
            }
        });

        document.getElementById('komga-test').addEventListener('click', async function () {
            setStatus('Testing connection…');
            const data = await readJSON('/api/komga/test', {
                method: 'POST',
                headers: csrfHeaders({'Content-Type': 'application/json'}),
                body: JSON.stringify({url: url.value, api_key: apiKey.value})
            });
            setStatus(data.message || (data.ok ? 'Connected' : 'Connection failed'), Boolean(data.ok));
        });

        function renderLibraries(rows) {
            libraries.replaceChildren();
            rows.forEach(function (row) {
                const wrapper = document.createElement('div');
                wrapper.className = 'flex flex-wrap items-center gap-3 bg-shelf-bg rounded-lg px-3 py-2';
                wrapper.dataset.libraryId = row.id;

                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.checked = row.included !== false;
                checkbox.className = 'rounded border-shelf-border';
                checkbox.dataset.role = 'included';

                const name = document.createElement('span');
                name.className = 'flex-1 min-w-0 text-sm text-shelf-text';
                name.textContent = row.name;

                const select = document.createElement('select');
                select.className = 'bg-shelf-card border border-shelf-border rounded-lg px-2 py-1 text-xs text-shelf-text';
                select.dataset.role = 'kind';
                ['comic', 'manga'].forEach(function (kind) {
                    const option = document.createElement('option');
                    option.value = kind;
                    option.textContent = kind === 'manga' ? 'Manga' : 'Comics';
                    option.selected = row.kind === kind;
                    select.appendChild(option);
                });

                wrapper.append(checkbox, name, select);
                libraries.appendChild(wrapper);
            });
            saveLibraries.hidden = rows.length === 0;
        }

        document.getElementById('komga-load-libraries').addEventListener('click', async function () {
            libraries.textContent = 'Loading libraries…';
            const data = await readJSON('/api/komga/libraries');
            if (!data.ok) {
                libraries.textContent = data.message || 'Could not load libraries';
                saveLibraries.hidden = true;
                return;
            }
            renderLibraries(data.libraries || []);
        });

        saveLibraries.addEventListener('click', async function () {
            const rows = Array.from(libraries.querySelectorAll('[data-library-id]')).map(function (node) {
                return {
                    id: node.dataset.libraryId,
                    included: node.querySelector('[data-role="included"]').checked,
                    kind: node.querySelector('[data-role="kind"]').value
                };
            });
            const data = await readJSON('/api/komga/libraries', {
                method: 'POST',
                headers: csrfHeaders({'Content-Type': 'application/json'}),
                body: JSON.stringify({libraries: rows})
            });
            setStatus(data.message || (data.ok ? 'Library selection saved' : 'Save failed'), Boolean(data.ok));
        });

        document.getElementById('komga-sync').addEventListener('click', function () {
            result.textContent = '';
            progress.textContent = 'Starting Komga sync…';
            progressBar.style.width = '0%';
            const stream = new EventSource('/api/komga/sync/stream');
            stream.onmessage = function (event) {
                const data = JSON.parse(event.data);
                if (data.type === 'progress') {
                    const pct = data.total ? Math.round(data.current / data.total * 100) : 0;
                    progress.textContent = data.current + ' / ' + data.total + ' — ' + data.title + ' (' + data.status + ')';
                    progressBar.style.width = pct + '%';
                } else if (data.type === 'done') {
                    progressBar.style.width = '100%';
                    progress.textContent = 'Sync complete';
                    result.textContent = 'Created: ' + data.created + ', adopted: ' + data.adopted + ', updated: ' + data.updated + ', skipped: ' + data.skipped + ', errors: ' + data.errors;
                    stream.close();
                } else if (data.type === 'error') {
                    progress.textContent = '';
                    result.textContent = data.message || 'Sync failed';
                    result.className = 'mt-2 text-xs text-shelf-error';
                    stream.close();
                }
            };
            stream.onerror = function () {
                result.textContent = 'Connection to sync stream was lost';
                result.className = 'mt-2 text-xs text-shelf-error';
                stream.close();
            };
        });

        loadStatus();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', onReady);
    } else {
        onReady();
    }
})();
