# Installation

Shelf ships as a single Docker image, [`dangahagan/shelf`](https://hub.docker.com/r/dangahagan/shelf).
It needs nothing else — no database server, no reverse proxy, no API keys.

## Requirements

- Docker 20+ (or Podman) with Compose
- ~200 MB disk for the image, plus space for your covers (a few KB each)
- A browser on the same network — phone, tablet or desktop. Camera scanning
  needs HTTPS, which Shelf provides out of the box

## Docker Compose (recommended)

Create a directory, add a `docker-compose.yml`:

```yaml
services:
  shelf:
    image: dangahagan/shelf:latest
    container_name: shelf
    ports:
      - "18888:18888"
    environment:
      - CERT_SAN=${CERT_SAN:-DNS:shelf,DNS:localhost}
    volumes:
      - ./data:/data:z
    restart: unless-stopped
```

Optionally add a `.env` beside it so other devices can reach Shelf without a
hostname mismatch on the certificate:

```bash
# your server's LAN address and any names you'll use in the browser
CERT_SAN=IP:192.168.1.100,DNS:shelf,DNS:localhost
```

Then:

```bash
docker compose up -d
```

Open **https://localhost:18888** (or `https://<server-ip>:18888`). Your browser
will warn about the self-signed certificate — that is expected on first run;
see [HTTPS & reverse proxy](https-and-reverse-proxy.md) for how to make the
warning go away. The setup wizard asks you to create the first admin account,
and you're in.

> The `:z` on the volume is an SELinux relabel flag. It is harmless on systems
> without SELinux and required on Fedora/RHEL-family hosts.

## `docker run`

```bash
mkdir -p shelf-data
docker run -d \
  --name shelf \
  -p 18888:18888 \
  -v ./shelf-data:/data:z \
  dangahagan/shelf:latest
```

## Image tags

| Tag | Meaning |
|---|---|
| `latest` | Latest stable release |
| `x.y.z` | A specific release, e.g. `0.13.0` |
| `x.y` | Latest patch of a minor line, e.g. `0.13` |
| `beta` | Pre-release, may have rough edges |

Pin to `x.y.z` if you want upgrades to be deliberate; see
[Upgrading & backups](upgrading-and-backups.md).

## The data directory

Everything Shelf persists lives in the one volume mounted at `/data`:

```
data/
  shelf.db        — SQLite database: your whole catalog, users, settings
  covers/         — cached cover images
  certs/          — self-signed TLS certificate, generated on first start
  encryption.key  — key for API credentials stored in the DB
                    (unless SHELF_ENCRYPTION_KEY is set)
```

Back it up by copying the directory, or use Settings → Data → Backup &
Restore. Keep `encryption.key` out of anything you share — without it, stored
API credentials are unreadable ciphertext, which is the point.

## Changing the port

The app listens on `18888` inside the container. Change the host side of the
port mapping (`- "8443:18888"`) rather than the container side. If you must
change the container port (e.g. host networking), set `SHELF_PORT`.

## Running from source

See [Development](development.md). The short version:

```bash
git clone https://github.com/dgahagan/shelf.git && cd shelf
pip install -r requirements.txt
DATA_DIR=./data uvicorn app.main:app --reload
```

Running without Docker means no auto-generated certificate — `uvicorn` will
serve plain HTTP on `127.0.0.1:8000`, which is fine for `localhost`
(browsers treat it as a secure context) but camera scanning from a phone
needs HTTPS.

## Next

- [Getting started](user-guide/getting-started.md) — the setup wizard and
  your first scan
- [Configuration](configuration.md) — every environment variable and setting
