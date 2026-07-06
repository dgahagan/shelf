# Security Policy

## Reporting a Vulnerability

Please report vulnerabilities privately via
[GitHub private vulnerability reporting](https://github.com/dgahagan/shelf/security/advisories/new)
— do **not** open a public issue for security problems.

You can expect an acknowledgement within a few days. This is a personal
project, so there's no formal SLA, but security reports get priority over
everything else.

## Supported Versions

Only the latest release (and `main`) receive security fixes.

## Security Posture

Shelf is designed to run on a private home network, but it's hardened as if
it weren't:

- Strict Content-Security-Policy — no `unsafe-inline`, no `unsafe-eval`, no
  CDNs (all assets vendored)
- CSRF protection on all mutating requests
- bcrypt password hashing; JWT sessions in HTTP-only, secure cookies
- Role-based access control (admin / editor / viewer)
- Third-party API credentials encrypted at rest (key kept outside the DB, so
  database backups contain ciphertext only) and write-only in the UI
- Optional passphrase-encrypted (AES) backup downloads
- HTTPS by default (self-signed certs generated on first run)
- Container runs as a non-root user

If you're exposing Shelf beyond your LAN, put it behind a reverse proxy with
a real certificate and set `SHELF_TRUST_PROXY=1`.
