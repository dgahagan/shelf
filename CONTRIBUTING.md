# Contributing to Shelf

Thanks for your interest! Shelf is a personal project that I'm happy to share.
Here's what that means in practice:

- **Bug reports are very welcome.** Please use the issue templates and include
  your version, browser, and any relevant logs (Settings → Logs, or
  `docker compose logs shelf`).
- **Feature requests are welcome** — no promises. The roadmap follows what my
  own library needs first.
- **Pull requests are considered**, but there's no SLA on review. For anything
  bigger than a small fix, open an issue first so we can talk about the
  approach before you invest time.

## Development Setup

```bash
git clone https://github.com/dgahagan/shelf.git
cd shelf
pip install -r requirements.txt -r requirements-dev.txt
make install-playwright   # one-time: headless Chromium for E2E tests
docker compose up -d      # or: uvicorn app.main:app --reload
```

## Before You Submit

Run the QA pipeline locally — there's a Makefile that orchestrates everything:

```bash
make test       # unit + integration tests
make test-e2e   # Playwright E2E tests (needs a live local server)
make checks     # dependency audit, license check, secret scan, CSRF lint, Alpine CSP lint
```

Notes:

- Unit and E2E tests **cannot** run in a single pytest invocation — use the
  Make targets, not raw `pytest`.
- Any raw `fetch()` call in frontend JS must send the `X-CSRF-Token` header
  (`make check-csrf` enforces this).
- Templates must stay compatible with the Alpine.js CSP build
  (`make check-alpine`).
- Run `make css` after changing templates or Tailwind classes — the stylesheet
  is built locally and vendored, no CDNs.

## License

By contributing, you agree that your contributions are licensed under
[AGPL-3.0](LICENSE).
