# Items

Every book, disc and game is an **item**. The item page (`/item/<id>`) is
its home.

## What's on the page

- **Cover**, with **Find cover** (search by title, or type your own query,
  and pick a candidate — your current cover is shown first, marked
  *Current*, for comparison), **Upload** your own image, or **Remove
  cover**. These work on an item that already has a cover, not just a
  cover-less one; **Retry cover** (re-running the automatic chain) only
  shows up when a cover is missing, since it would have nothing to do
  otherwise.

  **Find cover searches a different source depending on what the item is:**

  | Media type | What it searches | Tiles are labelled |
  |---|---|---|
  | Books, ebooks, audiobooks, kids' books, comics | Google Books and Open Library | by source |
  | DVDs and Blu-rays | the film's poster set on TMDb | by language (`TMDb · EN`) — the same film's posters differ mostly by language |
  | Video games | IGDB cover art **and** key artwork, as separate tiles | by game and kind (`IGDB · Portal · cover`) |

  For **books and comics**, the item's stored author is combined with
  whatever you type, so if the author on the record is wrong no query will
  find the cover — fix the author with **Edit**, or use **Upload**. The
  other media types search on the title (or your typed query) alone; a
  game's stored platform narrows the search when it is set, and a DVD's
  year is used to pick the right film when several share a title.

  **DVD and video game search need credentials.** TMDb needs an API key and
  IGDB needs a Twitch Client ID *and* Client Secret — see
  [Configuration](../configuration.md). Without them the picker says which
  credential is missing rather than reporting "No covers found for this
  title.", which would be untrue: the artwork exists, Shelf just cannot ask
  for it.

  The same is true once the credential is *there* but the provider will not
  answer. A key the provider rejects, a provider that is rate-limiting us, and
  a provider Shelf could not reach each say so by name — so **"No covers found
  for this title." now means only that the provider answered and had nothing.**
- **Metadata** — title, authors, publisher, year, pages, ISBN, language,
  series and position, platform (games), synopsis. **Fetch synopsis** pulls
  a description from Open Library, Google Books or Hardcover if one wasn't
  captured on add.
- **Reading status** — Want to read / Reading / Read, with start and finish
  dates. Viewers can set this too; it's the one thing they can change. It
  appears on books, kids' books, audiobooks, ebooks and comics — discs and
  games don't carry one.
- **Location** and **owned / wishlist** flag.
- **Tags** — add or remove chips inline.
- **Loan state** — who has it and since when, with check-in right there.
- **Value** — ISBNdb list price if valued, or a manual value you enter.
- **Links** — jump to the item in Audiobookshelf or Hardcover when linked.
- **Add a copy** — new item form pre-filled from this one.

## Editing

**Edit** opens the full form: every field above plus notes, a manual value,
and the cover upload. Changing the ISBN does *not* re-fetch metadata
automatically — use **Retry cover** / **Fetch synopsis** afterwards, or
delete and rescan if the record was wrong from the start.

The ISBN is checked when you save. An ISBN whose check digit doesn't add up
is refused with a banner at the top of the form, and **nothing else on the
form is saved** — correct the ISBN or clear the field and save again.
Entering an ISBN-10 stores both forms (the ISBN-13 and the ISBN-10 it
implies); a 979 ISBN has no ISBN-10 and stores none. The same banner
appears for a media type, location, game platform or reading status Shelf
doesn't recognise, and for a non-number in a number field.

An item whose stored ISBN isn't a real ISBN — older Audiobookshelf syncs
stored an ASIN there when a title had no ISBN — has to have it corrected or
cleared before the form will save, since the form submits every field. Clear
it and the item keeps everything else. For the Audiobookshelf case you
usually don't have to: from 0.28.0 the next sync clears an ASIN out of the
ISBN field for you (see [Integrations](integrations.md)).

**Retry cover** appears once the item has an ISBN. **Push to Hardcover**
appears only for book-family items, and only when the item has an ISBN or
an existing Hardcover link, so adding an ISBN in Edit helps only for
book-family rows.

## Covers

The automatic chain tries, in order: Open Library → Hardcover → DNB (German
ISBNs) → Amazon → Google Books → IGDB (games). A miss is retried in the
background, and Settings → Data → Maintenance → **Retry missing covers** sweeps
every cover-less item with an ISBN.

Covers you keep are stored locally in `data/covers/`; nothing hot-links to
the source. While the picker is open, though, the candidate tiles *are*
remote thumbnails fetched live from the source you're searching — only the
one you select gets downloaded and saved locally. Upload accepts JPEG /
PNG / GIF / WebP.

## Reading status vs. Hardcover

With Hardcover connected, status changes sync both ways on the schedule you
set — Shelf is the source of truth for *owning*, Hardcover for *reading*, and
the sync reconciles the reading side.

## Duplicates and merging

Scanning an owned ISBN again opens the existing item rather than creating a
twin. If you end up with duplicates anyway (two different ISBNs for one
book, or a manual add before a scan), keep the better record and delete the
other; tags and loan history live on the record, so move anything you need
first.

Bulk **Merge** copies the fields the kept record lacks from the others
before removing them. A merge that would copy an invalid ISBN is refused and
both records are left in place. The message names the record it stopped on,
by title and id, so a merge of several records tells you which one to fix.

## Deleting

**Delete** on the item page (editor or admin). Loan history referencing the
item is removed with it. A portable archive export is *not* an undo for
deletions — keep a backup if that matters.

## Video games

Games carry a **platform** (from your list under Settings → Library → Game
Platforms), publisher, series and IGDB cover. The same title on two
platforms is two items.

A game reaches IGDB two ways: a UPC scan, or a [Photo Intake](photo-intake.md)
row you typed Video Game, looked up by title when you confirm. The intake path
matches on title alone, so it takes an exact match only and marks the row
**declined** rather than guess — platform is not part of that match, so a
multi-platform title may need the platform set by hand afterwards.

## DVDs / Blu-rays

Looked up through TMDb — title, year, poster — either by UPC scan or from a
[Photo Intake](photo-intake.md) row you typed DVD, matched by title when you
confirm. The intake path takes an exact title match only and marks the row
**declined** rather than file a confidently wrong film. Shelf doesn't
distinguish DVD from Blu-ray — it's one type; use a tag if you care.
