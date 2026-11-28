// Default-tags input: sticky value + type-filtered datalist suggestions.
//
// Wires every `input[data-tag-suggest]` on the page. A plain script, not an
// Alpine component: nothing here is reactive state, and every reader of the
// field (htmx, FormData, the catalog mirror in app.js) reads the DOM value,
// so a programmatic restore cannot leave an x-model stale (G91).
//
//   data-storage-key  localStorage key the value is restored from / saved to
//   data-type-source  optional selector of the media-type <select>; without
//                     one (Intake — the type is per row) every tag is offered
//   list              id of the <datalist> this script fills
//
// GET /api/tags is fetched once per page load; a type change re-filters the
// cached rows. A failed fetch leaves a working input with no suggestions.
(function () {
    'use strict';

    function storageGet(key) {
        try { return window.localStorage.getItem(key); } catch (e) { return null; }
    }

    function storageSet(key, value) {
        try { window.localStorage.setItem(key, value); } catch (e) { /* private mode */ }
    }

    // Rows for `type`: global + that type's own. With no type (or Auto) every
    // row, deduped by name, a user tag winning over a starter — the payload
    // lists user tags first, so first-seen wins.
    function rowsFor(rows, type) {
        var filtered = rows.filter(function (row) {
            return !type || type === 'auto' || row.media_type === null || row.media_type === type;
        });
        var seen = {};
        return filtered.filter(function (row) {
            var key = row.name.toLowerCase();
            if (seen[key]) return false;
            seen[key] = true;
            return true;
        });
    }

    function render(list, rows) {
        while (list.firstChild) list.removeChild(list.firstChild);
        rows.forEach(function (row) {
            // createElement, never innerHTML: a tag name is user text.
            var opt = document.createElement('option');
            opt.value = row.name;
            opt.setAttribute('data-media-type', row.media_type || '');
            opt.setAttribute('data-starter', row.starter ? 'true' : 'false');
            list.appendChild(opt);
        });
    }

    var payload = null;

    function loadPayload() {
        if (!payload) {
            payload = fetch(shelfUrl('/api/tags'), {
                headers: { 'X-CSRF-Token': window.csrfToken ? window.csrfToken() : '' }
            }).then(function (resp) {
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                return resp.json();
            }).then(function (data) {
                return (data && Array.isArray(data.tags)) ? data.tags : [];
            }).catch(function () {
                return [];
            });
        }
        return payload;
    }

    function wire(input) {
        var key = input.getAttribute('data-storage-key');
        if (key) {
            var saved = storageGet(key);
            if (saved !== null) input.value = saved;
            input.addEventListener('input', function () { storageSet(key, input.value); });
        }

        var list = document.getElementById(input.getAttribute('list') || '');
        if (!list) return;
        var sourceSel = input.getAttribute('data-type-source');
        var source = sourceSel ? document.querySelector(sourceSel) : null;

        function refresh() {
            loadPayload().then(function (rows) {
                render(list, rowsFor(rows, source ? source.value : ''));
            });
        }

        // Enter here means "done typing", not "scan": the field shares a form
        // with the barcode input, whose hidden default button would otherwise
        // submit a blank scan.
        input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') e.preventDefault();
        });

        refresh();
        if (source) source.addEventListener('change', refresh);
        // Some code sets the type select without firing `change`; re-filter
        // when the user comes to the field.
        input.addEventListener('focus', refresh);
    }

    function init() {
        document.querySelectorAll('input[data-tag-suggest]').forEach(wire);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
