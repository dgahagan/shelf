// Registered Alpine components (CSP-build compatible) — library pages.
//
// See components.js for the pattern: the Alpine CSP build cannot evaluate
// arrow functions, template literals, or globals in template attributes,
// so any logic needing those lives here as Alpine.data components.

document.addEventListener('alpine:init', function () {

    // logs.html — auto-refresh toggle (resubmits the filter form every 10s)
    Alpine.data('logsPage', function () {
        return {
            auto: false,
            interval: false,
            init() {
                this.$watch('auto', (v) => {
                    if (v) {
                        this.interval = setInterval(() => this.$refs.filters.requestSubmit(), 10000);
                    } else {
                        clearInterval(this.interval);
                    }
                });
            }
        };
    });

});
