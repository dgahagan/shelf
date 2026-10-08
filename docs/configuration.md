# Configuration

Shelf is configured in two places: **environment variables** for things the
container needs before it starts (port, certificate, secret keys), and the
**Settings page** (admin only) for everything else. You can run Shelf with no
configuration at all.

## Environment variables

Set these in your `.env` file next to `docker-compose.yml`, or with `-e` on
`docker run`.

| Variable | Default | Purpose |
|---|---|---|
| `CERT_SAN` | `DNS:shelf,DNS:localhost` | Subject Alternative Names for the self-signed certificate. Comma-separated `DNS:<name>` and `IP:<addr>` entries. Add your server's LAN IP and any hostname you'll type in the browser. Only read when the certificate is first generated — delete `data/certs/` to regenerate |
| `SHELF_PORT` | `18888` | Port the app listens on *inside* the container. Usually leave it and change the Compose port mapping instead |
| `SHELF_TLS` | `on` | `on` serves HTTPS with the self-signed certificate in `data/certs/` (generated if absent). `off` serves plain HTTP, for a reverse proxy that terminates TLS itself; existing certificates are left in place. Any other value stops the container at startup. Plain HTTP without a proxy loses the camera scanner and offline Store Mode — see [HTTPS and reverse proxies](https-and-reverse-proxy.md#plain-http-without-a-proxy) |
| `SHELF_TRUST_PROXY` | *(unset)* | The reverse proxy's address as Shelf sees it: comma-separated IPs or CIDRs (e.g. `127.0.0.1`, `172.17.0.1`, `10.0.0.5,127.0.0.1`), or `*`, which is not recommended. Forwarded headers are honoured only from these peers, and `X-Forwarded-For` is read from the right. The legacy `1` means `127.0.0.1` and prints a startup warning. Leave unset without a proxy. Outside Docker, set uvicorn's `FORWARDED_ALLOW_IPS` instead, since `entrypoint.sh` does the hand-over. See [HTTPS and reverse proxies](https-and-reverse-proxy.md#2-reverse-proxy-with-a-real-certificate) |
| `SECRET_KEY` | *(auto)* | JWT signing key. If unset, generated at `data/signing.key` (0600) on first start; an existing key from before 0.30 is moved there from the database on the first start after upgrading, so sessions survive. Set it explicitly to run several instances against one database |
| `SHELF_SESSION_DAYS` | `7` | How long a login lasts, in whole days, `1`–`365`. The window slides: a session in use is renewed once it is past half its length, so someone who opens Shelf every few days is never logged out. A value that is not a whole number in range logs a warning at startup and uses 7. Existing sessions keep their old expiry until their next renewal |
| `SHELF_ENCRYPTION_KEY` | *(auto)* | Key for API credentials stored in the database. If unset, generated at `data/encryption.key`. Set it (`openssl rand -hex 32`) so the data directory alone can't decrypt credentials |
| `DATA_DIR` | `/data` | Where the database, covers and certs live. Only relevant outside Docker |
| `SHELF_DISABLE_RATE_LIMIT` | *(unset)* | Turns off per-IP rate limiting. For tests and local development only |
| `SHELF_DISABLE_PRICE_ALERTS` | *(unset)* | Stops the nightly wishlist price-alert check from starting, so it never calls ISBNdb. For tests only — the test suite sets it; leave it unset in production |
| `SHELF_UPC_LOOKUP_URL` | *(unset)* | Overrides the UPC Item DB lookup endpoint. For the test suite only — the E2E gate points it at a local stub. Leave it unset in production; the trial endpoint is used and paced when it is |

### Credential overrides

Integration credentials are normally entered in Settings and stored encrypted.
Each can instead be supplied as an environment variable, which **takes
priority** over anything stored:

| Variable | Setting |
|---|---|
| `HARDCOVER_TOKEN` | Hardcover API token |
| `GOOGLE_BOOKS_API_KEY` | Optional Google Books API key; anonymous lookups remain available when unset |
| `ABS_URL`, `ABS_TOKEN` | Audiobookshelf server URL and API token |
| `ABS_PUBLIC_URL` | Optional browser-facing Audiobookshelf URL, for **Listen** / **Read** links only. Set it when `ABS_URL` is an internal Docker or LAN address |
| `ISBNDB_API_KEY` | ISBNdb key (valuation, price alerts) |
| `TMDB_API_KEY` | TMDb credential (DVD / Blu-ray metadata **and** DVD cover search) — either the 32-character v3 API Key or the v4 Read Access Token |
| `IGDB_CLIENT_ID`, `IGDB_CLIENT_SECRET` | Twitch developer credentials (video game metadata **and** game cover search — both fields are required) |

Useful with Docker secrets or a secrets manager. Vision-provider keys (Photo
Intake) are Settings-only.

A credential supplied this way behaves like a saved one everywhere it matters,
with two differences worth knowing:

- **The Settings field stays blank.** Shelf never echoes a *secret* back into the
  page, whether it came from the database or the environment. Blank does not mean
  unset — leave it blank and the environment value keeps working. (`ABS_URL` and
  `ABS_PUBLIC_URL` are a partial exception: a server URL is not a secret, so
  those fields show the value in use **when one was saved through the form**. A
  value supplied only by the environment still renders the field blank, while
  remaining in force — so if the field is empty but Audiobookshelf links work,
  the variable is what is driving them.)
- **Shelf cannot remove it.** The "Remove saved key" checkbox only deletes the
  stored row, and the environment variable still takes priority afterwards. To
  change or remove an env-supplied credential, change it in your environment and
  restart the container. The checkbox is hidden when the credential comes *only*
  from the environment; if you also saved one through the form earlier you will
  still see it, and clearing that row changes nothing while the variable is set.

**Test key** works against an env-supplied credential — you do not need to paste
a second copy into the form to check it.

## The Settings page

**Settings** (gear icon, admin only) has five tabs. This is where each option
lives:

### Library

| Card | Options |
|---|---|
| **Collection** | Display currency (20 choices; formatting only, never conversion). Preferred language for title searches |
| **Navigation** | Which tabs appear in the nav. Tabs for unconfigured integrations hide themselves automatically; you can also hide any tab manually. Hiding a tab here only hides it — its pages keep working. A tab whose feature is turned off under [Features](#features) reads "Turned off in Features" |
| **Locations** | Add, rename and delete shelves/rooms. Names must be unique and non-blank — a clash is refused with a message rather than saved. Deleting a location unassigns its items |
| **Borrowers** | People you lend to. Deleting a borrower keeps their loan history |
| **Game Platforms** | The platform list used for video games — 30 built in, add your own |
| **Lending** | "Overdue after N days" for loans without a due date (0 disables). Notification URL (ntfy topic or JSON webhook) for the daily overdue digest and the wishlist price-drop digest, with a **Send test** button |
| **Trash** | "Prompt to empty Trash after N days" (`trash_retention_days`, default 180; 0 never prompts). Past the window, admins see a dismissable banner linking to the expired rows; nothing is deleted automatically. See [Trash](user-guide/items.md#trash) |

### Integrations

| Card | Options |
|---|---|
| **Audiobookshelf Sync** | Server URL, optional browser URL, API token, **Test**, per-library include/exclude, sync interval, manual sync |
| **Hardcover** | API token, import your Hardcover library, reading-status sync direction and schedule, export to Hardcover |
| **Collection Valuation** | ISBNdb API key, valuate all / test key. Its **Price alerts** block sets the drop threshold (`price_alert_threshold_pct`, 1–100, default 15) and books checked per night (`price_alert_nightly_cap`, 1–500, default 100), and shows the last run. See [Price alerts](user-guide/wishlist-and-store-mode.md#price-alerts) |
| **Google Books** | Optional API key, **Test Key**. Authenticates the Google Books requests Shelf already makes; keyless access stays enabled without it |
| **Movie Database (TMDb)** | API key for DVD / Blu-ray lookups, for **Find cover** on a DVD, and for the lookup a Photo Intake row typed DVD runs when you confirm it |
| **Photo Intake (Vision)** | Provider: Anthropic (API key + model), OpenAI-compatible (base URL, optional key, model, ingest long-edge), or Ollama (URL, model, ingest long-edge) |
| **IGDB (Video Games)** | Twitch client ID + secret, for game lookups, for **Find cover** on a video game, and for the lookup a Photo Intake row typed Video Game runs when you confirm it |
| **Discogs** | Personal access token for optional exact-pressing lookup on Music item pages. MusicBrainz remains the canonical release identity; Shelf stores only the selected Discogs Release ID and fetches provider details on demand |

Each card has a short inline setup guide for obtaining its credential. Keys
are **write-only** — once saved you see a masked placeholder and a "clear"
checkbox, never the value. See [Integrations](user-guide/integrations.md).

### Data

| Card | Options |
|---|---|
| **Maintenance** | Retry missing covers (book-shaped rows only), **Review covers needing attention** (the manual queue, every media type), backfill synopses, re-run value lookups — the sweeps each with a live progress stream |
| **Import / Export** | CSV export; CSV / Goodreads / StoryGraph / LibraryThing / Libib import with "fetch covers" and "to-read → wishlist" options |
| **Sharing** | Create and revoke public read-only wishlist / collection links |
| **Backup & Restore** | Download a database backup (optionally passphrase-encrypted), restore from one |
| **Portable archive** | Export the whole library as a zip including physical copies and covers; import with a preview step |

### Users

Add users, set roles, reset passwords. See [Users & roles](user-guide/users-and-roles.md).

### Features

Turn optional parts of Shelf on or off: Lending, Series, Statistics, Store
Mode, Sharing, Valuation, Price alerts, Music, Periodicals, Shelf Fill, Photo
Intake, Hardcover, Audiobookshelf sync, Komga and RomM. An upgrade leaves everything
on. A new install starts with the profile chosen in the setup wizard.
Scanning, Browse, items, locations, tags, Trash, settings, users, backups and
logs are core and cannot be turned off.

**Profiles.** A profile is a starting set of features:

| Profile | Features on |
|---|---|
| **Minimal** | None — only the core above |
| **Standard** | Lending, Series, Statistics, Store Mode, Music, Periodicals, Shelf Fill |
| **Everything** | Standard, plus Sharing, Valuation, Price alerts, Photo Intake, Hardcover, Audiobookshelf sync, Komga and RomM |

The **Profiles** row at the top of Features applies one in a single step and
shows which one the install matches. After you turn one feature on or off by
hand, it may match none and reads **Custom**. A profile is not stored: it is
only a way to set the switches below it. Each profile lists what applying it
would turn on and turn off from where the install is now. Applying one asks
first when it would turn off Sharing or Lending while they have something to
lose, as the individual switches do. It does not ask before turning off an
integration you have set up, such as Hardcover or Audiobookshelf sync — check
its **Turns off** line first.

A feature that is off:

- **hides its tab** from the nav,
- **refuses its pages and actions** — a page says the feature is turned off
  (an admin gets a **Turn on** button there),
- **disappears from the pages that stay on** — the item page, Home, Browse,
  Scan and Stats drop its buttons, badges, links and read-outs. Fields you
  typed onto an item stay, such as a series name or a manual value,
- **pauses its background job** — Audiobookshelf sync, Hardcover sync, the
  overdue-loan digest and the nightly price-alert check skip their runs until
  it is back on,
- **keeps all its data.** Turning it back on shows everything again,
  including anything added while it was off.

This is stronger than hiding a tab under **Navigation**, which only takes the
tab out of the nav bar. An integration's own card under **Integrations** stays
usable while its feature is off, so you can set it up and test the connection
before you turn it on; its row here says **Needs setup** until it is
configured. The card says the feature is turned off, links back here, and
keeps only its setup controls: sync, import, export and run buttons go until
the feature is back on.

Two features ask before they turn off, because the change reaches other
people: **Sharing** says how many share links will stop working, and
**Lending** says how many loans are open and that overdue reminders will
pause.

## Account settings

Every user (any role) can change their own display name and password from
the account menu — not from Settings.

## Where things are *not* configurable

- Metadata source order (a national bibliography where one covers the ISBN —
  DNB for German ISBNs, SBN for Italian ones, KB for Dutch ones → Open Library →
  Hardcover → Google Books) is fixed; see [Architecture](architecture.md). National
  routing follows the ISBN's registration group and has no on/off switch, for
  SBN, DNB or KB.
- Outbound API pacing per host is fixed to each provider's published limit.
- Media types are a fixed list: book, audiobook, eBook, magazine,
  DVD / Blu-ray, vinyl, cassette, CD, digital music, comic / graphic novel,
  Manga, video game. A kids' book is a **book carrying a `Kids` tag**, not a
  media type of its own — see [Items → Tags](user-guide/items.md).
  `kids_book` is still accepted on *input* (a CSV, an archive, or a device
  whose cached form still offers it) and is stored as `book`; from a CSV or
  an archive it also adds the `Kids` tag. The scan tab's **Auto** is a choice about how to scan,
  not a stored media type — it is never stored on an item; see
  [Scanning → Media types](user-guide/scanning.md#media-types).
