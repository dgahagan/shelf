# Sharing

Settings → Data → **Sharing** creates public, read-only links — no login
needed by the viewer.

| Link type | Shows |
|---|---|
| **Wishlist** | Your wishlist items: title, author, cover, series. For gift ideas |
| **Collection** | Your owned items, same fields. For "what do you have?" |

What a link **never** shows: locations, loans, values, notes, ISBNs, tags,
reading status, or anything about users. It's a cover wall with titles.

## Properties

- URLs carry an unguessable 128-bit token (`/share/<token>`).
- Pages are `noindex` so search engines skip them, and rate-limited per IP.
- **Revoke** any link from the same card; the URL dies immediately. Create a
  new one whenever you like.
- Links are live: the page reflects your library at view time, so a
  wishlist link you sent in November is still right in December.

## Outside the LAN

A share link is only useful if the recipient can reach your server. If
Shelf is LAN-only, the usual answers are a reverse proxy with a real
certificate for just this host, or a tunnel (Cloudflare Tunnel, Tailscale
Funnel) — see [HTTPS & reverse proxy](../https-and-reverse-proxy.md). The
self-signed certificate will make a recipient's browser complain, so a real
cert matters more for sharing than for your own use.
