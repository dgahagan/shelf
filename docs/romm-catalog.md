# RomM synced-library catalogue

Shelf can expose the games already synchronised from RomM as a dedicated read-only catalogue at `/romm/library`.

The view:

- keeps RomM as the authoritative source of the game files;
- groups counts by the stable RomM platform identifier already stored on each synced item;
- supports platform filtering and title/publisher search;
- paginates large libraries at 60 games per page;
- keeps same-titled games on different platforms as distinct catalogue records;
- links each result back to the normal Shelf item page;
- uses the configured Browser / Public RomM URL for **Open in RomM** links when present;
- respects Shelf library permissions, so users only see RomM-backed items from libraries they can view.

The catalogue does not infer that a RomM game and a physical cartridge/disc are the same item. Use Shelf's explicit related-media features for those relationships.
