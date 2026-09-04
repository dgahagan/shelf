# Contributing to Shelf

Thanks for your interest! Shelf is a personal project that I'm happy to share.
Here's what that means in practice:

- **Bug reports are very welcome.** Please use the issue templates and include
  your version, browser, and any relevant logs (Settings → Logs, or
  `docker compose logs shelf`).
- **Feature requests are welcome** — no promises. The
  [roadmap](docs/roadmap.md) follows what my own library needs first, but user
  reports regularly shape it, and requests that fit get folded into a group
  there. It carries no dates and no ordering on purpose.
- **Pull requests are considered**, and every open PR now gets a decision at
  each release — merged, or a comment saying what it is waiting on, or a
  comment saying why it is closed. That is a decision, not a review queue: it
  may still be "not yet". For anything bigger than a small fix, open an issue
  first so we can talk about the approach before you invest time.
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

## How PRs get merged here — please read this one

Shelf releases fast, sometimes **three times in a day**, and each release lands
on GitHub as one squashed commit that rewrites 20 or more files. That has a
consequence you did not cause and cannot see from the outside:

**A PR that sits for a few days will conflict, and the conflict is usually not
about your code.** It is your branch point being overwritten. When that happens
GitHub also cannot build a merge commit, so CI stops running and the PR looks
untested as well as stale. Neither is a judgement about the work.

What actually helps:

- **Branch from current `main`, and rebase rather than merge** if you are asked
  to. `git fetch origin && git rebase origin/main`.
- **One concern per PR.** A focused 40-line PR gets merged the same day. A
  roll-up of seven unrelated fixes cannot be reviewed as a unit, and if any one
  piece is wrong the whole thing waits — and by then it has conflicted.
- **Check it is not already fixed.** `main` moves faster than the release notes
  suggest. `git log --oneline -20` and a quick search for the symptom costs a
  minute and has saved whole PRs from being written against bugs fixed weeks
  earlier.
- **Claim an issue before you build.** Comment on it, or open one. I do a lot of
  work in private branches, and the only way you can know something is already
  in flight is if I tell you — so ask, and I will.
- **If the behaviour looks wrong, ask before changing it.** Some of what looks
  like a bug is deliberate, and the reasoning often lives in a code comment
  rather than the docs. One recent example: an unreadable barcode deliberately
  creates a placeholder row instead of being rejected, because the offline
  scanner drops a flushed code from its queue and rejecting it would silently
  lose a scan someone made in a shop. Worth a question first.

None of this applies to typo and docs fixes. Send those straight in.

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
- **Some files are generated — never hand-edit them.** `static/css/app.css` and
  the `SW_VERSION` constant in `static/sw.js` come from `make css`; the
  test-count badges in `README.md` come from `make badges`. Run the target and
  commit the result. If you rebase, regenerate these rather than replaying your
  old hunk — the service-worker stamp is a digest over the whole precache set,
  so a replayed stamp fails `make check-sw-version`.
- `GOTCHAS.md` lists the project's known traps; skim the headings before
  touching migrations, Alpine components, covers or the service worker.

Add a line under `[Unreleased]` in `CHANGELOG.md` for anything user-visible.
The PR template asks which checks you ran; fill it in.

## License

By contributing, you agree that your contributions are licensed under
[AGPL-3.0](LICENSE).
