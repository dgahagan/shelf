# HTTPS & reverse proxy

Shelf serves HTTPS from the first start, with a self-signed certificate it
generates into `data/certs/`. HTTPS is not optional: a secure origin is
mandatory for anything using `getUserMedia` — the barcode scanner and Photo
Intake's desktop webcam viewfinder — and for the offline Store Mode (service
workers). The one exception is Photo Intake's **Take photo** button on a
phone, which opens the native camera app via an HTML capture input rather
than `getUserMedia`, and so works even over plain `http://`.

## The certificate warning

On first visit every browser warns that the certificate is not trusted.
Clicking through is safe on your own LAN. The warning reappears per device
and, on some browsers, per session. Three ways to be rid of it:

### 1. Trust the certificate on each device

Download `data/certs/cert.pem` from the server and install it:

- **Android** — Settings → Security → Encryption & credentials → Install a
  certificate → **CA certificate**.
- **iOS / iPadOS** — open the `.pem` (AirDrop or a file share), install the
  profile in Settings, then enable full trust under Settings → General →
  About → Certificate Trust Settings.
- **Desktop** — import into the OS or browser trust store as a trusted root.

Make sure `CERT_SAN` included the IP/hostname you actually type; a
certificate trusted for `shelf` still warns for `192.168.1.100`. To
regenerate: stop Shelf, delete `data/certs/`, set `CERT_SAN`, start again,
then re-install the new cert on your devices.

### 2. Reverse proxy with a real certificate

Put Caddy, Traefik, nginx or Nginx Proxy Manager in front of Shelf and let it
terminate TLS with Let's Encrypt (or `tailscale cert` for a `ts.net` name).
The proxy talks to Shelf over **HTTPS** (Shelf does not serve plain HTTP), so
tell it to skip verification of the self-signed upstream.

Caddy example:

```
shelf.example.com {
    reverse_proxy https://127.0.0.1:18888 {
        transport http {
            tls_insecure_skip_verify
        }
    }
}
```

nginx example:

```nginx
location / {
    proxy_pass https://127.0.0.1:18888;
    proxy_ssl_verify off;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
}
```

Then set `SHELF_TRUST_PROXY=1` so login rate limiting and the auth log see
the real client IP instead of the proxy's. **Do not set it without a proxy**
— the forwarded headers are client-controlled.

### 3. VPN home

With WireGuard, Tailscale or similar you still see the self-signed warning,
but Store Mode's offline cache only needs the connection when syncing, so the
warning is a one-time nuisance rather than a daily one.

## Exposing Shelf to the internet

Shelf is hardened as if it were public (strict CSP, CSRF, bcrypt, rate
limits, non-root container), but it is designed for a home network. If you
expose it, use option 2 with a real certificate, keep it updated, and
consider an authenticating proxy or VPN in front. Public share links
(Settings → Data → Sharing) are the intended way to show your wishlist to
people outside the house.

## Store Mode specifics

Store Mode registers a service worker, which browsers only allow on
`localhost` or an origin whose certificate they trust. So for the offline
bookstore workflow on a phone you need option 1 or 2 above. Once installed
("Add to Home Screen" from the store page) it keeps working offline until
you next open it online to sync.
