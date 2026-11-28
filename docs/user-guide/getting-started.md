# Getting started

This walks you from a fresh install to a cataloged shelf. Install first:
[Installation](../installation.md).

## 1. The setup wizard

The first visit to `https://<server>:18888` redirects to `/setup`. Create the
first account — it is the **admin**. Pick a username, display name and
password. Shelf has no default credentials and no account-recovery email;
if you lose the admin password, another admin resets it under Settings →
Users (or see [Troubleshooting](../troubleshooting.md#locked-out)).

The same page asks how much of Shelf to start with — a feature profile. The
form lists what each choice turns on:

- **Minimal** — scan, catalogue and browse, with nothing optional.
- **Standard** (preselected) — adds Lending, Series, Statistics, Store Mode,
  Music, Periodicals and Shelf Fill.
- **Everything** — adds Sharing, Valuation, Photo Intake and the Hardcover,
  Audiobookshelf, Komga and RomM integrations as well.

This is only a starting point. Settings → **Features** turns any one of them
on or off later, or applies a different profile in one step. See
[Configuration → Features](../configuration.md#features).

## 2. The screens

The nav bar adapts to what you've configured — tabs for integrations you
haven't set up stay hidden (Settings → Library → Navigation controls this) —
and to the features you have on. A tab below whose feature is off does not
appear until you turn it on under Settings → Features.

The desktop bar keeps **Browse**, **Scan**, **Store** and **Discover** as tabs
and gathers the rest into two menus: **Explore** (Series, Music, Periodicals,
Stats) and **Tools** (Photo Intake, Shelf Fill). Click a menu's name to open
it; Escape, a click elsewhere, or tabbing out of it closes it. A menu with only one page available
— Tools without a vision provider, for instance — shows that page as a plain
tab instead, and a menu with none does not appear. On a phone the bar folds
into the ☰ menu, where the same groups appear as headings over their pages.
With everything on:

Shelf opens on **Home** — a read-only overview of your collection. Click
**Shelf** in the menu bar to come back to it from anywhere.

| Tab | What it is |
|---|---|
| **Home** | Totals, what is lent out, missing covers, media-type mix, recent additions. Not a tab — the **Shelf** link in the menu bar. [Home](home.md) |
| **Scan** | The workhorse: camera or hardware scanner, eight modes, title search, and Add by hand for anything no lookup can find. [Scanning](scanning.md) |
| **Browse** | Your catalog — grid or list, filters, search, bulk edit. [Browse & search](browse-and-search.md) |
| **Shelf Fill** (under Tools) | Keep one shelf selected and scan items onto it (editors and admins). [Shelf Fill](shelf-fill.md) |
| **Series** (under Explore) | Library grouped by series with gap detection. [Series](series.md) |
| **Music** (under Explore) | Your releases, and MusicBrainz search to add more. [Music](music.md) |
| **Periodicals** (under Explore) | Magazine publications and their issues. [Periodicals](periodicals.md) |
| **Stats** (under Explore) | Charts: read per year, growth, top authors, value over time. [Stats & valuation](stats-and-valuation.md) |
| **Photo Intake** (under Tools) | Bulk-add from a shelf photo (appears once a vision provider is configured). [Photo Intake](photo-intake.md) |
| **Store** | Offline bookstore mode. [Wishlist & Store Mode](wishlist-and-store-mode.md) |
| **Discover** | Hardcover-powered recommendations (appears with a Hardcover token) |
| **Settings** | Admin only, in the account menu under your name. [Configuration](../configuration.md) |
| **Logs** | Admin only, in the account menu beside Settings. [Users & roles](users-and-roles.md#the-log-viewer) |

Tabs you do not want can be hidden under **Settings → Library**.

Press **?** on any page (or the **?** button, bottom left) for the keyboard
shortcut list; **Escape** closes it.

Shelf follows your browser's language: German, Italian or Simplified Chinese,
else English. **Account → Language** overrides that for your user, and
**English** is always there to pick. Text that is not translated yet shows in
English.

## 3. Add your first book

On a phone: open **Scan**, leave the mode on **Add**, tap the camera button,
point it at the barcode. On a desktop with a USB scanner: click into the
barcode field and scan. Or type the ISBN and send it with Enter or the
**Scan** button beside the field. On a phone, use the button.

Shelf looks the ISBN up (Open Library, then Hardcover and Google Books as
fallbacks; German ISBNs go to the Deutsche Nationalbibliothek first, Italian
ones to the Servizio Bibliotecario Nazionale, Dutch ones to the Koninklijke
Bibliotheek, Hungarian ones to OSZK NEKTÁR, Portuguese ones to PORBASE,
Polish ones to the Biblioteka Narodowa, French ones to the Bibliothèque nationale de France, and Chinese ones from
mainland China, Taiwan and Hong Kong to NeoDB),
downloads a cover, and the item appears in the **Recent scans** strip with a
link to its page. Series and descriptions are fetched too when a source has
them.

Scanning is fast enough to keep pace with pulling books off a shelf; you do
not need to wait for one lookup to finish before scanning the next.

## 4. Tell Shelf where things live

Settings → Library → **Locations**: add your rooms or shelves ("Office",
"Kids' room", "Box 3 in the garage"). Then on Scan, switch to **Move** mode,
pick a location, and scan everything on that shelf. Locations drive
[audits](audits.md) — Scan → Inventory, **Start audit**, scan a shelf, and the
report says what is missing — and the valuation report's grouping.

## 5. Optional next steps

- **Photo Intake** — set a vision provider under Settings → Integrations and
  add whole shelves from one photo. On a Minimal or Standard install, turn
  Photo Intake on under Settings → Features too. [Photo Intake](photo-intake.md)
- **Integrations** — Hardcover for reading sync and series checks, IGDB for
  games, TMDb for discs. All free. Hardcover, Audiobookshelf, Komga and RomM
  are also features: on a Minimal or Standard install, turn them on under
  Settings → Features. [Integrations](integrations.md)
- **Household accounts** — add editors and viewers under Settings → Users.
  [Users & roles](users-and-roles.md)
- **Migrate from Goodreads, StoryGraph, LibraryThing, Libib or Audible** (via audible-cli or Libation) — upload your export under
  Settings → Data → Import / Export. [Import & export](import-and-export.md)
- **Make the certificate warning go away** on your phone.
  [HTTPS & reverse proxy](../https-and-reverse-proxy.md)
