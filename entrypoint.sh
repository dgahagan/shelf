#!/bin/sh
set -e

CERT_DIR="/data/certs"
CERT_FILE="$CERT_DIR/cert.pem"
KEY_FILE="$CERT_DIR/key.pem"

# Exactly 'on' or 'off' (unset/empty = on); checked before any side effect.
_SHELF_TLS="${SHELF_TLS:-on}"
if [ "$_SHELF_TLS" != "on" ] && [ "$_SHELF_TLS" != "off" ]; then
    echo "ERROR: Invalid SHELF_TLS value: '$_SHELF_TLS'" >&2
    echo "Must be 'on' or 'off'." >&2
    exit 1
fi

# Ensure data directories exist; ownership is fixed only where it is wrong.
mkdir -p /data/certs /data/covers
# Only wrong-owned entries are re-owned, so a correctly owned tree gets no chown
# call: no ctime churn for backup tools, no EPERM on mounts where root cannot chown.
# find without -L does not follow symlinks, and chown -h re-owns the link itself,
# never a target outside /data.
# /bin/sh is dash with no pipefail, so the pipeline's status is xargs's: a failing
# chown (xargs exits 123) stops the start, a failing find would not. Accepted:
# root can read /data on the mounts this targets.
if ! find /data \( ! -user shelf -o ! -group shelf \) -print0 | xargs -0 -r chown -h shelf:shelf; then
    echo "ERROR: could not give /data to the shelf user (UID 1000)." >&2
    echo "       See docs/troubleshooting.md, 'Permission denied on /data'." >&2
    exit 1
fi

if [ "$_SHELF_TLS" = "on" ]; then
    # Generate self-signed certificate if it doesn't exist
    if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
        echo "Generating self-signed TLS certificate..."

        # Validate CERT_SAN to prevent command injection via subshell expansion
        _CERT_SAN="${CERT_SAN:-DNS:shelf,DNS:localhost}"
        if ! printf '%s' "$_CERT_SAN" | grep -qE '^(DNS:[a-zA-Z0-9._-]+|IP:[0-9.]+)(,(DNS:[a-zA-Z0-9._-]+|IP:[0-9.]+))*$'; then
            echo "ERROR: Invalid CERT_SAN value: '$_CERT_SAN'" >&2
            echo "       Must be comma-separated DNS:<name> or IP:<addr> entries." >&2
            exit 1
        fi

        openssl req -x509 -newkey rsa:2048 -nodes \
            -keyout "$KEY_FILE" -out "$CERT_FILE" \
            -days 730 -subj "/CN=shelf" \
            -addext "subjectAltName=${_CERT_SAN}"
        chown shelf:shelf "$KEY_FILE" "$CERT_FILE"
        echo "Certificate generated at $CERT_DIR"
    fi
fi

# SHELF_TRUST_PROXY is handed to uvicorn as FORWARDED_ALLOW_IPS, the list of
# proxies whose forwarded headers it honours; the app itself never reads it.
# The legacy value 1, or anything that is not an address list or a star, means
# the documented same-host proxy, so it maps to the loopback address with a
# warning rather than failing a start that used to succeed.
if [ -n "${SHELF_TRUST_PROXY:-}" ]; then
    _trust_ok=1
    if [ "$SHELF_TRUST_PROXY" != "*" ]; then
        set -f
        _old_ifs=$IFS
        IFS=','
        for _hop in $SHELF_TRUST_PROXY; do
            _hop=$(printf '%s' "$_hop" | tr -d '[:space:]')
            if ! printf '%s' "$_hop" | grep -qE '^([0-9]{1,3}\.){3}[0-9]{1,3}(/[0-9]{1,2})?$|^[0-9A-Fa-f:]*:[0-9A-Fa-f:]*(/[0-9]{1,3})?$'; then
                _trust_ok=0
            fi
        done
        IFS=$_old_ifs
        set +f
    fi
    if [ "$_trust_ok" = 1 ]; then
        export FORWARDED_ALLOW_IPS="$SHELF_TRUST_PROXY"
    else
        echo "WARNING: SHELF_TRUST_PROXY='$SHELF_TRUST_PROXY' is not an address list; trusting 127.0.0.1 (same-host proxy)." >&2
        echo "         Set it to your proxy's address, e.g. SHELF_TRUST_PROXY=172.17.0.1" >&2
        export FORWARDED_ALLOW_IPS=127.0.0.1
    fi
fi

# Drop to non-root user for the application
if [ "$_SHELF_TLS" = "on" ]; then
    exec gosu shelf uvicorn app.main:app --host 0.0.0.0 --port "${SHELF_PORT:-18888}" \
        --ssl-keyfile "$KEY_FILE" --ssl-certfile "$CERT_FILE"
else
    exec gosu shelf uvicorn app.main:app --host 0.0.0.0 --port "${SHELF_PORT:-18888}"
fi
