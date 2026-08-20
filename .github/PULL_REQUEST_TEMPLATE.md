<!--
Thanks for contributing to Shelf! For anything bigger than a small fix,
please open an issue first so we can talk about the approach before you
invest time — see CONTRIBUTING.md.
-->

## What this changes

<!-- A sentence or two. If it fixes an open issue, add "Fixes #123". -->

## Why

<!-- What problem does this solve? For a bug, what was the user-visible
     symptom? Skip if the "what" already makes it obvious. -->

## How it was tested

<!-- Which of the checks below you ran, plus anything you exercised by hand
     (which browser, which device, which scanner). -->

- [ ] `make test` passes
- [ ] `make test-e2e` passes
- [ ] `make checks` passes
- [ ] `make css` re-run, if templates or Tailwind classes changed

## Notes for review

<!-- Anything you're unsure about, deliberately left out, or want a second
     opinion on. Screenshots or a short clip help a lot for UI changes. -->

---

A few project-specific things that are easy to trip over — see `GOTCHAS.md`:

- Unit and E2E tests **cannot** share a pytest invocation; use the Make targets.
- Raw `fetch()` calls must send the `X-CSRF-Token` header (`make check-csrf`).
- Templates must stay compatible with the Alpine.js **CSP build**
  (`make check-alpine`) — nested or bracketed `x-model` bindings silently
  drop input.
- `MIGRATIONS` in `app/database.py` is append-only. Never edit or reorder an
  existing entry; add a new one at the end.
- No CDN references — all JS and CSS is vendored in `static/`.
