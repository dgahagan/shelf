# Troubleshooting

First stop for anything odd: **Logs** in the nav (admin) or
`docker compose logs -f shelf`.

## Browser says the connection isn't private

Expected on first run — Shelf's certificate is self-signed. Click through,
or fix it properly: [HTTPS & reverse proxy](https-and-reverse-proxy.md).

If you *did* trust the cert and still get the warning, the name you're using
isn't in it. Set `CERT_SAN` to include that IP/hostname, delete
`data/certs/`, restart, re-trust.

## Camera won't start / no camera button

Applies to both the barcode scanner and Photo Intake's **Take photo** button
on desktop (its in-page viewfinder), which both use `getUserMedia`:

- Must be HTTPS. `http://` or an untrusted origin on some browsers disables
  `getUserMedia` — trust the certificate, see
  [HTTPS & reverse proxy](https-and-reverse-proxy.md).
- Permission was denied once — reset it in the browser's site settings for
  your Shelf URL.
- iOS: Safari only (Chrome on iOS is Safari underneath and also works);
  in-app browsers (e.g. from a messaging app) often block the camera. Open
  in Safari proper.
- Another app/tab holds the camera — close it.
- Desktop with no camera attached: Photo Intake's **Take photo** button
  shows "No camera found. Use Choose photo instead." — use **Choose photo**.

Photo Intake's **Take photo** on a phone is different — it opens the native
camera *app* via an HTML capture input, not `getUserMedia`, so it works even
over plain `http://` and isn't affected by the in-app-browser restriction
above.

## USB scanner types nothing

Click into the barcode field first. If the scanner types characters but no
Enter, configure it to send a carriage-return suffix (every scanner manual
has a barcode for this).

## Barcode scans but "not found"

- Many pre-2007 books carry only an ISBN-10 *printed* and an EAN that isn't
  the ISBN; type the ISBN-10.
- Store-price-sticker barcodes aren't ISBNs. Peel.
- Genuinely obscure editions: use **Title search** or **Add manually**.

## Metadata came back wrong or sparse

Sources disagree. **Edit** the record; **Find cover** for another image;
**Fetch synopsis** if the description is missing. For German books, make
sure you're on 0.11+ (DNB source).

**DVDs and games that filed a bare title — no synopsis, no year, no cover —
were a bug, not a missing key.** TMDb rejected the credential type the setup
docs told you to paste, and retail barcode titles were sent to the provider
verbatim. Both are fixed; the affected items are not rewritten in place, so
delete them and re-scan. Check Settings → Integrations → TMDb → **Test key**
first: it now fails for a key that cannot work, where it used to pass.

Setting a preferred language
(Settings → Library → Collection) ranks matching editions first in title
search.

## Covers missing after an import

Imports fetch covers in the background; give it a few minutes on a big
batch. Then Settings → Data → Maintenance → **Retry missing covers**. Items
with no ISBN (manual adds, discs, games without IGDB) need a manual cover
or **Find cover**.

## Photo Intake finds nothing / garbage

- Is a provider configured and does its **Test** pass?
- Local model too small for the job: try a cloud model once to compare.
- Accept the **tiling** offer for high-resolution photos.
- An error starting "Anthropic rejected the request" or "OpenAI API
  rejected the request" quotes the provider's own reason (trimmed to a
  sentence or so) — the usual fixes are the high-res offer or a smaller
  photo; an error ending in "try again" is a transient one worth retrying.
- **Logs** shows an `Intake analyze:` line for every send, naming each
  uploaded part's filename, type and size — a quick way to confirm a photo
  really was resized before it went out (a resized as-is send appears as
  `photo.jpg`, an unmodified one keeps the original filename, a tiled send
  lists one `tile-N.jpg` per tile).
- Glare, angle, distance — see [Photo Intake](user-guide/photo-intake.md#getting-good-results).

## Store Mode isn't offline

Service workers need a trusted origin. Trust the certificate on the phone or
use a real one; `localhost` always works. After fixing trust, open the store
page once online so it can install.

## Container starts then exits / restarts

Read the log. Common causes:

- **Invalid `CERT_SAN`** — must be comma-separated `DNS:name` / `IP:addr`
  entries only.
- **Permission denied on `/data`** — the container runs as UID 1000; on
  SELinux hosts add `:z` to the volume; elsewhere `chown -R 1000:1000 data`.
- **Port in use** — change the host side of the mapping.

A crash-loop right after an *upgrade* on an old database was a known bug
fixed in 0.8.1 — upgrade to that or later and it heals itself.

## Locked out

Another admin: Settings → Users → reset password. Only admin? Stop the
container and run, from the host:

```bash
sqlite3 data/shelf.db ".schema users"
sqlite3 data/shelf.db "SELECT id, username, role FROM users;"
```

Passwords are bcrypt hashes. Generate one —
`python3 -c "import bcrypt; print(bcrypt.hashpw(b'newpass', bcrypt.gensalt()).decode())"`
— and `UPDATE` your user's hash column with it. Restart. (Take a copy of
`shelf.db` first.)

## Overdue reminders never arrive

- **Send test** on the Lending card — if that fails, the URL or format is
  wrong (ntfy needs the full topic URL, e.g. `https://ntfy.sh/my-topic`).
- The digest goes out once a day at most and only when something is
  overdue; check "Overdue after" isn't 0.

## Hardcover / Audiobookshelf sync does nothing

- **Test** the connection on its card.
- ABS: make sure at least one library is selected.
- Both run on an interval read every 5 minutes; "Sync now" is immediate.
- Env-var overrides (`HARDCOVER_TOKEN`, `ABS_TOKEN`) beat what's stored —
  if you changed the key in Settings and nothing changed, check your `.env`.

## Rate-limited (HTTP 429) in the UI

Per-IP limits protect `/api/`, `/share/`, `/login` and `/setup`. Behind a
reverse proxy without `SHELF_TRUST_PROXY=1`, every client looks like the
proxy's IP and shares one bucket — set the variable.

## Still stuck

[Open an issue](https://github.com/dgahagan/shelf/issues/new/choose) with
your version, browser/device, and the relevant log lines.
