# Copy labels

A barcode on the book tells Shelf *which title* it is. It cannot say *which
copy* — two copies of one novel carry the same ISBN. A **copy label** can. Shelf
gives each copy its own code, prints the codes on sticky label sheets, and
every Scan mode then acts on the exact copy you scanned.

Copy labels are part of **Collector tools**, an optional feature. It is on with
the Everything profile; turn it on or off in Settings → **Features** (see
[Configuration → Features](../configuration.md#features)). The Labels page is
for editors and admins, under the menu below your username → **Library** →
**Labels**.

## What a code looks like

Generated codes read **`SHF-000001`**, **`SHF-000002`**, and so on: a prefix,
a hyphen and a six-digit number. The next code is always one past the highest
number any copy has ever held under the prefix — including copies that are in
Trash — so a code is never handed out twice.

The prefix is a setting: Settings → Library → **Copy labels** (admins). It is
one to eight letters or digits, stored in capitals. Changing it does not
renumber existing labels; the next code under a new prefix starts at
`-000001`.

A barcode you typed by hand in a copy's edit panel stays exactly as you typed
it. Shelf never changes it, and it does not count towards the next number.
A typed code longer than about 24 characters is too wide to scan at label
size, so its label prints the code as text with the note "Code too long to
print as a barcode" instead of bars.

## Assigning codes

On the Labels page, **Assign codes** labels every copy in a scope that has no
barcode yet:

- **Every copy** — every copy in the collection without a barcode.
- **Copies of items with no ISBN or UPC** — the things a retail barcode cannot
  find: zines, ex-library books, hand-bound volumes.
- **Copies at one location** — that location only, not the places inside it.

The line under the scope says how many copies would get a code before you
commit. The result names the range assigned — *12 codes assigned, SHF-000001 to
SHF-000012* — with a link that prints exactly those labels. A copy that already
has a barcode, generated or typed, is never changed.

For one copy, open the item page, **Edit** the copy and press **Assign code**
beside its barcode field. Each copy's code is shown in the item's copies list.

## Printing labels

The **Print labels** half of the Labels page prints copies that *have* a code,
chosen by the same scopes. From an item page, **Print labels for this item**
prints that item's copies. Each label carries the barcode, the code in text,
and the title (cut at 40 characters).

Two label stocks are supported, with their published dimensions:

| Stock | Paper | Labels per sheet | Label size |
|---|---|---|---|
| Avery 5160 | US Letter | 30 (3 × 10) | 2⅝″ × 1″ |
| Avery L7160 | A4 | 21 (3 × 7) | 63.5 × 38.1 mm |

**Start at label** skips the first positions on the first sheet, so a
part-used sheet is not wasted. Shelf remembers the stock and start position in
this browser.

**One page holds at most 300 labels** (ten Letter sheets). A larger selection
shows a **Next** link above the sheets: print the page, then follow Next for
the rest. Later pages begin at the first position of a fresh sheet.

**Printer settings matter.** Print at **100% scale** with fit-to-page off, and
no headers or footers. Any scaling moves the labels off the die-cuts. Print a
test page on plain paper and hold it against a label sheet before using the
real stock.

Whether a printed label decodes reliably on a given phone is confirmed on real
stock during each release's test drive; if a stock reads poorly, that is a bug
worth reporting.

## Scanning a label

See [Scanning → Scanning a copy label](scanning.md#scanning-a-copy-label). In
short: Inventory, Move and Lookup act on the copy itself; Lend, Return and
Quick Rate act on its item; Add reports the item you already own.
