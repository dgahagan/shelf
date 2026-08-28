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
| `SHELF_TRUST_PROXY` | *(unset)* | Set to `1` **only** when a reverse proxy in front of Shelf overwrites `X-Forwarded-For` / `CF-Connecting-IP`. Without a proxy this lets clients spoof their IP past login rate limiting |
| `SECRET_KEY` | *(auto)* | JWT signing key. Auto-generated and stored in the database if unset. Set it explicitly if you run several instances against one database |
| `SHELF_ENCRYPTION_KEY` | *(auto)* | Key for API credentials stored in the database. If unset, generated at `data/encryption.key`. Set it (`openssl rand -hex 32`) so the data directory alone can't decrypt credentials |
| `DATA_DIR` | `/data` | Where the database, covers and certs live. Only relevant outside Docker |
| `SHELF_DISABLE_RATE_LIMIT` | *(unset)* | Turns off per-IP rate limiting. For tests and local development only |

### Credential overrides

Integration credentials are normally entered in Settings and stored encrypted.
Each can instead be supplied as an environment variable, which **takes
priority** over anything stored:

| Variable | Setting |
|---|---|
| `HARDCOVER_TOKEN` | Hardcover API token |
| `GOOGLE_BOOKS_API_KEY` | Optional Google Books API key; anonymous lookups remain available when unset |
| `ABS_URL`, `ABS_TOKEN` | Audiobookshelf server URL and API token |
| `ISBNDB_API_KEY` | ISBNdb key (valuation) |
| `TMDB_API_KEY` | TMDb credential (DVD / Blu-ray metadata **and** DVD cover search) — either the 32-character v3 API Key or the v4 Read Access Token |
| `IGDB_CLIENT_ID`, `IGDB_CLIENT_SECRET` | Twitch developer credentials (video game metadata **and** game cover search — both fields are required) |

Useful with Docker secrets or a secrets manager. Vision-provider keys (Photo
Intake) are Settings-only.

A credential supplied this way behaves like a saved one everywhere it matters,
with two differences worth knowing:

- **The Settings field stays blank.** Shelf never echoes a *secret* back into the
  page, whether it came from the database or the environment. Blank does not mean
  unset — leave it blank and the environment value keeps working. (`ABS_URL` is
  the exception: a server URL is not a secret, so the Audiobookshelf URL field
  shows the value in use.)
- **Shelf cannot remove it.** The "Remove saved key" checkbox only deletes the
  stored row, and the environment variable still takes priority afterwards. To
  change or remove an env-supplied credential, change it in your environment and
  restart the container. The checkbox is hidden when the credential comes *only*
  from the environment; if you also saved one through the form earlier you will
  still see it, and clearing that row changes nothing while the variable is set.

**Test key** works against an env-supplied credential — you do not need to paste
a second copy into the form to check it.

## The Settings page

**Settings** (gear icon, admin only) has four tabs. This is where each option
lives:

### Library

| Card | Options |
|---|---|
| **Collection** | Display currency (20 choices; formatting only, never conversion). Preferred language for title searches |
| **Navigation** | Which tabs appear in the nav. Tabs for unconfigured integrations hide themselves automatically; you can also hide any tab manually |
| **Locations** | Add, rename and delete shelves/rooms. Deleting a location unassigns its items |
| **Borrowers** | People you lend to. Deleting a borrower keeps their loan history |
| **Game Platforms** | The platform list used for video games — 30 built in, add your own |
| **Lending** | "Overdue after N days" for loans without a due date (0 disables). Notification URL (ntfy topic or JSON webhook) for the daily overdue digest, with a **Send test** button |

### Integrations

| Card | Options |
|---|---|
| **Audiobookshelf Sync** | Server URL + API token, **Test**, per-library include/exclude, sync interval, manual sync |
| **Hardcover** | API token, import your Hardcover library, reading-status sync direction and schedule, export to Hardcover |
| **Collection Valuation** | ISBNdb API key, valuate all / test key |
| **Movie Database (TMDb)** | API key for DVD / Blu-ray lookups, and for **Find cover** on a DVD |
| **Photo Intake (Vision)** | Provider: Anthropic (API key + model), OpenAI-compatible (base URL, optional key, model, ingest long-edge), or Ollama (URL, model, ingest long-edge) |
| **IGDB (Video Games)** | Twitch client ID + secret, for game lookups and for **Find cover** on a video game |

Each card has a short inline setup guide for obtaining its credential. Keys
are **write-only** — once saved you see a masked placeholder and a "clear"
checkbox, never the value. See [Integrations](user-guide/integrations.md).

### Data

| Card | Options |
|---|---|
| **Maintenance** | Retry missing covers, backfill synopses, re-run value lookups — each with a live progress stream |
| **Import / Export** | CSV export; CSV / Goodreads / StoryGraph import with "fetch covers" and "to-read → wishlist" options |
| **Sharing** | Create and revoke public read-only wishlist / collection links |
| **Backup & Restore** | Download a database backup (optionally passphrase-encrypted), restore from one |
| **Portable archive** | Export the whole library as a zip including covers; import with a preview step |

### Users

Add users, set roles, reset passwords. See [Users & roles](user-guide/users-and-roles.md).

## Account settings

Every user (any role) can change their own display name and password from
the account menu — not from Settings.

## Where things are *not* configurable

- Metadata source order (DNB for German ISBNs → Open Library → Hardcover →
  Google Books) is fixed; see [Architecture](architecture.md).
- Outbound API pacing per host is fixed to each provider's published limit.
- Media types are a fixed list: book, kids book, audiobook, eBook, DVD /
  Blu-ray, CD, comic / graphic novel, video game. The scan tab's **Auto** is a
  choice about how to scan, not a ninth type — it is never stored on an item;
  see [Scanning → Media types](user-guide/scanning.md#media-types).
