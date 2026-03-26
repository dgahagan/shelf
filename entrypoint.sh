#!/bin/sh
set -e

CERT_DIR="/data/certs"
CERT_FILE="$CERT_DIR/cert.pem"
KEY_FILE="$CERT_DIR/key.pem"

# Generate self-signed certificate if it doesn't exist
if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
    echo "Generating self-signed TLS certificate..."
    mkdir -p "$CERT_DIR"
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout "$KEY_FILE" -out "$CERT_FILE" \
        -days 3650 -subj "/CN=shelf" \
        -addext "subjectAltName=IP:192.168.1.50,DNS:shelf,DNS:localhost"
    echo "Certificate generated at $CERT_DIR"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 18888 \
    --ssl-keyfile "$KEY_FILE" --ssl-certfile "$CERT_FILE"
