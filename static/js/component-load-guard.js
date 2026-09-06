// Component-load guard — says so, once, when a component script did not run.
//
// Written in the same ES5 house style as the components*.js files it guards:
// no arrow functions, no template literals, `var` throughout. That is not
// about the Alpine CSP build (this file holds no template expressions) — it is
// so the four registering scripts and their guard read alike.
//
// Why this is its own file, loaded FIRST in every shell:
//
//  - A guard inside components.js could never report the loss of
//    components.js, and on setup.html that file registers the page's only
//    component — so the whole page would go silent with nothing to say.
//  - It installs a recording wrapper on Alpine.data from an `alpine:init`
//    listener. Listener order is registration order, so the guard must be
//    parsed before every script that registers, or it records nothing.
//
// scripts/check_alpine_csp.py enforces both halves of that position.

(function () {
    'use strict';

    // ---- Part 1: the closed declaration -----------------------------------
    // Every registrable component name -> the script under static/js/ that
    // registers it. This is what lets the message name a *file* rather than a
    // list of bindings. Keep it in step with the Alpine.data() calls; the
    // reconciler below ignores a name it does not know.
    var SCRIPTS = {
        setupForm: 'components.js',
        navMenu: 'components.js',
        accountModal: 'components.js',

        logsPage: 'components-library.js',

        seriesCard: 'components-item.js',
        seriesFilter: 'components-item.js',
        synopsisFetcher: 'components-item.js',
        hardcoverPush: 'components-item.js',
        hcResultCard: 'components-item.js',
        manualAddForm: 'components-item.js',

        settingsTabs: 'components-settings.js',
        lendingPanel: 'components-settings.js',
        absSync: 'components-settings.js',
        absLibraries: 'components-settings.js',
        hardcoverPanel: 'components-settings.js',
        valuationPanel: 'components-settings.js',
        googleBooksPanel: 'components-settings.js',
        tmdbPanel: 'components-settings.js',
        igdbPanel: 'components-settings.js',
        maintenancePanel: 'components-settings.js',
        csvImportPanel: 'components-settings.js',
        archivePanel: 'components-settings.js',
        sharePanel: 'components-settings.js',
        backupRestore: 'components-settings.js',
        usersPanel: 'components-settings.js',

        browsePage: 'browse.js',
        scanPage: 'scan.js',
        intakePage: 'intake.js',
        coverDrop: 'item_edit.js'
    };

    // The four page-scoped scripts each declare a named top-level function
    // matching their component, so `typeof window[name]` separates "the script
    // never executed" from "it executed but registered late". The 25 names in
    // the components*.js files are anonymous Alpine.data factories and are
    // never globals on any page — probing them says nothing.
    var PAGE_SCOPED = {
        browsePage: true, scanPage: true, intakePage: true, coverDrop: true
    };

    // Same bare-identifier rule scripts/check_alpine_csp.py's _XDATA_NAME
    // applies: x-data="{ expanded: false }" is an inline literal, not a name.
    var NAME_RE = /^[A-Za-z_$][A-Za-z0-9_$]*$/;

    // ---- Part 2: record what actually registers ---------------------------
    var recorded = {};
    var reportedScripts = {};
    var toastShown = false;

    // Read-only exports. The E2E harness (tests/e2e/conftest.py) reads both
    // rather than restating the declaration in Python: SCRIPTS says what
    // *should* have registered, the recorded set says what *did*.
    try {
        Object.defineProperty(window, '__shelfComponentScripts', {
            get: function () { return SCRIPTS; }
        });
        Object.defineProperty(window, '__shelfRecordedComponents', {
            get: function () { return Object.keys(recorded).sort(); }
        });
    } catch (e) { /* a frozen window is still better than no guard */ }

    document.addEventListener('alpine:init', function () {
        // Defensive throughout: a guard that throws here would take out every
        // registration in the app, which is worse than the failure it reports.
        try {
            if (!window.Alpine || typeof window.Alpine.data !== 'function') return;
            var original = window.Alpine.data;
            window.Alpine.data = function (name) {
                // Record only on a normal return. Recording first would let a
                // rejected call read back as a successful registration, and
                // the recorded set is the whole basis of the guard's verdict.
                var result = original.apply(this, arguments);
                try { recorded[name] = true; } catch (e) {}
                return result;
            };
        } catch (e) {}
    });

    // ---- Part 3: reconcile a root against the recorded set -----------------
    function describe(name) {
        if (!PAGE_SCOPED[name]) return name;
        var t;
        try { t = typeof window[name]; } catch (e) { t = 'unknown'; }
        if (t === 'function') {
            return name + ' (typeof window.' + name + ' is "function" — the ' +
                   'script executed but never registered the component)';
        }
        return name + ' (typeof window.' + name + ' is "' + t + '" — the ' +
               'script did not execute)';
    }

    function report(script, names) {
        var described = [];
        for (var i = 0; i < names.length; i++) described.push(describe(names[i]));
        // Must not contain the substring "Alpine Expression Error":
        // tests/e2e/conftest.py filters console text on exactly that to build
        // its Alpine-warning list, and this message is not one of those.
        console.error(
            '[shelf] component load failure: /static/js/' + script +
            ' did not register ' + described.join(', ') +
            '. This page is missing behaviour it needs. Reload it; if the ' +
            'message returns, report this line.'
        );
    }

    function notify() {
        // setup.html loads neither app.js nor #toast-container, so there is no
        // toast on that shell and the console message is the whole of what it
        // can say. showToast appends without a null check.
        if (toastShown) return;
        if (typeof showToast !== 'function') return;
        if (!document.getElementById('toast-container')) return;
        toastShown = true;
        showToast('This page did not load fully — reload it.', 'error');
    }

    function reconcile(root) {
        try {
            if (!root) return;
            var nodes = [];
            if (root.nodeType === 1 && root.hasAttribute &&
                root.hasAttribute('x-data')) {
                nodes.push(root);
            }
            if (root.querySelectorAll) {
                var found = root.querySelectorAll('[x-data]');
                for (var i = 0; i < found.length; i++) nodes.push(found[i]);
            }

            // Group by the script that owns the name: fourteen unresolved
            // bindings from one lost file are one message, not fourteen.
            var lost = {};
            for (var j = 0; j < nodes.length; j++) {
                var raw = nodes[j].getAttribute('x-data');
                if (!raw) continue;
                var name = raw.replace(/^\s+|\s+$/g, '');
                if (!NAME_RE.test(name)) continue;
                if (recorded[name]) continue;
                var script = SCRIPTS[name];
                if (!script) continue;
                if (!lost[script]) lost[script] = [];
                if (lost[script].indexOf(name) === -1) lost[script].push(name);
            }

            var scripts = Object.keys(lost);
            var newly = 0;
            for (var k = 0; k < scripts.length; k++) {
                if (reportedScripts[scripts[k]]) continue;
                reportedScripts[scripts[k]] = true;
                newly++;
                report(scripts[k], lost[scripts[k]]);
            }
            if (newly > 0) notify();
        } catch (e) {}
    }

    // The initial tree, after Alpine has walked it.
    document.addEventListener('alpine:initialized', function () {
        reconcile(document);
    });

    // And every HTMX swap target. Two components — hcResultCard and
    // manualAddForm — have no root at alpine:initialized on any page and
    // arrive only by swap, so losing components-item.js would otherwise scan
    // clean and then deliver a dead card in silence.
    //
    // G6: read the event's own target, never re-query the document. This
    // listens on `document` rather than `document.body` only because the guard
    // is parsed in <head>, before a body exists; htmx events bubble.
    document.addEventListener('htmx:afterSwap', function (event) {
        reconcile(event && event.detail ? event.detail.elt : null);
    });
})();
