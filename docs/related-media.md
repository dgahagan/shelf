# Related Media

Related Media connects catalogue items that belong together without pretending they are the same record.

Shelf reuses its existing `item_links` graph. A related-media group is the full connected component: if A is linked to B and B is linked to C, all three are shown together from any member. No second grouping table is maintained.

## Relationship types

- **Format** — another format or edition of substantially the same work, such as a printed novel and its audiobook.
- **Related** — a deliberately broad relationship for items that belong together but are neither alternate formats nor adaptations.
- **Adaptation** — a work adapted into another medium, such as a novel and a film or game.

Relationships are manual-first. Shelf does not infer cross-media links from similar titles alone.

## Using Related Media

Open an item. The Related Media panel appears below its tags. Editors and administrators can search the existing Shelf catalogue, select a relationship type, and link another item. Viewers see the resulting group read-only.

Direct relationships can be removed from the item page. Removing one edge can split a larger group; other independent links are left intact.

Search excludes every item already in the current connected group, preventing redundant edges from being added just to make a transitive member direct.

Related Media follows Shelf's normal live-catalogue boundary. Soft-deleted items do not appear in panels, searches or relationship-management targets.
