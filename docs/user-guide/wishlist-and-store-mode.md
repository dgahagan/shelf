# Wishlist & Store Mode

## Wishlist

The wishlist is a list of items you want. Owning and wishing are separate
things, so an item is in one of three states: **owned**, **on your
wishlist**, or **neither** — tracked in your catalogue (say, a book you read
from the library) without being owned or wanted. An owned item is never on
the wishlist.

Put an item on the wishlist by:

- **Wishlist** scan mode — in a bookstore, scan what you want.
- **Series → Check completeness → Add to wishlist** for missing volumes. A
  volume you already track but neither own nor want counts as missing here,
  and the button adds that item to the wishlist rather than a second copy of
  it.
- Ticking **On my wishlist** on an item's edit page.
- **Browse → select items → Wishlist: Add to wishlist**.
- Importing from Goodreads / StoryGraph with "Import 'to read' books as
  wishlist" on, importing a LibraryThing export (its Wishlist collection), or
  re-importing Shelf's own CSV export.

Take one off with the same controls: untick **On my wishlist**, or **Browse →
select → Wishlist: Remove from wishlist**. Removing an item from the wishlist
does not delete it or its reading history.

Browse → **Owned: Wishlist** shows the list, and **Owned: Not owned or
wishlisted** shows the neither items. Buying one later? Scan it in **Add**
mode: Shelf marks the existing record owned and takes it off the wishlist
(the scan card says *Now owned — was on your wishlist*) instead of
duplicating it. Scanning it in **Wishlist** mode changes nothing.

Share it as a gift list with a public link — see [Sharing](sharing.md). A
wishlisted book can also carry [buy links](#buy-links), and its list price
can be watched for [price alerts](#price-alerts).

## Price alerts

Shelf can watch the wishlist for price cuts. It checks the **publisher's list
price** through [ISBNdb](https://isbndb.com) — the same source as
[Valuation](stats-and-valuation.md#valuation-isbndb). That makes it good for
spotting reprints and publisher price cuts, and no use for used-market or
second-hand deals, which ISBNdb does not track.

It needs two things: an ISBNdb API key (Settings → Integrations →
**Collection Valuation**) and the **Price alerts** feature, which is on in
the Everything profile (Settings → **Features**). Until a key is saved, the
Features row says it needs setup.

Once a day Shelf looks up wishlisted books that have an ISBN, starting with
the ones it has never checked and then the ones it checked longest ago. It
checks at most **Books checked per night** of them (default 100), so a
larger wishlist is covered over several nights, and a book you have just
wishlisted is checked first. Lookups are paced to ISBNdb's rate limit.
Games, discs and music are not watched — there is no list-price source for
them.

When a book's price has fallen by at least the **drop threshold** (default
15%) since its last recorded price, it goes into a digest — one message per
night for all the drops, like `Dune — $12.99 → $8.49 (−35%)`. The digest
goes to the notification URL under Settings → Library → **Lending**, in the
same ntfy or webhook format as [loan reminders](lending.md#reminders). With
no URL set, prices are still recorded; you just get no message.

On a wishlisted book's page, a line under the title shows the latest price
and, if it has changed, the previous one: *List price $8.49 · was $12.99 on
2026-09-02*. If a later check found no price for the book, the latest price
is dated so it does not read as current: *List price $8.49 as of 2026-09-20*.

Both settings and the last check are in the **Price alerts** block on the
Collection Valuation card. The last check reads like *2026-10-01 02:40 UTC —
100 checked*; if ISBNdb did not answer some lookups it adds how many and why,
such as *1 checked, 1 failed (ISBNdb refused the API key)* — a refused key
stops that night's check at the first book. A book whose lookup failed is
checked again first next time — see
[Settings → Price alerts](settings.md#price-alerts). Price history is kept in
the database and is **not** included in a portable archive export — see
[Upgrading and backups](../upgrading-and-backups.md).

## Buy links

Shelf can show **Bookshop.org** and **Amazon** links that carry *your own*
affiliate tags. Shelf earns nothing from them: the links pay you, through your
own affiliate accounts.

They appear in two places, and nowhere else:

- a **wishlisted book's page**, under the title;
- the **missing volumes** of a series, after **Check completeness** on the
  [Series](series.md#with-hardcover-connected) page.

Owned items, Browse, share links and Store Mode never show them. Wishlisted
items that are not books — games, discs, music — get none.

Bookshop.org comes first. It supports independent bookstores. A book with an
ISBN gets a link straight to its product page. A book without one gets a store
search on its title instead. A Bookshop.org search carries no affiliate id, so
it **pays nobody**. Missing series volumes have no ISBN in Shelf, so their
links are always searches.

Each set of links has a short line under it saying they are affiliate links.

To turn them on, enter a tag in Settings → Library → **Buy links** (see
[Settings → Buy links](settings.md#buy-links)). Nothing shows until you enter
one, and a store whose tag is blank shows no link. The **Buy links** row in
Settings → Features turns the feature off entirely.

## Store Mode (offline)

Standing in a shop with no signal and a stack of second-hand books, you want
one answer per barcode: *do I already have this?* Store Mode gives it
instantly, offline.

### How it works

Open **Store** in the nav (or `/store`) while online. Shelf caches your
library's ISBNs — owned and wishlist — on the device. From then on each scan
answers from that cache:

| Verdict | Meaning |
|---|---|
| **Owned** | Already on your shelf (shows where) |
| **On wishlist** | You wanted it — buy it |
| **Not in library** | New to you. It's queued on the device |

The cache holds what you own and what you want. An item you track but
neither own nor want reads **Not in library** too, and scanning it queues it
like any unknown: on the next sync Shelf adds that existing item to your
wishlist instead of creating a new one.

Queued unknowns are looked up and **added to your wishlist** (with metadata
and cover) the next time you open the page online. Nothing is lost if you
close the tab; the queue lives in the browser.

A barcode the scanner misreads is kept too. If a queued code fails its check
digit it can't be looked up, so instead of dropping it Shelf saves a wishlist
row titled **Unreadable barcode — <code>** with no ISBN, and the sync line
says how many couldn't be read. Open that row later and type the right ISBN
into **Edit**. Scanning the same bad barcode again matches the row it already
made rather than queueing a second one.

### Installing it as an app

On the store page use the browser's **Add to Home Screen**. It then launches
full-screen like a native app and works with no connection at all.

### The one requirement

Offline support uses a service worker, which browsers only run on an origin
they trust. `localhost` always qualifies; your server over LAN HTTPS does not
until you either trust Shelf's self-signed certificate on the phone or put a
real certificate in front of it. Both are covered in
[HTTPS & reverse proxy](../https-and-reverse-proxy.md). Until then Store
Mode still works — just not with the signal off.

### Keeping the cache fresh

The cache refreshes each time you open Store Mode online. Scanned a box of
books at home this morning? Open the store page once before you leave.
