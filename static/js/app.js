// --- Scan result card: the one reader of a scan fragment ---
//
// Both consumers of `fragments/scan_result.html` — the typed/Enter path's
// toast below, and the camera overlay in scan.js — used to re-derive the
// outcome by substring-matching Tailwind class names out of the raw HTML
// (`html.indexOf('bg-shelf-warning')`) and to pull fields by first-match-in-
// DOM-order (`.font-medium` -> title, `.text-sm.text-shelf-muted` -> authors).
//
// Two copies of the same guess, and both are one class token away from being
// wrong: any element inside a *successful* card that happens to carry a
// `bg-shelf-warning` background flips the whole card to a failure, and any
// muted small-text paragraph added above the authors line becomes the author.
// The card now states its outcome outright in `data-scan-status`, and names
// each field it wants read. This function is the only place that knows how.
//
// Keep the status lists below in step with the badge's class ternary at the
// foot of fragments/scan_result.html — that template and this table are the
// two halves of one contract.
var SCAN_OK_STATUSES = [
    'added', 'wishlisted', 'returned', 'confirmed', 'marked_read',
    'checked_out', 'moved', 'found', 'relocated'
];
var SCAN_WARN_STATUSES = ['duplicate', 'already_checked_out', 'not_checked_out'];

function scanCardOutcome(root) {
    if (!root) return null;
    var status = root.getAttribute('data-scan-status') || '';
    var titleEl = root.querySelector('[data-scan-title]');
    var authorsEl = root.querySelector('[data-scan-authors]');
    var coverEl = root.querySelector('[data-scan-cover]');
    var badgeEl = root.querySelector('[data-scan-badge]');
    var detailEl = root.querySelector('[data-scan-detail]');
    return {
        status: status,
        ok: SCAN_OK_STATUSES.indexOf(status) !== -1,
        warn: SCAN_WARN_STATUSES.indexOf(status) !== -1,
        label: badgeEl ? badgeEl.textContent.trim() : '',
        title: titleEl ? titleEl.textContent.trim() : null,
        authors: authorsEl ? authorsEl.textContent.trim() : null,
        detail: detailEl ? detailEl.textContent.trim() : null,
        cover: coverEl ? coverEl.getAttribute('src') : null
    };
}

// --- Toast notifications ---
function showToast(message, type) {
    var container = document.getElementById('toast-container');
    var colors = {success: 'bg-shelf-success', error: 'bg-shelf-error', warning: 'bg-shelf-warning'};
    var el = document.createElement('div');
    el.className = (colors[type] || 'bg-shelf-accent') + ' text-white px-4 py-2 rounded-lg shadow-lg text-sm font-medium transition-opacity duration-300';
    el.textContent = message;
    container.appendChild(el);
    setTimeout(function() { el.style.opacity = '0'; }, 2700);
    setTimeout(function() { el.remove(); }, 3000);
}

// Listen for HX-Trigger showToast events from server
document.body.addEventListener('showToast', function(e) {
    var d = e.detail || {};
    showToast(d.message || 'Done', d.type || 'success');
});

// --- Loading bar ---
(function() {
    var bar = document.getElementById('htmx-indicator');
    document.body.addEventListener('htmx:beforeRequest', function() {
        bar.style.opacity = '1';
        bar.style.width = (30 + Math.random() * 30) + '%';
    });
    document.body.addEventListener('htmx:afterRequest', function() {
        bar.style.width = '100%';
        setTimeout(function() { bar.style.opacity = '0'; bar.style.width = '0'; }, 300);
    });
})();

// --- Keyboard shortcuts ---
document.addEventListener('keydown', function(e) {
    var tag = document.activeElement.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (e.key === '/' ) { e.preventDefault(); var q = document.querySelector('[name="q"]'); if (q) q.focus(); }
    else if (e.key === 's') { window.location.href = '/scan'; }
    else if (e.key === 'b') { window.location.href = '/browse'; }
    else if (e.key === '?') { document.getElementById('shortcut-modal').classList.toggle('hidden'); }
});

// --- Search-result form sync ---
// Replaces the inline scripts formerly embedded in the book/dvd/game
// search-result fragments (inline scripts cannot execute under the CSP).
document.body.addEventListener('htmx:afterSwap', function() {
    var loc = document.getElementById('location');
    var plat = document.getElementById('platform');
    if (loc) {
        document.querySelectorAll('.book-loc-sync, .dvd-loc-sync, .game-loc-sync').forEach(function(el) {
            el.value = loc.value;
        });
    }
    if (plat) {
        document.querySelectorAll('.game-platform-sync').forEach(function(el) {
            el.value = plat.value;
        });
    }
});

// CSP: hx-on:: attributes and hx-vals='js:...' need unsafe-eval, which the CSP
// forbids — equivalent behavior via delegated listeners keyed by data attributes.
htmx.config.allowEval = false;

document.body.addEventListener('htmx:afterRequest', function (evt) {
    var el = evt.detail.elt;
    if (!el || !el.getAttribute) return;
    var action = el.getAttribute('data-after-request');
    if (!action) return;
    var ok = evt.detail.successful;
    if (action === 'clear-scan-input') {
        var input = el.querySelector('#isbn-input');
        if (input) { input.value = ''; input.focus(); }
        // This handler is the SOLE owner of the typed-scan toast: /api/scan
        // sets no HX-Trigger on any branch, so nothing else toasts here.
        // Typed/Enter entry has no camera overlay and the result card lands in
        // #scan-results below the fold, so without this the submit looks like
        // a silent no-op. The card is the only input — see GOTCHAS "When
        // adding a response branch to /api/scan".
        var card = ok && document.querySelector('#scan-results > :first-child');
        if (card) {
            var outcome = scanCardOutcome(card);
            var errMsg = card.querySelector('.text-shelf-error:not(span)');
            var label = outcome.label || 'Done';
            // The badge reads lower-case ('added', 'lent'); the toast is a
            // sentence, so it opens capitalised the way the server string did.
            label = label.charAt(0).toUpperCase() + label.slice(1);
            // A card is a failure when its own status says so — not when some
            // element inside it happens to be styled with a warning colour.
            var isErr = !outcome.ok;
            var text;
            if (errMsg) {
                text = errMsg.textContent.trim();
            } else {
                text = label + (outcome.title ? ': ' + outcome.title : '');
                // The detail line carries the second party the title cannot:
                // the borrower on a lend, the destination on a move.
                if (outcome.detail) text += ' — ' + outcome.detail;
            }
            showToast(text, isErr ? 'warning' : 'success');
        }
    } else if (action === 'clear-title-search' && ok) {
        var si = document.getElementById('title-search-input') || document.getElementById('game-search-input');
        if (si) si.value = '';
        var sr = document.getElementById('title-search-results') || document.getElementById('game-search-results');
        if (sr) sr.innerHTML = '';
    } else if (action === 'reload' && ok) {
        location.reload();
    } else if (action === 'goto-browse' && ok) {
        window.location = '/browse';
    }
});

// Replaces hx-vals='js:...' on the recent-scans panel (dynamic localStorage value)
document.body.addEventListener('htmx:configRequest', function (evt) {
    var el = evt.detail.elt;
    if (el && el.getAttribute && el.getAttribute('data-vals-scan-mode') !== null) {
        evt.detail.parameters.mode = localStorage.getItem('shelf_scan_mode') || 'add';
    }
});
