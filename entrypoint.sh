#!/bin/sh
set -e

# SHELF_CERT_DIR is a test hook for tests/test_entrypoint.py, not a supported setting.
CERT_DIR="${SHELF_CERT_DIR:-/data/certs}"
CERT_FILE="$CERT_DIR/cert.pem"
KEY_FILE="$CERT_DIR/key.pem"
MARKER_FILE="$CERT_DIR/cert.sha256"
NEW_KEY="$CERT_DIR/.new-key.pem"
NEW_CERT="$CERT_DIR/.new-cert.pem"
OLD_KEY="$CERT_DIR/.old-key.pem"
OLD_CERT="$CERT_DIR/.old-cert.pem"
OLD_MARKER="$CERT_DIR/.old-cert.sha256"

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

# Shelf renews only a certificate it generated itself, recognised by cert.sha256,
# which holds that certificate's SHA-256 fingerprint. /bin/sh is dash with no
# pipefail, so every openssl probe below sits in an `if` or a `||` and the start is
# never aborted by a failing probe (G63).

# Reads a SAN list on stdin, prints it as a sorted, comma-joined, lowercase set with
# OpenSSL's "IP Address:" spelling folded into "IP:".
_san_norm() {
    tr -d ' ' | tr 'A-Z' 'a-z' | sed 's/ipaddress:/ip:/g' | tr ',' '\n' | sort -u | paste -sd, -
}

_cert_san_norm() {
    openssl x509 -in "$CERT_FILE" -noout -ext subjectAltName 2>/dev/null | tail -n 1 | _san_norm
}

_cert_fpr() {
    openssl x509 -in "$CERT_FILE" -noout -fingerprint -sha256 2>/dev/null | sed 's/.*=//'
}

_cert_name_is_shelf() {
    [ "$(openssl x509 -in "$CERT_FILE" -noout -subject 2>/dev/null | tr -d ' ')" = "subject=CN=shelf" ] &&
        [ "$(openssl x509 -in "$CERT_FILE" -noout -issuer 2>/dev/null | tr -d ' ')" = "issuer=CN=shelf" ]
}

# Sets _state to missing | unreadable | ours | enrol | foreign. Always returns 0.
_cert_state() {
    _state=foreign
    if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
        _state=missing
        return 0
    fi
    if ! openssl x509 -in "$CERT_FILE" -noout >/dev/null 2>&1; then
        _state=unreadable
        return 0
    fi
    if [ -f "$MARKER_FILE" ]; then
        _fpr=$(_cert_fpr)
        if [ -n "$_fpr" ] && [ "$(cat "$MARKER_FILE" 2>/dev/null)" = "$_fpr" ]; then
            _state=ours
        fi
        return 0
    fi
    if _cert_name_is_shelf && [ "$(_cert_san_norm)" = "$_want_san" ]; then
        _state=enrol
    fi
    return 0
}

# Copies each live file that exists to its .old-* name. Returns 1 on any failure.
_backup() {
    rm -f "$OLD_KEY" "$OLD_CERT" "$OLD_MARKER"
    { [ ! -e "$KEY_FILE" ] || cp -p "$KEY_FILE" "$OLD_KEY"; } &&
        { [ ! -e "$CERT_FILE" ] || cp -p "$CERT_FILE" "$OLD_CERT"; } &&
        { [ ! -e "$MARKER_FILE" ] || cp -p "$MARKER_FILE" "$OLD_MARKER"; }
}

# Puts back what _backup saved. A live file that had no copy did not exist before,
# so it is removed.
_restore_one() {
    if [ -e "$2" ]; then mv -f "$2" "$1"; else rm -f "$1"; fi
}
_restore() {
    _restore_one "$KEY_FILE" "$OLD_KEY"
    _restore_one "$CERT_FILE" "$OLD_CERT"
    _restore_one "$MARKER_FILE" "$OLD_MARKER"
}

# Generates into temporary files, checks them, then moves them into place.
# Returns 1 on any failure, with the live key, certificate and marker as they were:
# a failure after the first move restores all three from the .old-* copies.
_generate() {
    rm -f "$NEW_KEY" "$NEW_CERT"
    if ! { openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout "$NEW_KEY" -out "$NEW_CERT" \
        -days 730 -subj "/CN=shelf" \
        -addext "subjectAltName=${_CERT_SAN}" &&
        openssl x509 -in "$NEW_CERT" -noout &&
        _pub_cert=$(openssl x509 -in "$NEW_CERT" -noout -pubkey) &&
        _pub_key=$(openssl pkey -in "$NEW_KEY" -pubout) &&
        [ -n "$_pub_cert" ] && [ "$_pub_cert" = "$_pub_key" ] &&
        _new_fpr=$(openssl x509 -in "$NEW_CERT" -noout -fingerprint -sha256 | sed 's/.*=//') &&
        [ -n "$_new_fpr" ] && _backup; }; then
        rm -f "$NEW_KEY" "$NEW_CERT" "$OLD_KEY" "$OLD_CERT" "$OLD_MARKER"
        return 1
    fi
    if mv -f "$NEW_KEY" "$KEY_FILE" && mv -f "$NEW_CERT" "$CERT_FILE" &&
        printf '%s\n' "$_new_fpr" > "$MARKER_FILE"; then
        rm -f "$OLD_KEY" "$OLD_CERT" "$OLD_MARKER"
        chown shelf:shelf "$KEY_FILE" "$CERT_FILE" "$MARKER_FILE"
        return 0
    fi
    _restore
    rm -f "$NEW_KEY" "$NEW_CERT"
    return 1
}

# $1 = missing | renew
_generate_or_keep() {
    if _generate; then
        echo "Certificate generated at $CERT_DIR"
        return 0
    fi
    if [ "$1" = "missing" ]; then
        echo "ERROR: could not generate the TLS certificate." >&2
        exit 1
    fi
    echo "ERROR: certificate renewal failed." >&2
    if openssl x509 -in "$CERT_FILE" -noout -checkend 0 >/dev/null 2>&1; then
        echo "       Continuing with the existing certificate, which is still valid." >&2
    else
        echo "       The existing certificate has expired; not starting." >&2
        exit 1
    fi
}

if [ "$_SHELF_TLS" = "on" ]; then
    # Validate CERT_SAN to prevent command injection via subshell expansion
    _CERT_SAN="${CERT_SAN:-DNS:shelf,DNS:localhost}"
    if ! printf '%s' "$_CERT_SAN" | grep -qE '^(DNS:[a-zA-Z0-9._-]+|IP:[0-9.]+)(,(DNS:[a-zA-Z0-9._-]+|IP:[0-9.]+))*$'; then
        echo "ERROR: Invalid CERT_SAN value: '$_CERT_SAN'" >&2
        echo "       Must be comma-separated DNS:<name> or IP:<addr> entries." >&2
        exit 1
    fi
    _want_san=$(printf '%s\n' "$_CERT_SAN" | _san_norm)

    _cert_state
    _renew=""
    case "$_state" in
        missing)
            echo "Generating self-signed TLS certificate..."
            _generate_or_keep missing
            ;;
        unreadable)
            echo "WARNING: $CERT_FILE cannot be read as a PEM certificate; Shelf did not regenerate it." >&2
            ;;
        foreign)
            if ! openssl x509 -in "$CERT_FILE" -noout -checkend 2592000 >/dev/null 2>&1; then
                echo "WARNING: the certificate in $CERT_DIR was not generated by Shelf and expires $(openssl x509 -in "$CERT_FILE" -noout -enddate 2>/dev/null | sed 's/notAfter=//'); Shelf did not regenerate it." >&2
            fi
            if [ ! -f "$MARKER_FILE" ] && _cert_name_is_shelf; then
                echo "NOTE: the certificate in $CERT_DIR is named CN=shelf but does not match CERT_SAN and was not generated by this Shelf. To let Shelf manage it, delete data/certs/ once and restart." >&2
            fi
            ;;
        enrol | ours)
            if [ "$_state" = "enrol" ]; then
                _fpr=$(_cert_fpr)
                if [ -n "$_fpr" ] && printf '%s\n' "$_fpr" > "$MARKER_FILE"; then
                    chown shelf:shelf "$MARKER_FILE"
                    echo "Adopted the existing certificate as Shelf's own."
                else
                    echo "WARNING: could not write $MARKER_FILE; Shelf did not adopt the certificate." >&2
                    _state=foreign
                fi
            fi
            if [ "$_state" = "ours" ] || [ "$_state" = "enrol" ]; then
                if ! openssl x509 -in "$CERT_FILE" -noout -checkend 2592000 >/dev/null 2>&1; then
                    _renew="Certificate expires $(openssl x509 -in "$CERT_FILE" -noout -enddate 2>/dev/null | sed 's/notAfter=//'); renewing."
                elif [ "$(_cert_san_norm)" != "$_want_san" ]; then
                    _renew="CERT_SAN changed; renewing."
                fi
                if [ -n "$_renew" ]; then
                    echo "$_renew"
                    echo "Devices that trust cert.pem must install the new one."
                    _generate_or_keep renew
                fi
            fi
            ;;
    esac
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
