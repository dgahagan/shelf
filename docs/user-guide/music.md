# Music

Shelf can catalogue exact music releases rather than treating a title as one undifferentiated record.

Supported music formats are **Vinyl**, **Cassette**, **CD** and **Digital Music**. CD reuses Shelf's existing CD media type. There is deliberately no catch-all "other" format: when MusicBrainz reports a medium Shelf does not recognise, the add screen asks you to choose the format rather than guessing.

## Add a release

Open **Music** from the navigation bar. Search MusicBrainz by any combination of:

- release title;
- artist;
- UPC/EAN barcode;
- catalogue number.

Shelf searches MusicBrainz **releases**, not only release groups, so different pressings can retain their own country, date, label, catalogue number, packaging and medium format.

Choose the matching result, confirm the Shelf format and optional physical location, then add it.

## Track lists and multi-disc releases

The Music detail page keeps the MusicBrainz medium hierarchy. Multi-disc releases remain separated by medium and vinyl-style track positions such as `A1`, `A2`, `B1` are stored as text instead of being forced into simple integers.

Each track can retain its MusicBrainz recording identity, artist credit and duration.

## Artwork

After adding an exact release, Shelf asks Cover Art Archive for artwork associated with that MusicBrainz release. A front image is preferred. Existing Shelf cover artwork is never overwritten by a refresh.

## Alternate formats

MusicBrainz Release Group identity is stored separately from the exact release identity. If two catalogued releases share a Release Group, Shelf links them using its existing `format` item relationship while preserving each exact release as its own catalogue item.

## Refresh metadata

Editors can refresh an exact release from its Music page. Provider-owned release and track metadata is replaced from MusicBrainz while Shelf's catalogue identity remains the same.

## Physical copies

Release metadata does not store condition, acquisition details or provenance. Those belong to Shelf's physical-copy model, so two physical copies of the same pressing can eventually share one music release record while keeping their own copy-specific state.

## Discogs

Discogs enrichment is intentionally separate from the core Music contribution. MusicBrainz provides the canonical release identity in this feature; Discogs can be added later as optional pressing enrichment without making the core catalogue depend on a Discogs credential.
