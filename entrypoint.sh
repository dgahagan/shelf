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

# Drop to non-root user for the application
exec gosu shelf uvicorn app.main:app --host 0.0.0.0 --port 18888 \
    --ssl-keyfile "$KEY_FILE" --ssl-certfile "$CERT_FILE"
