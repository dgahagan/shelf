#!/bin/sh
set -e

CERT_DIR="/data/certs"
CERT_FILE="$CERT_DIR/cert.pem"
KEY_FILE="$CERT_DIR/key.pem"

# Ensure data directories exist and are owned by shelf user
mkdir -p /data/certs /data/covers
chown -R shelf:shelf /data

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
exec gosu shelf uvicorn app.main:app --host 0.0.0.0 --port "${SHELF_PORT:-18888}" \
    --ssl-keyfile "$KEY_FILE" --ssl-certfile "$CERT_FILE"
