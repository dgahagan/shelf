(function () {
    function start() {
        const root = document.querySelector('[data-location-order]');
        if (!root || root.dataset.canEdit !== '1') return;
        const list = root.querySelector('[data-copy-list]');
        if (!list) return;
        const locationId = root.dataset.locationId;
        const status = root.querySelector('[data-order-status]');
        let dragging = null;

        function message(text, error) {
            if (!status) return;
            status.textContent = text || '';
            status.classList.toggle('text-shelf-error', !!error);
            status.classList.toggle('text-shelf-muted', !error);
        }

        function ids() {
            return Array.from(list.querySelectorAll('[data-copy-id]')).map(function (row) {
                return Number(row.dataset.copyId);
            });
        }

        list.querySelectorAll('[data-copy-id]').forEach(function (row) {
            row.addEventListener('dragstart', function () {
                dragging = row;
                row.setAttribute('aria-grabbed', 'true');
            });
            row.addEventListener('dragend', function () {
                row.removeAttribute('aria-grabbed');
                dragging = null;
                message('Order changed — save when ready.', false);
            });
        });

        list.addEventListener('dragover', function (event) {
            if (!dragging) return;
            event.preventDefault();
            const target = event.target.closest('[data-copy-id]');
            if (!target || target === dragging) return;
            const box = target.getBoundingClientRect();
            if (event.clientY < box.top + box.height / 2) {
                list.insertBefore(dragging, target);
            } else {
                list.insertBefore(dragging, target.nextSibling);
            }
        });

        async function post(path, body) {
            const response = await fetch(path, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': window.csrfToken()
                },
                body: JSON.stringify(body)
            });
            let data = {};
            try { data = await response.json(); } catch (_) {}
            if (!response.ok || !data.ok) throw new Error(data.message || 'Could not save shelf order');
            return data;
        }

        const save = root.querySelector('[data-save-order]');
        if (save) save.addEventListener('click', async function () {
            save.disabled = true;
            message('Saving…', false);
            try {
                await post('/api/locations/' + locationId + '/order', {copy_ids: ids()});
                message('Shelf order saved.', false);
            } catch (error) {
                message(error.message, true);
            } finally {
                save.disabled = false;
            }
        });

        root.querySelectorAll('[data-auto-order]').forEach(function (button) {
            button.addEventListener('click', async function () {
                button.disabled = true;
                message('Ordering…', false);
                try {
                    await post('/api/locations/' + locationId + '/auto-order', {sort_key: button.dataset.autoOrder});
                    window.location.reload();
                } catch (error) {
                    message(error.message, true);
                    button.disabled = false;
                }
            });
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
