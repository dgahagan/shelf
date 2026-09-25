# Users & roles

Shelf is multi-user from the first install: one shared library, several
logins, three roles.

| Role | Can |
|---|---|
| **Admin** | Everything — settings, users, integrations, locations, borrowers, sync, bulk ops, backup/restore, logs, **delete permanently and empty expired Trash**, the Trash retention setting, **rename, scope and delete tags**, plus all editor rights |
| **Editor** | Add / edit items, delete them to Trash and restore them from Trash, all scan modes, covers, lend and return, apply tags (one item or a Browse selection), import / export CSV and archives |
| **Viewer** | Browse, search, Series, Stats, Store Mode, set reading status on items, export CSV (without the items in Trash) |

Cover controls — **Find cover**, **Upload**, **Remove cover**, **Retry
cover** — are editor+; a viewer doesn't see them on the item page at all.
The same goes for **Lend** / **Check in** and **Push to Hardcover** on the item
page, the Series page's rename, merge and disband actions, and Discover's
add-to-wishlist buttons: viewers get the read-only view of each, with the
loan context and the Series list still shown.

**Trash** (account menu → Library) is for editors and admins. Both can
restore; only an admin sees **Delete permanently**, **Empty expired** and the
banner that says when Trash holds rows past the retention window. Viewers
cannot delete, so they have no Trash.

The first account created in the setup wizard is an admin. There must always
be at least one admin.

## Managing users

Settings → **Users** (admin): add a user with username, display name, role
and initial password; change a role; reset a password; delete a user.
Deleting a user doesn't touch the catalog — items are shared, not owned.

Every user changes their **own** display name and password from the account
menu in the nav. The menu's trigger shows your name and your role, and the
panel opens with both, so it is always clear which account you are signed in
as. Its entries fall into three groups — **Account** (profile and password),
**Administration** (Settings and Logs, shown to admins only) and **Sign out**.

## Sessions

Login sets an HTTP-only, secure cookie with a 7-day JWT that refreshes while
you're active, so a device you use regularly stays logged in. **Log out**
from the account menu; to force every device out, change your password.
Restoring a database backup also signs every device out.
Login attempts are rate-limited per IP.

## The log viewer

**Logs** — in the account menu under your name, beside Settings (admin) —
tails Shelf's application log in the browser:
auth events (logins, failures, role changes), sync runs, metadata and cover
lookups, errors. Handy for "why didn't that cover load" without touching
`docker compose logs`.

## What's per-user today

Only your login, display name and password. Reading status, tags, wishlist
and everything else are shared across the household. Per-user reading
tracking and per-household libraries are on the roadmap (see
[FUTURE_FEATURES in the repo issues](https://github.com/dgahagan/shelf/issues)).
