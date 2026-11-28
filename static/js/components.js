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

    // base.html — responsive nav disclosure. Below lg the tab row is hidden
    // and this drives the hamburger dropdown; open state is per-page-load
    // (every tab is a full navigation), so there is nothing to persist.
    Alpine.data('navMenu', function () {
        return {
            open: false,
            toggle() { this.open = !this.open },
            close() { this.open = false }
        };
    });

    // base.html — one tab-row group disclosure (app.nav.nav_row decides which
    // tabs fold into one). Several roots live on one page, one per group; the
    // only page-level listener is the template's @keydown.escape.window, and
    // Escape closing every open group at once is intended (G91), so no host
    // marker is needed. Every member is a full navigation: nothing to persist.
    Alpine.data('navGroup', function () {
        return {
            open: false,
            toggle() { this.open = !this.open },
            close() { this.open = false },
            // Keyboard focus leaving the group closes it. Only a real element
            // outside the root counts: a null relatedTarget (a click on
            // something unfocusable, or Safari not focusing a clicked link)
            // is left to @click.outside, so it can't close the panel under
            // a click that is about to land on a member link.
            leave(e) {
                if (e.relatedTarget && !this.$root.contains(e.relatedTarget)) this.open = false;
            }
        };
    });

    // base.html — user menu. Settings belongs with the signed-in user rather
    // than consuming one of the library's primary navigation tabs. Account
    // still opens the existing profile/password modal.
    Alpine.data('accountMenu', function () {
        return {
            open: false,
            showAccount: false,
            toggle() { this.open = !this.open },
            close() { this.open = false },
            openAccount() { this.open = false; this.showAccount = true },
            closeAccount() { this.showAccount = false },
            closeAll() { this.open = false; this.showAccount = false },
            // As navGroup.leave: Tab past the last item closes the menu. The
            // account modal sits inside this root, so focus moving into it
            // never counts as leaving.
            leave(e) {
                if (e.relatedTarget && !this.$root.contains(e.relatedTarget)) this.open = false;
            }
        };
    });

    // base.html — account modal (display name + password)
    Alpine.data('accountModal', function () {
        return {
            tab: 'profile',
            displayName: '',
            language: '',
            savedLanguage: '',
            nameResult: false, nameSaving: false,
            current: '', newPw: '', confirm: '',
            pwResult: false, pwSaving: false,
            init() {
                this.displayName = this.$el.dataset.displayName || '';
                this.language = this.savedLanguage = this.$el.dataset.language || '';
            },
            get mismatch() { return this.confirm && this.newPw !== this.confirm },
            get tooShort() { return this.newPw && this.newPw.length < 8 },
            async saveName() {
                this.nameSaving = true; this.nameResult = false;
                const form = new FormData();
                form.append('display_name', this.displayName);
                const r = await fetch(shelfUrl('/api/account/display-name'), { method: 'POST', headers: { 'X-CSRF-Token': window.csrfToken() }, body: form });
                this.nameResult = await r.json();
                this.nameSaving = false;
                if (this.nameResult.ok) setTimeout(() => location.reload(), 800);
            },
            async saveLanguage() {
                const form = new FormData();
                form.append('language', this.language);
                // A refusal can be plain text (a CSRF 403), not our JSON.
                const r = await fetch(shelfUrl('/api/account/language'), { method: 'POST', headers: { 'X-CSRF-Token': window.csrfToken() }, body: form });
                const result = await r.json().catch(() => ({ ok: false }));
                if (result.ok) { location.reload(); return; }
                this.language = this.savedLanguage;
                showToast(result.message || t('Could not change the language'), 'error');
            },
            async savePw() {
                this.pwSaving = true; this.pwResult = false;
                const form = new FormData();
                form.append('current_password', this.current);
                form.append('new_password', this.newPw);
                const r = await fetch(shelfUrl('/api/account/password'), { method: 'POST', headers: { 'X-CSRF-Token': window.csrfToken() }, body: form });
                this.pwResult = await r.json();
                this.pwSaving = false;
                if (this.pwResult.ok) { this.current = ''; this.newPw = ''; this.confirm = ''; }
            }
        };
    });

});
