# Periodicals

Shelf models a periodical as two related things:

- the **publication** — the continuing magazine or serial title;
- the **issue** — one concrete issue in your collection.

This lets *Private Eye*, for example, have a publication page containing many individual issue items without pretending the whole run is one Shelf item.

## Scan a magazine

On **Scan**, choose **Magazine** and scan a valid 977 serial barcode.

Shelf decodes the ISSN from the 977 carrier and asks the ISSN metadata source for the publication title and publisher. A 2- or 5-digit barcode add-on is preserved separately.

The add-on is **not automatically treated as an issue number**. Publishers use serial variants and supplements differently, so Shelf asks you to confirm whichever issue information is actually printed on the copy:

- volume;
- issue number;
- issue date;
- cover-date label, such as `September 2026` or `Summer Special`.

If the ISSN metadata source is unavailable, the barcode identity is still retained and you can enter the publication title manually instead of receiving a generic `Magazine` result.

## Barcode identity

The 13-digit 977 carrier can be shared by many issues. Shelf therefore does not store it in the ordinary globally unique UPC field.

Instead, each periodical issue keeps its own:

- 977 carrier;
- optional 2/5-digit supplement;
- confirmed issue metadata.

A full carrier+supplement is the strongest duplicate identity. Without that, Shelf can fall back to publication + volume + issue number or publication + issue date during confirmation.

When scanning an issue already in Shelf, a full carrier+supplement resolves directly. A carrier-only scan identifies the publication but **never claims to identify one concrete stored issue**, even if there happens to be only one today. That keeps a camera scan which omitted the add-on from turning the next issue into a false duplicate.

## Browse a publication

Open **Periodicals** from the navigation bar to see continuing publications. Each publication page lists its concrete issues, newest first where dates are known, while retaining issue numbers and barcode supplements separately.

## Physical copies

A periodical issue is a catalogue item. Physical ownership, duplicate copies, condition, provenance and shelf location can use Shelf's physical-copy model independently, so the periodical catalogue does not need to duplicate those concepts.
