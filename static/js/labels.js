// The Labels page and label sheet. Plain script, no Alpine component: Shelf's
// strict script-src 'self' CSP blocks inline handlers, so print and the form
// helpers are wired here.
(function () {
    var PRESET_KEY = 'shelf-label-preset';
    var START_KEY = 'shelf-label-start';

    function remember(key, value) {
        try { window.localStorage.setItem(key, value); } catch (e) { /* private mode */ }
    }
    function recall(key) {
        try { return window.localStorage.getItem(key); } catch (e) { return null; }
    }

    // The sheet's Print button.
    var printButton = document.querySelector('[data-print-page]');
    if (printButton) {
        printButton.addEventListener('click', function () { window.print(); });
    }

    // The location picker shows, and submits, only for the "location" scope.
    Array.prototype.forEach.call(document.querySelectorAll('[data-labels-scope-form]'), function (form) {
        var box = form.querySelector('[data-labels-location]');
        if (!box) return;
        var select = box.querySelector('select');
        function sync() {
            var picked = form.querySelector('input[name="scope"]:checked');
            var on = !!picked && picked.value === 'location';
            box.classList.toggle('hidden', !on);
            select.disabled = !on;
            // Native validation stops a submit with no location picked, so the
            // Print half cannot open an empty sheet for a missing choice.
            select.required = on;
        }
        form.addEventListener('change', sync);
        sync();
    });

    // Remember the stock and start position between visits.
    var printForm = document.querySelector('[data-labels-print-form]');
    if (printForm) {
        var preset = printForm.querySelector('[data-labels-preset]');
        var start = printForm.querySelector('[data-labels-start]');
        var savedPreset = recall(PRESET_KEY);
        var known = Array.prototype.some.call(preset.options, function (o) { return o.value === savedPreset; });
        if (known) preset.value = savedPreset;
        var savedStart = recall(START_KEY);
        if (savedStart) start.value = savedStart;
        // The start position runs 1..labels-per-sheet of the chosen stock.
        function syncMax() {
            var opt = preset.options[preset.selectedIndex];
            var max = opt && opt.dataset.perSheet;
            if (!max) return;
            start.max = max;
            if (parseInt(start.value, 10) > parseInt(max, 10)) start.value = max;
        }
        syncMax();
        preset.addEventListener('change', function () { syncMax(); remember(PRESET_KEY, preset.value); });
        start.addEventListener('change', function () { remember(START_KEY, start.value); });
    }
})();
