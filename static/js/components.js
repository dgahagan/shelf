// Registered Alpine components (CSP-build compatible).
//
// The Alpine CSP build cannot evaluate arrow functions, template literals,
// or globals (fetch/window/document/JSON/Math/localStorage/...) in template
// attributes — any logic needing those lives here as Alpine.data components,
// and templates reference plain method/property names. Keep new template
// expressions CSP-safe: scripts/check_alpine_csp.py fails the build otherwise.
//
// Jinja-templated initial state is passed via data-* attributes on the
// component root and read in init() from this.$el.dataset.

document.addEventListener('alpine:init', function () {

    // setup.html — first-run admin account form (password confirmation)
    Alpine.data('setupForm', function () {
        return {
            password: '', confirm: '',
            get mismatch() { return this.confirm && this.password !== this.confirm },
            get tooShort() { return this.password && this.password.length < 8 }
        };
    });

    // base.html — account modal (display name + password)
    Alpine.data('accountModal', function () {
        return {
            tab: 'profile',
            displayName: '',
            nameResult: false, nameSaving: false,
            current: '', newPw: '', confirm: '',
            pwResult: false, pwSaving: false,
            init() {
                this.displayName = this.$el.dataset.displayName || '';
            },
            get mismatch() { return this.confirm && this.newPw !== this.confirm },
            get tooShort() { return this.newPw && this.newPw.length < 8 },
            async saveName() {
                this.nameSaving = true; this.nameResult = false;
                const form = new FormData();
                form.append('display_name', this.displayName);
                const r = await fetch('/api/account/display-name', { method: 'POST', headers: { 'X-CSRF-Token': window.csrfToken() }, body: form });
                this.nameResult = await r.json();
                this.nameSaving = false;
                if (this.nameResult.ok) setTimeout(() => location.reload(), 800);
            },
            async savePw() {
                this.pwSaving = true; this.pwResult = false;
                const form = new FormData();
                form.append('current_password', this.current);
                form.append('new_password', this.newPw);
                const r = await fetch('/api/account/password', { method: 'POST', headers: { 'X-CSRF-Token': window.csrfToken() }, body: form });
                this.pwResult = await r.json();
                this.pwSaving = false;
                if (this.pwResult.ok) { this.current = ''; this.newPw = ''; this.confirm = ''; }
            }
        };
    });

});
