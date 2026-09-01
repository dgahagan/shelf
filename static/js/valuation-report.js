// The valuation report is served under Shelf's strict script-src 'self' CSP.
// Keep print behaviour in this external script: inline onclick handlers are
// blocked by the browser and otherwise leave the visible print control inert.
(function () {
    var printButton = document.querySelector('[data-print-page]');
    if (!printButton) return;

    printButton.addEventListener('click', function () {
        window.print();
    });
})();
