# Audits

An **audit** checks a shelf — or the whole library — against what Shelf thinks
is there. You start one on the Scan page, scan what you find, and finish it.
Shelf records every scan on the server, so an audit survives a reload and two
people with two phones can scan into the same one. When you finish, the
**report** says what was found, what was moved, what turned up in the wrong
place, what is out on loan, what is missing and what Shelf did not recognise.

Audits are for editors and admins. Running one needs nothing turned on. The
**Audits** pages, where you read reports, are part of **Collector tools**, an
optional feature (on with the Everything profile; Settings → **Features**, see
[Configuration → Features](../configuration.md#features)). They are under the
menu below your username → **Library** → **Audits**.

## Running an audit

1. Open **Scan** and pick **Inventory** mode. The audit strip appears above the
   camera button.
2. Pick the location you are standing at in **Auditing Location**, then press
   **Start audit of the selected location**. Or press **Start whole-library
   audit** — see [Location and whole-library audits](#location-and-whole-library-audits).
3. Scan. Camera, typed entry and a USB or Bluetooth scanner all count. After
   each scan the strip updates: how many scans, how many found, moved here,
   seen elsewhere and unexpected, and — for a location audit — how many
   expected copies are still unaccounted for.
4. Press **Finish audit**. With Collector tools on you land on the report.
   With it off, the strip says how many scans were recorded; turn Collector
   tools on to read the report.

Each scan does what Inventory mode always does — confirm the item, or move it
here — and is also recorded into the audit. Scanning in Inventory mode with no
audit open still confirms and moves items; it just records nothing.

**Reload, switch devices, come back later.** The open audit is remembered on
the device, so a reload resumes it. Another device sees every open audit in the
strip and joins one with **Join**; its scans land in the same audit. **Leave**
stops this device scanning into the audit without finishing it.

**A location audit takes scans only at its location.** If you change *Auditing
Location* while a location audit is open, the next scan is refused with "This
audit is for … — switch the location back, or finish it and start a new one",
and nothing is moved or recorded. Starting, joining or resuming a location
audit selects its location for you.

A scan into an audit that someone else has finished, or deleted, is refused the
same way: nothing is moved, and the card says to start a new one.

## Location and whole-library audits

A **location audit** expects every copy filed at that location, plus any item
that has no copy record but whose location is that one (an older wishlist
record, for example). It counts copies, not records — see
[Auditing a shelf with more than one copy](scanning.md#auditing-a-shelf-with-more-than-one-copy).

A **whole-library audit** expects every copy that has a location, anywhere.
Each scan still uses the selected *Auditing Location* as the place you are
standing, so a scan in the Office confirms the copy filed in the Office and
moves a single-copy item filed elsewhere to the Office. Copies with no location
are expected nowhere. Use it to walk the whole house in one audit, changing
the location as you go from room to room. The audit strip says **Scanning at**
the selected location, so you can see where the next scan will file the book.

## The report

The report has six sections, each with its count. While the audit is open
they read "so far", and the page has its own **Finish audit** button.

| Section | What is in it | What you can do |
|---|---|---|
| **Found** | Expected copies you scanned where they were filed. A copy label confirms that copy; an ISBN confirms every copy of that book on that shelf, shown as one line with a count | — |
| **Moved here** | Items a scan moved to this location | — |
| **Seen here, filed elsewhere** | A book with copies in other rooms, scanned by its ISBN here. Shelf could not tell which copy you were holding, so it moved nothing | Per copy: **It was this one — move it here** moves that copy, clears its missing mark and lists it under Moved here. One scan moves one copy: a second click — from another tab or device — is refused with "That scan was already resolved" |
| **Out on loan** | Expected items you did not scan that are checked out. Not counted missing | — |
| **Missing** | Expected copies nobody scanned and nobody has borrowed. Copies already marked show *missing since* the date | **Mark missing** marks that line's copies; **Mark all missing** marks every copy in the section. A line with *no copy record* has nothing to mark |
| **Unexpected** | Barcodes Shelf does not know (grouped, with how many times each was scanned), and items that are in Trash | **Add** opens Scan in Add mode with the code typed in; *in Trash* links to the Trash page |

**The report reads the collection as it stands now.** It is worked out from the
recorded scans each time you open it, not saved when you finish. If a copy is
moved, deleted or bought after the audit, an old report's *Missing* changes
with it. A scanned record that has since been deleted for good still counts, as
"a record since deleted".

**An audit of a location that was later deleted** keeps its name, marked
"(deleted)". It expects nothing any more, so it lists its scans but no Missing
copies, and takes no new scans.

**Notes** on the report page are free text, up to 2,000 characters.
**Delete audit** (admins only) removes the audit and its scans. Copies it
marked missing stay marked.

The **Audits** page lists audits newest first (the last 100): location,
started, finished or *open*, how long it took and how many scans it recorded.

## The missing mark

**Mark missing** puts a *missing since* date on a copy, which shows on the
item's page beside that copy. It is the date the copy was first marked; marking
it again keeps the earlier date.

A mark clears when there is evidence that exact copy is in your hand:

- an **Inventory** scan of its copy label, or of the ISBN of a book with only
  that one copy — with or without an audit open;
- a **Move** scan of its copy label;
- a [Shelf Fill](shelf-fill.md) placement of its copy label;
- **Found** next to the mark on the item page (editors and admins).

An ISBN scan of a book with more than one copy clears nothing: the ISBN cannot
say which copy you are holding. A mark never clears with time.

Audits and missing marks are kept in the database, so a database backup has
them. The [portable archive](import-and-export.md) does not: a restored copy is
not missing until the next audit says so.
