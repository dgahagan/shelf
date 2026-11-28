# Roadmap

What Shelf is likely to grow next, grouped by theme.

**This is direction, not a schedule.** There are no dates and no order here. Items
move, merge and get dropped as the project learns things — a group appearing on
this page is not a commitment that it ships, and its absence is not a refusal.
For what actually shipped, read the [changelog](../CHANGELOG.md).

Some groups say what they wait on. Where that appears it is a technical fact —
the work genuinely reuses something built earlier — not a queue position.

**Status words:** *Planned* means the shape is settled and it is a matter of
building it. *Exploring* means the idea is accepted but the shape is not.

---

## Homelab integration

**Planned.** Shelf should behave like the rest of your stack. The first part shipped in 0.74.0.

- **API tokens and a dashboard document — shipped in 0.74.0.** Read-only
  tokens you make from your account menu, `GET /api/v1/stats`, and tested
  recipes for Homepage and Home Assistant. See
  [Homelab integration](homelab-integration.md).
- **The rest of the read API** — items, loans, wishlist, series and upcoming
  releases under the same tokens, with a published schema. A stable
  read-mostly surface that will not move under you.
- **SSO via OIDC** ([#89](https://github.com/dgahagan/shelf/issues/89)) — sign in
  with the identity provider you already run instead of Shelf keeping its own
  user list. This one builds on the account rework in *Multi-user & households*,
  so it follows that work.

If what you actually need is your reverse proxy handling the login (Authelia,
Authentik and friends), **trusted proxy-header auth** is part of the households
work below and arrives well before full OIDC.

## Multi-user & households

**Planned.** [#48](https://github.com/dgahagan/shelf/issues/48) — the largest
thing on this page, and it will arrive over several releases rather than one.

Today every account shares one library. The goal is that one Shelf install can
host several households — family, friends — each with its own complete library,
and each able to share it with chosen people at chosen access levels. Individual
items and whole locations can be marked private so they never appear to an
outside viewer. On top of that: borrow requests with a lending ledger, wishlists
visible for gift-buying with secret claims, and "who owns this?" search across
the households you can see.

Existing installs upgrade without noticing. Your data becomes the first
household, and nothing visibly changes until you invite someone.

## More sources and languages

**Planned.** Shelf's metadata is good if your books are English and in Open
Library, and thinner otherwise.

- **More national sources.** German ISBNs already route to the Deutsche
  Nationalbibliothek, French ones to the BnF, Italian ones to Italy's national
  network, Dutch ones to
  the Koninklijke Bibliotheek, Hungarian ones to OSZK NEKTÁR and Portuguese
  ones to PORBASE, Polish ones to the Biblioteka Narodowa, and Chinese-language ones to NeoDB. Other
  countries deserve the same.
- **A translated interface.** In progress. The translation machinery and the
  Italian and German catalogs have landed, and each user can pick a language
  or follow the browser; `SHELF_DEFAULT_LANGUAGE` sets the language for
  everyone else. More languages are welcome. The page shell (navigation, sign-in,
  setup, the Account dialog), Browse, the item page, item edit, manual add, Scan,
  Photo Intake, Shelf Fill and Store Mode are translated, and in Italian also
  Series, Upcoming, Discover, Music and Periodicals; the other feature pages
  follow area by area. Until then it sits behind `SHELF_I18N`, off by default.

## Reading life

**Planned.** Ratings, did-not-finish and paused states, re-reads, a reading
journal, yearly goals and an Up Next list. This needs per-user state to mean
anything, so it follows the households work.

## Alerts and discovery

**Exploring.**

- **New-release alerts for authors** you follow, beyond the series you watch
  (series watching and the Upcoming releases page shipped in 0.73.0).

---

## Recently shipped

The last five releases that changed something you can see. Some releases change
only security or foundations — 0.50.1 and 0.51.0 hardened sign-in and fixed a
security issue without changing what you see — and those are left out here
rather than listed as "nothing visible". Full detail on every release, visible
or not, is in the [changelog](../CHANGELOG.md).

| Version | What landed |
|---|---|
| [0.88.0](https://github.com/dgahagan/shelf/releases/tag/v0.88.0) | **Shelf under a URL sub-path.** `SHELF_BASE_PATH` serves Shelf at a path such as `/shelf` behind a proxy that strips it — several apps on one host, or Home Assistant ingress — with every link, redirect, cookie and Store Mode's offline copy kept under it. |
| [0.87.0](https://github.com/dgahagan/shelf/releases/tag/v0.87.0) | **The navigation bar groups its tabs.** Series, Music, Periodicals and Stats sit under **Explore**, Photo Intake and Shelf Fill under **Tools**, so the bar fits at every desktop width in every language Shelf ships. This completes *Choose your features*, which began with the Features page and setup profiles in 0.55.0. |
| [0.86.0](https://github.com/dgahagan/shelf/releases/tag/v0.86.0) | **Polish books from Poland's national library.** 978-83 ISBNs are looked up in the Biblioteka Narodowa before the general sources. Also: the Audiobookshelf and Hardcover settings in Italian. Contributed by @sudo-rpaisley and @vjandrea. |
| [0.85.0](https://github.com/dgahagan/shelf/releases/tag/v0.85.0) | **The HTTPS certificate renews itself.** A certificate Shelf generated is replaced on start when it expires within 30 days or no longer matches `CERT_SAN`, so changing `CERT_SAN` now takes effect on restart. A certificate you placed yourself is never replaced. |
| [0.84.1](https://github.com/dgahagan/shelf/releases/tag/v0.84.1) | **Restarts leave your files alone.** The container now changes ownership only of files under `/data` whose owner is wrong, so restarts and upgrades no longer make backup tools re-read every cover, and storage where root cannot change ownership no longer stops a correctly owned install from starting. |
---

## Suggesting something

Open a [feature request](https://github.com/dgahagan/shelf/issues/new?template=feature_request.yml).
Requests are read and answered, and the ones that fit get folded into a group
above — several already have. Saying what problem you are trying to solve helps
more than proposing a solution, because the fix that lands is often not the one
first suggested.

This page is not a voting board and requests are not ranked by how many people
ask. Shelf is one person's project, built for a real collection.
