# Wishlist & Store Mode

## Wishlist

A wishlist item is just an item with **owned = no**. Make one by:

- **Wishlist** scan mode — in a bookstore, scan what you want.
- **Series → Check completeness → Add to wishlist** for missing volumes.
- Untick *Owned* on any item page.
- Importing from Goodreads / StoryGraph with "to-read → wishlist" on.

Browse → **Owned: wishlist** shows it. Buying one later? Scan it in **Add**
mode and Shelf flips the existing record to owned instead of duplicating.

Share it as a gift list with a public link — see [Sharing](sharing.md).

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

Queued unknowns are looked up and **added to your wishlist** (with metadata
and cover) the next time you open the page online. Nothing is lost if you
close the tab; the queue lives in the browser.

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
