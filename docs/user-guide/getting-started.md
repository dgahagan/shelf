# Getting started

This walks you from a fresh install to a cataloged shelf. Install first:
[Installation](../installation.md).

## 1. The setup wizard

The first visit to `https://<server>:18888` redirects to `/setup`. Create the
first account — it is the **admin**. Pick a username, display name and
password. Shelf has no default credentials and no account-recovery email;
if you lose the admin password, another admin resets it under Settings →
Users (or see [Troubleshooting](../troubleshooting.md#locked-out)).

## 2. The screens

The nav bar adapts to what you've configured — tabs for integrations you
haven't set up stay hidden (Settings → Library → Navigation controls this).
Out of the box:

| Tab | What it is |
|---|---|
| **Scan** | The workhorse: camera or hardware scanner, eight modes, title search, manual add. [Scanning](scanning.md) |
| **Browse** | Your catalog — grid or list, filters, search, bulk edit. [Browse & search](browse-and-search.md) |
| **Series** | Library grouped by series with gap detection. [Series](series.md) |
| **Stats** | Charts: read per year, growth, top authors, value over time. [Stats & valuation](stats-and-valuation.md) |
| **Photo Intake** | Bulk-add from a shelf photo (appears once a vision provider is configured). [Photo Intake](photo-intake.md) |
| **Store** | Offline bookstore mode. [Wishlist & Store Mode](wishlist-and-store-mode.md) |
| **Discover** | Hardcover-powered recommendations (appears with a Hardcover token) |
| **Settings** | Admin only. [Configuration](../configuration.md) |

## 3. Add your first book

On a phone: open **Scan**, leave the mode on **Add**, tap the camera button,
point it at the barcode. On a desktop with a USB scanner: click into the
barcode field and scan. Or type the ISBN and press Enter.

Shelf looks the ISBN up (Open Library, then Hardcover and Google Books as
fallbacks; German ISBNs go to the Deutsche Nationalbibliothek first),
downloads a cover, and the item appears in the **Recent scans** strip with a
link to its page. Series and descriptions are fetched too when a source has
them.

Scanning is fast enough to keep pace with pulling books off a shelf; you do
not need to wait for one lookup to finish before scanning the next.

## 4. Tell Shelf where things live

Settings → Library → **Locations**: add your rooms or shelves ("Office",
"Kids' room", "Box 3 in the garage"). Then on Scan, switch to **Move** mode,
pick a location, and scan everything on that shelf. Locations drive the
inventory audit and the valuation report's grouping.

## 5. Optional next steps

- **Photo Intake** — set a vision provider under Settings → Integrations and
  add whole shelves from one photo. [Photo Intake](photo-intake.md)
- **Integrations** — Hardcover for reading sync and series checks, IGDB for
  games, TMDb for discs. All free. [Integrations](integrations.md)
- **Household accounts** — add editors and viewers under Settings → Users.
  [Users & roles](users-and-roles.md)
- **Migrate from Goodreads / StoryGraph** — upload your export under
  Settings → Data → Import / Export. [Import & export](import-and-export.md)
- **Make the certificate warning go away** on your phone.
  [HTTPS & reverse proxy](../https-and-reverse-proxy.md)
