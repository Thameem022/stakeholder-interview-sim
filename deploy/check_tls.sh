#!/usr/bin/env bash
# TLS scan for SES (SR-2026-052 item 2.3, SEC-ENC-001). Needs only openssl
# (and curl for the header check), so it runs on the VM itself.
#
#   deploy/check_tls.sh [HOST[:PORT]]          what the SES vhost accepts
#   deploy/check_tls.sh --bedrock [REGION]     what the Bedrock endpoints negotiate
#
# Inbound, it expects: TLS 1.0/1.1 refused; TLS 1.3 negotiated whenever the
# client offers it; every accepted TLS 1.2 cipher an AEAD one (GCM, ChaCha20-
# Poly1305, CCM); HSTS on the response. Outbound, it expects TLS 1.3 to every
# Bedrock endpoint SES calls, as negotiated by the same Python TLS stack the
# application uses. Exits non-zero if anything is off.
#
# For a local test against a copy of the vhost: CHECK_TLS_CONNECT=127.0.0.1
# connects there while still sending HOST as the server name, and --insecure
# accepts a self-signed certificate for the header check.
set -uo pipefail

OPENSSL=${OPENSSL:-openssl}
fail=0
pass() { printf '  ok    %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; fail=1; }

# Handshake with the given s_client options; prints the protocol on success.
handshake() {
    local target=$1 sni=$2; shift 2
    "$OPENSSL" s_client -connect "$target" -servername "$sni" -brief "$@" </dev/null 2>&1 \
        | sed -n 's/^Protocol version: *//p' | head -1
}

inbound() {
    local host=${1%%:*} port=443 insecure=$2
    [[ $1 == *:* ]] && port=${1##*:}
    local target="${CHECK_TLS_CONNECT:-$host}:$port"
    echo "Inbound TLS: $host:$port"

    # Old protocols: the client is allowed to offer them (SECLEVEL=0), so a
    # refusal is the server's.
    for proto in tls1 tls1_1; do
        if [[ -n $(handshake "$target" "$host" "-$proto" -cipher 'DEFAULT:@SECLEVEL=0') ]]; then
            bad "$proto accepted"
        else
            pass "$proto refused"
        fi
    done

    local v
    v=$(handshake "$target" "$host")
    [[ $v == TLSv1.3 ]] && pass "TLS 1.3 negotiated by default" || bad "default handshake negotiated '${v:-nothing}', expected TLSv1.3"
    v=$(handshake "$target" "$host" -tls1_2)
    [[ $v == TLSv1.2 ]] && pass "TLS 1.2 available for older clients" || bad "TLS 1.2 not available"

    # Every TLS 1.2 cipher this openssl knows, one handshake each.
    local cipher accepted=0
    for cipher in $("$OPENSSL" ciphers -tls1_2 'ALL:COMPLEMENTOFALL:@SECLEVEL=0' | tr ':' ' '); do
        [[ -n $(handshake "$target" "$host" -tls1_2 -cipher "$cipher:@SECLEVEL=0") ]] || continue
        accepted=$((accepted + 1))
        case $cipher in
            *GCM*|*CHACHA20*|*CCM*) pass "TLS 1.2 $cipher (AEAD)" ;;
            *) bad "TLS 1.2 $cipher accepted (not AEAD)" ;;
        esac
    done
    [[ $accepted -gt 0 ]] || bad "no TLS 1.2 cipher accepted"

    for cipher in TLS_AES_128_GCM_SHA256 TLS_AES_256_GCM_SHA384 TLS_CHACHA20_POLY1305_SHA256 \
                  TLS_AES_128_CCM_SHA256 TLS_AES_128_CCM_8_SHA256; do
        [[ -n $(handshake "$target" "$host" -tls1_3 -ciphersuites "$cipher") ]] \
            && pass "TLS 1.3 $cipher (AEAD)"
    done

    local headers
    headers=$(curl -sS -I ${insecure:+--insecure} \
        ${CHECK_TLS_CONNECT:+--resolve "$host:$port:$CHECK_TLS_CONNECT"} "https://$host:$port/" 2>&1)
    if grep -qi '^strict-transport-security: *max-age=[1-9]' <<<"$headers"; then
        pass "HSTS: $(grep -i '^strict-transport-security' <<<"$headers" | tr -d '\r' | cut -d' ' -f2-)"
    else
        bad "no Strict-Transport-Security header"
    fi
    grep -qi '^content-security-policy:' <<<"$headers" && pass "Content-Security-Policy present" || bad "no Content-Security-Policy header"
}

bedrock() {
    local region=$1 python=${SES_PYTHON:-/opt/stakeholder-engagement-simulator/backend/.venv/bin/python}
    [[ -x $python ]] || python=python3
    echo "Outbound TLS to Bedrock ($region), via $python"
    local host v
    for host in "bedrock-runtime.$region.amazonaws.com" "bedrock-mantle.$region.api.aws"; do
        # The application's own TLS stack: Python's default context, the one
        # botocore and the Anthropic SDK build on.
        v=$("$python" - "$host" <<'PY' 2>&1
import socket, ssl, sys
host = sys.argv[1]
ctx = ssl.create_default_context()
with socket.create_connection((host, 443), timeout=10) as raw:
    with ctx.wrap_socket(raw, server_hostname=host) as tls:
        print(tls.version())
PY
)
        [[ $v == TLSv1.3 ]] && pass "$host: TLSv1.3" || bad "$host: ${v:-no answer} (expected TLSv1.3)"
    done
}

case ${1:-} in
    --bedrock) bedrock "${2:-${AWS_REGION:-us-east-1}}" ;;
    --insecure) inbound "${2:-stakeholder-engagement-simulator.wpi.edu}" 1 ;;
    *) inbound "${1:-stakeholder-engagement-simulator.wpi.edu}" "" ;;
esac

if [[ $fail -ne 0 ]]; then echo "RESULT: FAIL"; exit 1; fi
echo "RESULT: ok"
