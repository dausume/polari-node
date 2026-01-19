#!/bin/bash
set -e

# Polari Research Framework (PRF) Certificate Generator
# For standalone PRF deployment or as part of Polari Suite
# Generates certificates for: proxy, backend, and frontend services
#
# Can be called directly or from parent generate-pol-certs.sh
# When called from parent, accepts args to bypass interactive prompts

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ==============================================================================
# ARGUMENT PARSING
# ==============================================================================
# Supports both positional args (for direct use) and flags (for parent call)
#
# Direct usage:   ./generate-prf-certs.sh [dev|prod|cleanup]
# Parent call:    ./generate-prf-certs.sh prod --parent-call --server-ip=<IP> [--shared-ca=<path>]
# ==============================================================================

ENV="${1:-dev}"
PARENT_CALL=false
SERVER_IP=""
SHARED_CA_DIR=""

# Parse additional arguments
shift || true  # Shift past the environment arg if it exists
while [[ $# -gt 0 ]]; do
    case $1 in
        --parent-call)
            PARENT_CALL=true
            shift
            ;;
        --server-ip=*)
            SERVER_IP="${1#*=}"
            shift
            ;;
        --shared-ca=*)
            SHARED_CA_DIR="${1#*=}"
            shift
            ;;
        *)
            shift
            ;;
    esac
done

if [[ "$ENV" != "dev" && "$ENV" != "prod" && "$ENV" != "cleanup" ]]; then
    echo "Usage: $0 [dev|prod|cleanup] [options]"
    echo ""
    echo "Environments:"
    echo "  dev     - Development environment (localhost, self-signed)"
    echo "  prod    - Production environment (prf.polari-systems.org)"
    echo "  cleanup - Remove all existing certificates and keys"
    echo ""
    echo "Options (for parent script integration):"
    echo "  --parent-call         Script is being called from parent orchestrator"
    echo "  --server-ip=<IP>      Server IPv6 address (required for prod with --parent-call)"
    echo "  --shared-ca=<PATH>    Path to shared CA directory (uses existing CA instead of generating)"
    exit 1
fi

# Directory structure - certs accessible to all services
CERTS_DIR="$SCRIPT_DIR/prf-proxy/certs"
CA_DIR="$CERTS_DIR/ca"

# Handle cleanup
if [[ "$ENV" == "cleanup" ]]; then
    echo "Cleaning up PRF certificates..."

    if [[ -d "$CERTS_DIR" ]]; then
        rm -rf "$CERTS_DIR"
        mkdir -p "$CERTS_DIR"
        echo "  - Cleaned $CERTS_DIR"
    fi

    echo ""
    echo "Cleanup complete. Run '$0 dev' or '$0 prod' to generate new certificates."
    exit 0
fi

echo "============================================"
echo "PRF Certificate Generator"
echo "Environment: $ENV"
if [[ "$PARENT_CALL" == "true" ]]; then
    echo "Mode: Called from parent orchestrator"
fi
echo "============================================"
echo ""

# Environment-specific configuration
if [[ "$ENV" == "dev" ]]; then
    CA_SUBJ="/C=US/ST=State/L=City/O=Polari/OU=PRF/CN=PRF Dev CA"
    PROXY_CN="localhost"
    PROXY_SANS="DNS.1 = localhost
DNS.2 = prf-proxy
DNS.3 = host.docker.internal
DNS.4 = *.localhost"
    SERVER_IP=""
else
    # Production environment
    if [[ "$PARENT_CALL" == "true" ]]; then
        # Called from parent - use provided values, no prompts
        if [[ -z "$SERVER_IP" ]]; then
            echo "Error: --server-ip is required for production mode with --parent-call"
            exit 1
        fi
        echo "Using server IP from parent: $SERVER_IP"
    else
        # Direct call - show warning and prompt
        echo ""
        echo "WARNING: Production certificates should only be generated on the production server itself."
        echo "         Do not run this on a development machine."
        echo ""
        read -p "Are you running this on the production server? (yes/no): " CONFIRM
        if [[ "$CONFIRM" != "yes" ]]; then
            echo "Aborting. Please run this script on the production server."
            exit 1
        fi

        read -p "Enter the server's public IPv6 address: " SERVER_IP
        if [[ -z "$SERVER_IP" ]]; then
            echo "Error: IPv6 address is required for production."
            exit 1
        fi
    fi

    CA_SUBJ="/C=US/ST=VA/L=Arlington/O=Polari/OU=PRF/CN=prf.polari-systems.org CA"
    PROXY_CN="prf.polari-systems.org"
    PROXY_SANS="DNS.1 = prf.polari-systems.org
DNS.2 = api.prf.polari-systems.org
DNS.3 = prf-proxy
IP.1 = $SERVER_IP"
fi

# Create directory structure
mkdir -p "$CA_DIR"

# Check if we should use a shared CA
if [[ -n "$SHARED_CA_DIR" && -f "$SHARED_CA_DIR/pol-ca.crt" && -f "$SHARED_CA_DIR/pol-ca.key" ]]; then
    echo "1. Using shared CA from: $SHARED_CA_DIR"
    USE_SHARED_CA=true
    CA_CRT="$SHARED_CA_DIR/pol-ca.crt"
    CA_KEY="$SHARED_CA_DIR/pol-ca.key"
    # Copy CA to local directory for reference
    cp "$CA_CRT" "$CA_DIR/prf-ca.crt"
    cp "$CA_KEY" "$CA_DIR/prf-ca.key"
    chmod 600 "$CA_DIR/prf-ca.key"
    echo "   Shared CA copied to: $CA_DIR/"
else
    echo "1. Generating CA certificate..."
    USE_SHARED_CA=false
    CA_CRT="$CA_DIR/prf-ca.crt"
    CA_KEY="$CA_DIR/prf-ca.key"
    openssl genrsa -out "$CA_KEY" 4096
    openssl req -new -x509 -days 3650 -key "$CA_KEY" -out "$CA_CRT" \
        -subj "$CA_SUBJ"
    echo "   CA certificate created: $CA_CRT"
fi

echo ""
echo "2. Generating PRF Proxy certificate (CN=$PROXY_CN)..."
openssl genrsa -out "$CERTS_DIR/prf-proxy.key" 2048

cat > "$CERTS_DIR/prf-proxy.cnf" <<EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
req_extensions = v3_req

[dn]
C=US
ST=VA
L=Arlington
O=Polari
OU=PRF
CN=$PROXY_CN

[v3_req]
subjectAltName = @alt_names

[alt_names]
$PROXY_SANS
EOF

openssl req -new -key "$CERTS_DIR/prf-proxy.key" -out "$CERTS_DIR/prf-proxy.csr" \
    -config "$CERTS_DIR/prf-proxy.cnf"

openssl x509 -req -in "$CERTS_DIR/prf-proxy.csr" -CA "$CA_CRT" \
    -CAkey "$CA_KEY" -CAcreateserial -out "$CERTS_DIR/prf-proxy.crt" \
    -days 825 -sha256 -extfile "$CERTS_DIR/prf-proxy.cnf" -extensions v3_req

rm "$CERTS_DIR/prf-proxy.csr" "$CERTS_DIR/prf-proxy.cnf"
chmod 600 "$CERTS_DIR/prf-proxy.key"
echo "   Proxy certificate created: $CERTS_DIR/prf-proxy.crt"

echo ""
echo "============================================"
echo "PRF Certificate generation complete!"
echo "============================================"
echo ""
echo "Generated certificates:"
echo "  CA:    $CA_DIR/prf-ca.{crt,key}"
echo "  Cert:  $CERTS_DIR/prf-proxy.{crt,key}"
echo ""
echo "These certificates can be used by:"
echo "  - prf-proxy (NGINX reverse proxy)"
echo "  - prf-backend (Python/Falcon on HTTPS port 2096)"
echo "  - prf-frontend (Angular on HTTPS port 2087)"
echo ""
echo "Dev mode ports (Cloudflare-compatible HTTPS):"
echo "  Frontend: HTTP 4200, HTTPS 2087"
echo "  Backend:  HTTP 3000, HTTPS 2096"
echo ""
echo "Verifying certificate:"
openssl x509 -in "$CERTS_DIR/prf-proxy.crt" -noout -subject -ext subjectAltName
