# Homelab integration

Shelf can feed a dashboard. You make a **read-only API token**, and a tool such
as [Homepage](https://gethomepage.dev), Homarr or
[Home Assistant](https://www.home-assistant.io) reads one JSON document,
`GET /api/v1/stats`, with it.

## API tokens

A token is a credential for scripts and dashboards. It has these limits:

- **It reads only.** A token is accepted on `GET` (and `HEAD`) requests under
  `/api/v1/` and nowhere else. Any other method answers `401`. On any other
  path the token is ignored, as if no credential was sent.
- **It never acts above viewer.** A token acts as the user who made it, but
  always with the **viewer** role, whatever that user's own role is. An
  admin's token cannot download a backup or change a setting.
- **It is shown once.** Shelf stores only a hash of the token. Copy it when
  you create it, because no screen can show it again.
- **It lasts until it is revoked.** A token has no expiry date. A password
  change or a role change does not end it. Deleting the user deletes their
  tokens.

### Turn the feature on

API tokens are an optional feature. A new install with the **Everything**
profile has it on. **An upgraded install starts with it off.** To turn it on,
go to Settings → **Features** → **API tokens**. When the feature is off,
`/api/v1/` answers `403` with `{"error": "feature_disabled", …}` to a script or
dashboard, and the token screens are hidden. (A browser that opens the address
directly gets the normal "turned off" page instead.) Tokens you made before
are kept, and they work again when the feature is turned back on.

### Make a token

1. We recommend a **dedicated viewer user** for dashboards, for example
   `dashboard`. An admin adds it under Settings → **Users**. Then sign in as
   that user.
2. Open the account menu (your name, top right) → **Profile and password** →
   **API tokens**.
3. Type a name that says where the token goes, for example `homepage`, and
   click **Create**.
4. Click **Copy**, and put the token in your dashboard's secret store now.
   If the copy fails (some browsers block the clipboard on a self-signed
   certificate), the token is selected in its box so you can copy it by hand.

The list shows each token's name, the start of the token, when it was
created and when it was last used. "Last used" updates at most once a
minute. **Revoke** deletes a token at once, and the next request with it
answers `401`. Each user can have up to 25 tokens.

An admin sees every user's tokens under Settings → **Users** → **API
tokens**, and can revoke any of them. Use this to stop a leaked token when
its owner is not available.

## `GET /api/v1/stats`

Send the token in the `Authorization` header:

```bash
curl -k -H "Authorization: Bearer shelf_…" https://shelf.lan:18888/api/v1/stats
```

`-k` accepts Shelf's self-signed certificate. A signed-in browser can also
open `/api/v1/stats` directly to see what a dashboard will see.

The response:

```json
{
  "items": {
    "total": 1240,
    "owned": 1175,
    "wishlist": 65,
    "by_media_type": [
      {"media_type": "book", "total": 1010, "owned": 960, "wishlist": 50},
      {"media_type": "dvd", "total": 230, "owned": 215, "wishlist": 15}
    ]
  },
  "loans": {"open": 3, "overdue": 1},
  "value": {"total": 18450.5, "currency": "USD"},
  "upcoming": {"count": 4, "next_release_date": "2026-11-18"},
  "generated_at": "2026-10-27T14:03:11+00:00"
}
```

| field | meaning |
|---|---|
| `items.total`, `.owned`, `.wishlist` | Items in the catalog (Trash excluded), items you own, items on the wishlist |
| `items.by_media_type[]` | The same three counts per media type. `media_type` is the stored value (`book`, `dvd`, `video_game`, `cd`, …) |
| `loans.open`, `loans.overdue` | Items lent out now; loans past their due date (or past the overdue window, for a loan with no due date) |
| `value.total`, `value.currency` | Collection value of what you own, the figure on the Stats page; the display currency's code |
| `upcoming.count`, `upcoming.next_release_date` | Announced volumes in watched series; the first release date today or later, or `null` if none is dated |
| `generated_at` | When the document was built, UTC, ISO 8601 |

**A section is `null` when its feature is off, and is never missing.**
`loans` is `null` when Lending is off. `value` is `null` when Valuation is
off. `upcoming` is `null` when Release radar or Hardcover is off. A template
that reads `loans.overdue` must allow for `null`. The recipes below do.

The field names are the contract. A later release can add fields, but it
does not rename or remove the ones above.

**Rate limit.** Each token has its own limit of 60 requests a minute. A
dashboard that polls too fast slows only itself, not the browser UI behind
the same proxy. Failed attempts with a wrong token count against the
sender's IP address. A poll every 30 seconds or slower is plenty.

## Homepage

Homepage's `customapi` widget reads the document. Put the token in
Homepage's environment, for example in its `docker-compose.yml`:

```yaml
environment:
  HOMEPAGE_VAR_SHELF_TOKEN: shelf_…
```

Then add Shelf to `services.yaml`:

```yaml
- Library:
    - Shelf:
        href: https://shelf.lan:18888
        description: Home library
        icon: mdi-bookshelf
        widget:
          type: customapi
          url: https://shelf.lan:18888/api/v1/stats
          refreshInterval: 60000
          headers:
            Authorization: Bearer {{HOMEPAGE_VAR_SHELF_TOKEN}}
          mappings:
            - field: items.total
              label: Items
              format: number
            - field: loans.overdue
              label: Overdue
              format: number
            - field: items.wishlist
              label: Wishlist
              format: number
```

With Lending off, `loans` is `null`, so the Overdue field has no value.
Remove that mapping if you do not use Lending.

**Homarr.** Homarr's generic API/iframe widgets can read the same URL. Set
the `Authorization: Bearer …` header the same way, and point the fields at
`items.total`, `loans.overdue` and `items.wishlist`.

## Home Assistant

Home Assistant's [RESTful](https://www.home-assistant.io/integrations/rest/)
integration reads the document once and makes one sensor per field. In
`secrets.yaml`, store the whole header value, including the word `Bearer`:

```yaml
shelf_token: "Bearer shelf_…"
```

Then in `configuration.yaml`:

```yaml
rest:
  - resource: https://shelf.lan:18888/api/v1/stats
    verify_ssl: false
    scan_interval: 300
    headers:
      Authorization: !secret shelf_token
    sensor:
      - name: "Shelf items"
        unique_id: shelf_items_total
        icon: mdi:bookshelf
        state_class: measurement
        availability: "{{ value_json is mapping and 'items' in value_json }}"
        value_template: "{{ value_json['items']['total'] }}"
      - name: "Shelf open loans"
        unique_id: shelf_loans_open
        icon: mdi:book-arrow-right
        availability: "{{ value_json is mapping and value_json.get('loans') is not none }}"
        value_template: "{{ value_json.loans.open }}"
      - name: "Shelf overdue loans"
        unique_id: shelf_loans_overdue
        icon: mdi:book-alert
        availability: "{{ value_json is mapping and value_json.get('loans') is not none }}"
        value_template: "{{ value_json.loans.overdue }}"
      - name: "Shelf next release"
        unique_id: shelf_next_release
        device_class: date
        availability: >-
          {{ value_json is mapping
             and (value_json.get('upcoming') or {}).get('next_release_date') is not none }}
        value_template: "{{ value_json.upcoming.next_release_date }}"
```

`value_json['items']` uses brackets because `items` is also the name of a
Python dict method. Each `availability` line makes its sensor
**unavailable** rather than an error when there is nothing to show: the
section is `null` (Lending off), no upcoming release has a date yet, or Shelf
refused the token (revoked, or the API feature turned off).

An automation that tells you when a loan is overdue:

```yaml
automation:
  - alias: "Shelf: a loan is overdue"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.shelf_overdue_loans
        above: 0
    actions:
      - action: notify.notify
        data:
          title: "Shelf"
          message: >-
            {{ states('sensor.shelf_overdue_loans') }} loan(s) overdue.
```

## Self-signed certificates

Shelf serves HTTPS with a self-signed certificate by default, so each tool
must either skip certificate checks for Shelf or trust Shelf's certificate.

- **Home Assistant:** `verify_ssl: false` on the `rest` resource, as above.
  To keep verification on, give Shelf a certificate your Home Assistant
  trusts (see below) and remove the line.
- **Homepage:** Homepage's server-side proxy does not verify certificates for
  `https://` service widget URLs, so the recipe works against a self-signed
  Shelf with no extra setting. (This is how Homepage behaves today; it is
  not a Shelf setting.)
- **curl and scripts:** `curl -k`, or `--cacert` with Shelf's certificate.

To stop skipping checks, put Shelf behind a reverse proxy with a real
certificate, or trust Shelf's own certificate on the machine that runs the
tool. See [HTTPS & reverse proxy](https-and-reverse-proxy.md).

## Security advice

- Use a dedicated viewer user for dashboards. Its tokens see exactly what
  that viewer sees, including collection value, and nothing more.
- Keep the token in `secrets.yaml`, an environment variable or your
  dashboard's secret store, never in a file you share or commit.
- Make one token per tool, so you can revoke one without breaking the
  others. Revoke from the account menu, or ask an admin.
- **Tokens are not in the portable archive.** A database backup contains
  token hashes (not tokens); a portable archive contains none. After you
  restore an archive into a new install, make new tokens and update every
  dashboard.
