# Settings

Open the menu under your username and choose **Settings**. Settings is available to administrators; account profile and password controls remain under **Account** in the same menu.

The Settings page is organised into five sections, listed in a sidebar on a wide screen and as a row of buttons above the content on a narrow one:

- **Library** — collection display, lending, locations, **tags**, game platforms, navigation preferences, **Trash** retention and the **copy label** prefix.
- **Integrations** — Audiobookshelf, Hardcover, metadata providers, valuation, price alerts and vision services.
- **Data** — CSV and portable-archive import/export, backups and maintenance tools.
- **Users** — accounts, roles and access administration.
- **Features** — turn optional parts of Shelf, such as Lending, Sharing, Hardcover or Collector tools, on or off, one at a time or all at once with a Minimal, Standard or Everything profile. Collector tools gates the copy-label, **Audits** and **Duplicates** pages (Duplicates is admin only); the Scan page's audit strip works without it. See [Configuration → Features](../configuration.md#features).

The selected section is remembered in the browser, so returning to Settings opens the area you were working in. The redesign changes navigation and presentation only: existing settings forms, endpoints and stored values keep their current behaviour.

## Trash retention

Settings → Library → **Trash** sets **Prompt to empty Trash after** — how many
days a deleted item or copy stays in [Trash](items.md#trash) before it counts
as *expired*. The default is **180**; **0** means nothing ever expires and the
prompt never appears.

Nothing is deleted on a timer. When rows pass the window, every admin sees a
banner across the top of each page — *N items in Trash have been there longer
than your retention window* — linking to the expired rows, where **Empty
expired** deletes them permanently. **Dismiss** hides the banner on every
device until more rows expire. Changing the setting takes effect at once.

## Copy labels

Settings → Library → **Copy labels** sets the prefix generated copy codes use:
one to eight letters or digits, stored in capitals, `SHF` by default. Codes read
`PREFIX-000001`. Changing the prefix does not renumber existing labels. The
card shows only while Collector tools is on. See [Copy labels](labels.md).

## Price alerts

Settings → Integrations → **Collection Valuation** → **Price alerts** holds
the two settings for [wishlist price alerts](wishlist-and-store-mode.md#price-alerts),
plus when the last nightly check ran, how many books it checked, and how many
lookups ISBNdb did not answer and why (a refused key, a rate limit):

- **Alert when the price drops by (%)** — the drop threshold, 1–100, default
  **15**.
- **Books checked per night** — how many wishlisted books one nightly check
  looks up, 1–500, default **100**. Each lookup is one ISBNdb request.

The block reminds you that it tracks the publisher's list price, not
used-market prices. If no notification URL is set under Library →
**Lending**, it says so and links there. The feature itself is the **Price
alerts** row in Features; it needs an ISBNdb key before it does anything.

## Tags

Settings → Library → **Tags** lists every tag with the number of items that
carry it (items in Trash are not counted). Admin only. A tag is created by
applying it to an item — on the item page, the edit page, the Browse bulk bar
or through [Default tags](scanning.md#default-tags) — so there is no **Add**
here.

- **Rename** — open **Edit**, change the name, **Save tag**. Every item keeps
  the tag under its new name. Renaming onto a name another tag already has
  (in any case) is refused: merging two tags is not supported yet. Changing
  only the case, `signed` → `Signed`, is fine.
- **Scope** — the same **Edit** form sets a media type, or **All types**. A
  scope only narrows the suggestions a tag appears in (the item pages, the
  Default tags field); it never removes the tag from an item of another
  type, and nothing stops you adding it to one. A scoped tag shows how many
  of its items fall outside the scope — *3 outside Book* — so a mismatch is
  visible rather than silent.
- **Delete** — removes the tag from every item carrying it, including items
  in Trash, after a confirmation. It cannot be undone.

Saved **Default tags** are not updated. Renaming or deleting a tag leaves the
old name in any Default tags field on Scan, Shelf Fill or Photo Intake that
still holds it, on this device and on others — and the next item filed there
creates the old tag again. Clear or retype those fields after a rename.

### LibraryThing English cover fallback

Add your LibraryThing API token in Settings > Integrations, or set
`LIBRARYTHING_TOKEN` in your deployment environment. Environment values take
precedence. Saved tokens are encrypted, masked, and never returned to the browser.
Never commit a token; `.env.example` contains only an empty placeholder.

When existing direct cover downloads and title/author recovery fail, Shelf asks
LibraryThing for related ISBNs and tries verified English editions, regardless of
the original language.
It retains your original ISBN and metadata and labels the cover as coming from
another edition. Title/author recovery keeps its existing language behavior.
English candidates also appear in the manual cover picker.
Use Retry Missing Covers to process older missing covers; existing covers are
never automatically replaced.

The integration checks up to 20 editions within a 20-second budget, returns up
to five picker candidates, and caches mappings for 24 hours (until restart or
credential change). Unknown edition languages are skipped. LibraryThing calls
are limited to one per second. Each installation supplies its own token.
LibraryThing permits non-commercial API use only unless you obtain permission:
https://www.librarything.com/developer/terms
