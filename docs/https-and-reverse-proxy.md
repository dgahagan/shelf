# HTTPS & reverse proxy

By default Shelf serves HTTPS from the first start, with a self-signed
certificate it generates into `data/certs/`. HTTPS is the default because a
secure origin is mandatory for anything using `getUserMedia` — the barcode
scanner and Photo Intake's desktop webcam viewfinder — and for the offline
Store Mode (service workers). The one exception is Photo Intake's **Take
photo** button on a phone, which opens the native camera app via an HTML
capture input rather than `getUserMedia`, and so works even over plain
`http://`.

Behind a reverse proxy that already terminates TLS, set `SHELF_TLS=off` and
Shelf serves plain HTTP to the proxy instead (option 2 below).

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
Set `SHELF_TLS=off` so Shelf serves plain HTTP to the proxy, and point the
proxy at `http://`.

Caddy example (Caddy sends `X-Forwarded-Proto` itself):

```
shelf.example.com {
    reverse_proxy http://127.0.0.1:18888
}
```

nginx example:

```nginx
location / {
    proxy_pass http://127.0.0.1:18888;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
}
```

Then set `SHELF_TRUST_PROXY` to **the proxy's address as Shelf sees it**.
With `SHELF_TLS=off` this is required whenever the proxy is not on the same
host. Shelf marks the login cookie `Secure` and sends HSTS only when the
browser reached it over HTTPS, and it learns that from the proxy's
`X-Forwarded-Proto` — which it believes only from a listed proxy. An
unlisted proxy still works, but the cookie loses `Secure`. The same setting
lets login rate limiting and the auth log see the real client IP instead of
the proxy's:

- `127.0.0.1` for a proxy on the same host. The bundled compose file uses host
  networking, so this is the usual value.
- The Docker bridge gateway when Shelf runs under `docker run -p`, commonly
  `172.17.0.1`.
- The proxy's LAN address when it runs on another machine.

With the bundled compose file and a proxy on the same host, set `127.0.0.1`
and stop here. Shelf already trusts `127.0.0.1` when the variable is unset, so
the log shows your clients' real addresses, not the proxy's.

For any other setup, find the address first. Leave the variable unset, fail
one login through the proxy, and read the `from <ip>` at the end of that line
in the log viewer. If that address is the same whatever device you log in
from, it is the proxy's: set it. If it changes from device to device, Shelf
already trusts the proxy — the address is a client's, and setting it would put
every client in one rate-limit bucket.

A chain of two proxies lists both, comma-separated
(`SHELF_TRUST_PROXY=10.0.0.5,127.0.0.1`). Shelf walks `X-Forwarded-For` from
the right and skips every listed proxy, so the first address it does not
trust is the client, whatever the client itself sent.

Behind Cloudflare, list Cloudflare's published IP ranges. `CF-Connecting-IP`
is not read.

Avoid `*`. It trusts every peer, and the server then takes the **left-most**
`X-Forwarded-For` entry, which the client wrote. That is the spoof this
setting exists to stop.

**Do not set it without a proxy.** An address that is not your proxy lets
anything at that address choose the client IP Shelf logs and rate-limits.

The legacy value `1` still works for a same-host proxy: it means `127.0.0.1`,
and Shelf prints a warning at startup asking for the address instead. Outside
Docker, the entrypoint that reads `SHELF_TRUST_PROXY` does not run; set
uvicorn's own `FORWARDED_ALLOW_IPS` to the same list.

**Opening Shelf from another site keeps the session.** The login cookie is
deliberately `SameSite=Strict`. A cross-site link from a dashboard (Homepage,
Homarr), a chat message or another host on your LAN still lands on the page
you clicked, logged in: Shelf has the browser re-request it once from its own
origin, with the cookie. Do not rewrite the cookie's `SameSite` at the proxy.

**Fallback: proxy to the built-in HTTPS listener.** Leave `SHELF_TLS` on and
point the proxy at `https://127.0.0.1:18888`, telling it to skip verification
of the self-signed upstream — Caddy's `transport http { tls_insecure_skip_verify }`
inside the `reverse_proxy` block, nginx's `proxy_ssl_verify off`. This is how
proxies were set up before `SHELF_TLS` existed, and those setups keep working
unchanged.

### 3. VPN home

With WireGuard, Tailscale or similar you still see the self-signed warning,
but Store Mode's offline cache only needs the connection when syncing, so the
warning is a one-time nuisance rather than a daily one.

## Plain HTTP without a proxy

`SHELF_TLS=off` with nothing in front serves plain `http://` on your LAN. It
works, but browsers withhold a secure context from any plain-HTTP origin
other than `localhost`, so these stop working:

- The camera barcode scanner.
- Photo Intake's desktop webcam viewfinder.
- Store Mode offline — the page works online, but it cannot be installed
  for offline use.

Typed and USB-scanner entry, Photo Intake's **Take photo** button on a phone,
and online Store Mode all keep working. Over plain HTTP the login cookie is
sent without `Secure` and there is no HSTS, since both describe a connection
that is not encrypted.

## Exposing Shelf to the internet

Shelf is hardened as if it were public (strict CSP, CSRF, bcrypt, rate
limits, non-root container), but it is designed for a home network. If you
expose it, use option 2 with a real certificate, keep it updated, and
consider an authenticating proxy or VPN in front. Public share links
(Settings → Data → Sharing) are the intended way to show your wishlist to
people outside the house.

## Store Mode specifics

Store Mode registers a service worker, which browsers only allow on
`localhost` or an HTTPS origin whose certificate they trust — never a plain
`http://` LAN address. So for the offline bookstore workflow on a phone you
need option 1 or 2 above. Once installed
("Add to Home Screen" from the store page) it keeps working offline until
you next open it online to sync.
