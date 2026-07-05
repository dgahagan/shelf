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
