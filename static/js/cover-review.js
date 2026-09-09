// The cover review queue's only script.
//
// RateLimitMiddleware (app/main.py) answers a throttled /api/ request with a
// bare 429 *before* the route runs, so this state cannot be rendered
// server-side. HTMX does not swap a non-2xx response, so without this handler
// a throttled reviewer's click does nothing visible at all — the worst
// possible feedback. Unhide a notice that is already in the page.
//
// No fetch() is added (check-csrf has nothing to see) and no Alpine directive
// (check-alpine likewise). The notice lives outside #cover-review, so it
// survives every card swap.
(function () {
    'use strict';
    var notice = document.getElementById('cover-review-throttled');
    if (!notice) { return; }

    document.body.addEventListener('htmx:responseError', function (evt) {
        if (evt.detail && evt.detail.xhr && evt.detail.xhr.status === 429) {
            notice.hidden = false;
        }
    });

    // Any successful exchange means the limiter let us through again.
    document.body.addEventListener('htmx:afterOnLoad', function (evt) {
        if (evt.detail && evt.detail.xhr && evt.detail.xhr.status < 400) {
            notice.hidden = true;
        }
    });
})();
