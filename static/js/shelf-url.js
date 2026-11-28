/*
 * shelfUrl / shelfPath — the URL sub-path Shelf is served under (#165).
 *
 * Behind a proxy that strips a prefix (Home Assistant ingress, a shared nginx
 * host), every URL a script requests must carry that prefix, or the browser
 * resolves it against the host root. The server renders the prefix into
 * <meta name="shelf-base">; it is "" when Shelf is served at the root.
 *
 *   fetch(shelfUrl('/api/items/' + id))      never  fetch('/api/items/' + id)
 *
 * `make check-base-path` enforces the rule. The meta tag is read at call time,
 * so this file may load before <body> exists. It registers no Alpine component.
 */
(function () {
    'use strict';

    function base() {
        var meta = document.querySelector('meta[name="shelf-base"]');
        return meta ? (meta.getAttribute('content') || '') : '';
    }

    // Prefix a root-relative path: shelfUrl('/browse') -> '/ingress/x/browse'.
    window.shelfUrl = function (path) {
        return base() + path;
    };

    // The current pathname with the prefix removed, for code that parses it.
    window.shelfPath = function () {
        var b = base();
        var p = window.location.pathname;
        if (b && p === b) return '/';
        if (b && p.indexOf(b + '/') === 0) return p.slice(b.length);
        return p;
    };
})();
