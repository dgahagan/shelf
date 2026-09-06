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
        const panel = document.getElementById('romm-panel');
        if (!panel) return;
        const url = document.getElementById('romm-url');
        const publicUrl = document.getElementById('romm-public-url');
        const token = document.getElementById('romm-token');
        const clearToken = document.getElementById('romm-clear-token');
        const status = document.getElementById('romm-status');
        const platforms = document.getElementById('romm-platforms');
        const savePlatforms = document.getElementById('romm-save-platforms');
        const progress = document.getElementById('romm-progress');
        const progressBar = document.getElementById('romm-progress-bar');
        const result = document.getElementById('romm-result');

        function setStatus(message, ok) {
            status.textContent = message || '';
            status.className = 'text-xs h-5 ' + (ok === true ? 'text-shelf-success' : ok === false ? 'text-shelf-error' : 'text-shelf-muted');
        }
        async function loadStatus() {
            const data = await readJSON('/api/romm/status');
            if (!data.ok) return;
            url.value = data.url || '';
            publicUrl.value = data.public_url || '';
            token.placeholder = data.token_saved ? 'Saved — leave blank to keep' : 'RomM client API token';
        }

        document.getElementById('romm-save').addEventListener('click', async function () {
            setStatus('Saving…');
            const data = await readJSON('/api/romm/settings', {
                method: 'POST', headers: csrfHeaders({'Content-Type': 'application/json'}),
                body: JSON.stringify({url: url.value, public_url: publicUrl.value, token: token.value, clear_token: clearToken.checked})
            });
            setStatus(data.message || (data.ok ? 'Saved' : 'Save failed'), Boolean(data.ok));
            if (data.ok) { token.value = ''; clearToken.checked = false; await loadStatus(); }
        });

        document.getElementById('romm-test').addEventListener('click', async function () {
            setStatus('Testing connection…');
            const data = await readJSON('/api/romm/test', {
                method: 'POST', headers: csrfHeaders({'Content-Type': 'application/json'}),
                body: JSON.stringify({url: url.value, token: token.value})
            });
            setStatus(data.message || (data.ok ? 'Connected' : 'Connection failed'), Boolean(data.ok));
        });

        function renderPlatforms(rows) {
            platforms.replaceChildren();
            rows.forEach(function (row) {
                const wrapper = document.createElement('label');
                wrapper.className = 'flex items-center gap-3 bg-shelf-bg rounded-lg px-3 py-2';
                wrapper.dataset.platformId = row.id;
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox'; checkbox.checked = row.included !== false;
                checkbox.className = 'rounded border-shelf-border'; checkbox.dataset.role = 'included';
                const name = document.createElement('span');
                name.className = 'flex-1 min-w-0 text-sm text-shelf-text'; name.textContent = row.name;
                const count = document.createElement('span');
                count.className = 'text-xs text-shelf-muted';
                count.textContent = Number.isInteger(row.rom_count) ? row.rom_count + ' games' : '';
                wrapper.append(checkbox, name, count);
                platforms.appendChild(wrapper);
            });
            savePlatforms.hidden = rows.length === 0;
        }

        document.getElementById('romm-load-platforms').addEventListener('click', async function () {
            platforms.textContent = 'Loading platforms…';
            const data = await readJSON('/api/romm/platforms');
            if (!data.ok) { platforms.textContent = data.message || 'Could not load platforms'; savePlatforms.hidden = true; return; }
            renderPlatforms(data.platforms || []);
        });

        savePlatforms.addEventListener('click', async function () {
            const rows = Array.from(platforms.querySelectorAll('[data-platform-id]')).map(function (node) {
                return {id: node.dataset.platformId, included: node.querySelector('[data-role="included"]').checked};
            });
            const data = await readJSON('/api/romm/platforms', {
                method: 'POST', headers: csrfHeaders({'Content-Type': 'application/json'}),
                body: JSON.stringify({platforms: rows})
            });
            setStatus(data.message || (data.ok ? 'Platform selection saved' : 'Save failed'), Boolean(data.ok));
        });

        document.getElementById('romm-sync').addEventListener('click', function () {
            result.textContent = ''; result.className = 'mt-2 text-xs text-shelf-muted';
            progress.textContent = 'Starting RomM sync…'; progressBar.style.width = '0%';
            const stream = new EventSource('/api/romm/sync/stream');
            stream.onmessage = function (event) {
                const data = JSON.parse(event.data);
                if (data.type === 'progress') {
                    const pct = data.total ? Math.round(data.current / data.total * 100) : 0;
                    progress.textContent = data.total ? data.current + ' / ' + data.total + ' — ' + data.title + ' (' + data.status + ')' : data.current + ' — ' + data.title + ' (' + data.status + ')';
                    if (data.total) progressBar.style.width = pct + '%';
                } else if (data.type === 'done') {
                    progressBar.style.width = '100%'; progress.textContent = 'Sync complete';
                    result.textContent = 'Created: ' + data.created + ', updated: ' + data.updated + ', skipped: ' + data.skipped + ', errors: ' + data.errors;
                    stream.close();
                } else if (data.type === 'error') {
                    progress.textContent = ''; result.textContent = data.message || 'Sync failed';
                    result.className = 'mt-2 text-xs text-shelf-error'; stream.close();
                }
            };
            stream.onerror = function () {
                result.textContent = 'Connection to sync stream was lost'; result.className = 'mt-2 text-xs text-shelf-error'; stream.close();
            };
        });
        loadStatus();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', onReady); else onReady();
})();
