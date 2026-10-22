// The Duplicates page. Plain script, no Alpine component (so the G2/G4 Alpine
// traps do not apply): Shelf's strict script-src 'self' CSP blocks inline
// handlers, so merge, dismiss, restore and the fill highlight are wired here.
// Messages go in by textContent only: merge refusals carry record titles.
(function () {
    function all(root, selector) {
        return Array.prototype.slice.call(root.querySelectorAll(selector));
    }

    function memberId(card) { return parseInt(card.getAttribute('data-dup-member'), 10); }

    // Cards in ascending id order, whatever the DOM order is.
    function cardsOf(group) {
        return all(group, '[data-dup-member]').sort(function (a, b) { return memberId(a) - memberId(b); });
    }

    function keptCard(group) {
        var radio = group.querySelector('input[data-dup-keep]:checked');
        return radio ? radio.closest('[data-dup-member]') : null;
    }

    function fieldOf(card, name) {
        return card.querySelector('dd[data-field="' + name + '"]');
    }

    // Decision 12: for each field the kept card lacks, the first other card
    // (ascending id) that has it is the one the merge copies from.
    function highlight(group) {
        all(group, '[data-fill-active]').forEach(function (el) { el.removeAttribute('data-fill-active'); });
        all(group, '[data-fill-marker]').forEach(function (el) { el.classList.add('hidden'); });
        var kept = keptCard(group);
        if (!kept) return;
        var cards = cardsOf(group);
        var names = {};
        cards.forEach(function (card) {
            all(card, 'dd[data-field]').forEach(function (dd) { names[dd.getAttribute('data-field')] = true; });
        });
        Object.keys(names).forEach(function (name) {
            if (fieldOf(kept, name)) return;
            for (var i = 0; i < cards.length; i++) {
                if (cards[i] === kept) continue;
                var dd = fieldOf(cards[i], name);
                if (!dd) continue;
                dd.setAttribute('data-fill-active', '');
                var marker = dd.querySelector('[data-fill-marker]');
                if (marker) marker.classList.remove('hidden');
                break;
            }
        });
    }

    function failure(why) {
        return 'That did not go through (' + why + ') — reload and try again';
    }

    // Each mutating fetch spells its method out, so `make check-csrf` can see it.
    function postJson(url, payload) {
        return settle(fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json', 'X-CSRF-Token': window.csrfToken()},
            body: JSON.stringify(payload)
        }));
    }

    function deleteJson(url, payload) {
        return settle(fetch(url, {
            method: 'DELETE',
            headers: {'Content-Type': 'application/json', 'X-CSRF-Token': window.csrfToken()},
            body: JSON.stringify(payload)
        }));
    }

    // Resolves to {ok, message}; never rejects.
    function settle(pending) {
        return pending.then(function (resp) {
            return resp.json().then(
                function (data) { return {status: resp.status, data: data}; },
                function () { return {status: resp.status, data: null}; }
            );
        }).then(function (r) {
            if (r.data && r.data.ok === true) return {ok: true, message: ''};
            if (r.data && r.data.message) return {ok: false, message: String(r.data.message)};
            return {ok: false, message: failure('HTTP ' + r.status)};
        }, function () {
            return {ok: false, message: failure('network error')};
        });
    }

    function setBusy(scope, busy) {
        all(scope, '[data-dup-action]').forEach(function (btn) { btn.disabled = busy; });
    }

    function showError(scope, message) {
        var box = scope.querySelector('[data-dup-error]');
        if (box) box.textContent = message;
    }

    // Decision 14: a settled group leaves the page; an emptied list says so.
    function removeGroup(group) {
        var section = group.closest('[data-dup-list]');
        group.remove();
        if (section && !section.querySelector('[data-dup-group]')) {
            var empty = section.querySelector('[data-dup-empty]');
            if (empty) empty.classList.remove('hidden');
        }
    }

    function run(scope, request, onOk) {
        showError(scope, '');
        setBusy(scope, true);
        request.then(function (result) {
            if (result.ok) {
                onOk();
            } else {
                showError(scope, result.message);
                setBusy(scope, false);
            }
        });
    }

    function memberIds(group) {
        return cardsOf(group).map(memberId);
    }

    document.addEventListener('click', function (event) {
        var button = event.target.closest('[data-dup-action]');
        if (!button || button.disabled) return;
        var action = button.getAttribute('data-dup-action');

        if (action === 'restore') {
            var row = button.closest('[data-dup-dismissed]');
            if (!row) return;
            var pair = (button.getAttribute('data-pair') || '').split(',').map(function (s) { return parseInt(s, 10); });
            run(row, deleteJson('/api/duplicates/dismiss', {item_ids: pair}), function () {
                window.location.reload();
            });
            return;
        }

        var group = button.closest('[data-dup-group]');
        if (!group) return;
        var ids = memberIds(group);

        if (action === 'dismiss') {
            run(group, postJson('/api/duplicates/dismiss', {item_ids: ids}), function () { removeGroup(group); });
        } else if (action === 'merge') {
            var kept = keptCard(group);
            if (!kept) return;
            var keepId = memberId(kept);
            var mergeIds = ids.filter(function (id) { return id !== keepId; });
            var title = kept.getAttribute('data-title') || '';
            if (!window.confirm('Merge ' + ids.length + ' records into "' + title +
                    '"? The merged-away records cannot be restored.')) return;
            run(group, postJson('/api/items/merge', {keep_id: keepId, merge_ids: mergeIds}), function () { removeGroup(group); });
        }
    });

    document.addEventListener('change', function (event) {
        var radio = event.target.closest && event.target.closest('input[data-dup-keep]');
        if (!radio) return;
        var group = radio.closest('[data-dup-group]');
        if (group) highlight(group);
    });

    function init() { all(document, '[data-dup-group]').forEach(highlight); }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
