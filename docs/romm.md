# RomM integration

Shelf can catalogue digital game availability from a RomM server without treating a ROM as the same thing as a physical cartridge or disc.

## Configure

Open **Settings → Integrations → RomM** and provide:

- **RomM URL** — the URL Shelf itself can reach, such as a Docker/LAN address.
- **Browser URL** — optional; use the reverse-proxy/public address if it differs from the server URL.
- **Client API token** — stored encrypted at rest. Leaving the field blank keeps an existing token.

Use **Test Connection** before loading platforms.

## Platforms

Choose **Manage Platforms** after saving a working connection. Every RomM platform can be included or excluded from Shelf.

Known IGDB platform identities are mapped onto Shelf's existing platform slugs. Unknown RomM platforms receive a deterministic Shelf-compatible slug and are registered when an item from that platform is imported.

## Digital and physical games

RomM is treated as a digital holding, not as a physical location.

The stable RomM ROM id is the automatic integration identity. Shelf deliberately does not attach a RomM game to an existing physical game because the title and platform happen to match. This prevents a digital ROM from silently taking over a cartridge/disc catalogue record.

Consequently:

- RomM sync never assigns a room/shelf location or creates a physical copy;
- physical and RomM-backed editions can coexist safely;
- explicit Related Media linking can connect them later when the user knows they belong together.

## Sync and covers

**Sync Now** streams ROMs from every included platform using bounded pages, retry handling and repeated-page protection. Settings shows progress as items are created or refreshed.

If an imported game has no local cover, Shelf may fetch RomM-provided artwork. Cover downloads are restricted to the configured RomM host, so provider metadata cannot turn the sync into an arbitrary external fetch.

## Open in RomM

Items backed by RomM get a compact **Open in RomM** action on the main item card. The action uses the optional Browser URL when configured, keeping Docker-internal addresses out of browser links.
