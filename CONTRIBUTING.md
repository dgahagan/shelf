# Contributing to Shelf

Thanks for your interest! Shelf is a personal project that I'm happy to share.
Here's what that means in practice:

- **Bug reports are very welcome.** Please use the issue templates and include
  your version, browser, and any relevant logs (Settings → Logs, or
  `docker compose logs shelf`).
- **Feature requests are welcome** — no promises. The roadmap follows what my
  own library needs first, but user reports regularly shape it.
- **Pull requests are considered**, but there's no SLA on review. For anything
  bigger than a small fix, open an issue first so we can talk about the
  approach before you invest time.
- **Docs fixes are always welcome** — the user guide lives in
  [`docs/`](docs/README.md); a typo PR needs no issue.

Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Development setup

```bash
git clone https://github.com/dgahagan/shelf.git
cd shelf
pip install -r requirements.txt
make setup                # dev deps, npm (Tailwind), Playwright Chromium
make dev                  # docker compose up -d --build
# or: DATA_DIR=./data-dev uvicorn app.main:app --reload
```

Full details — running, testing, project layout, the rules that bite — are in
[docs/development.md](docs/development.md) and
[docs/architecture.md](docs/architecture.md).

## Before you submit

```bash
make test        # unit + integration tests
make test-e2e    # Playwright E2E tests (starts its own server)
make checks      # dependency audit, license check, secret scan, CSRF lint, Alpine CSP lint
make css         # if you touched templates or Tailwind classes — commit the rebuilt CSS *and* static/sw.js
```

Notes:

- Unit and E2E tests **cannot** run in a single pytest invocation — use the
  Make targets, not raw `pytest`.
- Any raw `fetch()` call in frontend JS must send the `X-CSRF-Token` header
  (`make check-csrf` enforces this).
- Templates must stay compatible with the Alpine.js CSP build
  (`make check-alpine`) — in particular, guard a chain with a ternary
  (`x ? x.prop.length : ''`), never `&&`, which the CSP build evaluates
  eagerly and which therefore throws instead of guarding.
- E2E tests fail if a page leaves an uncaught browser error behind, even when
  the test's own assertions pass.
- `MIGRATIONS` in `app/database.py` is append-only — never edit or reorder an
  existing entry.
- No CDN references — all JS and CSS is vendored in `static/`.
- `GOTCHAS.md` lists the project's known traps; skim the headings before
  touching migrations, Alpine components, covers or the service worker.

Add a line under `[Unreleased]` in `CHANGELOG.md` for anything user-visible.
The PR template asks which checks you ran; fill it in.

## License

By contributing, you agree that your contributions are licensed under
[AGPL-3.0](LICENSE).
