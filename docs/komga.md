# Komga integration

Shelf can catalogue digital comics and manga that are available in a Komga server while keeping that service availability separate from physical ownership and location.

## Configure

Open **Settings → Integrations → Komga** and provide:

- **Komga URL** — the URL Shelf itself can reach, such as a Docker/LAN address.
- **Browser URL** — optional; use the reverse-proxy/public address if it differs from the server URL.
- **API key** — stored encrypted at rest. Leaving the field blank keeps an existing key.

Use **Test Connection** before loading libraries.

## Libraries

Choose **Manage Libraries** after saving a working connection. Every Komga library can be:

- included or excluded from Shelf; and
- classified explicitly as **Comics** or **Manga**.

Shelf suggests Manga only when the library name clearly contains “manga”; the saved user choice always wins.

## Identity and physical copies

Komga is treated as a digital holding, not as a physical location.

Shelf uses the stable Komga book ID as the integration identity. On first import it may attach the Komga holding to an existing item only when there is an exact canonical ISBN match of the same content type. It never adopts an existing item from title/series similarity alone.

Consequently:

- a physical comic/manga and a Komga copy can share one catalogue item when a strong identifier proves the edition;
- ambiguous editions remain separate instead of being silently merged;
- Komga sync never assigns a room/shelf location or creates a physical copy.

## Sync and covers

**Sync Now** reads every included library with bounded pagination, persists new/changed holdings, and reports progress in Settings.

If an imported item has no cover, Shelf requests the book thumbnail from the configured Komga server and stores it using Shelf’s normal local cover handling.

## Open in Komga

Items with a Komga holding get a compact **Open in Komga** action on the main item card. The action uses the optional Browser URL when configured, so Docker-internal addresses do not leak into browser links.
