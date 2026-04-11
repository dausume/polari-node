#!/bin/bash
# ==============================================================================
# POLARI RESEARCH FRAMEWORK - STAGING SETUP SCRIPT
# ==============================================================================
#
# This script sets up the standalone PRF staging environment with nip.io
# subdomain routing. Auto-detects your local IP and generates configuration.
#
# Run this script before starting the staging environment:
#   ./staging-setup.sh
#
# Then start the environment:
#   sudo docker compose -f docker-compose.staging-nip.yml --env-file .generated/.env.staging up -d --build
#
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERTS_DIR="$SCRIPT_DIR/prf-proxy/certs"
GENERATED_DIR="$SCRIPT_DIR/.generated"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  Polari Research Framework - Staging Setup${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# ==============================================================================
# STEP 1: Detect Local IP
# ==============================================================================
echo -e "${YELLOW}[1/5] Detecting local IP address...${NC}"

detect_ip() {
    local ip=""

    # Method 1: ip route (Linux)
    if command -v ip &> /dev/null; then
        ip=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[\d.]+' | head -1)
    fi

    # Method 2: hostname -I (Linux fallback)
    if [ -z "$ip" ] && command -v hostname &> /dev/null; then
        ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    fi

    # Method 3: ifconfig (macOS/BSD)
    if [ -z "$ip" ] && command -v ifconfig &> /dev/null; then
        ip=$(ifconfig 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}')
    fi

    echo "$ip"
}

LOCAL_IP=$(detect_ip)

if [ -z "$LOCAL_IP" ]; then
    echo -e "${RED}ERROR: Could not detect local IP address${NC}"
    echo "Please set LOCAL_IP environment variable manually:"
    echo "  export LOCAL_IP=192.168.x.x"
    echo "  ./staging-setup.sh"
    exit 1
fi

# Allow override via environment variable
if [ -n "$OVERRIDE_IP" ]; then
    LOCAL_IP="$OVERRIDE_IP"
    echo -e "  Using override IP: ${GREEN}$LOCAL_IP${NC}"
else
    echo -e "  Detected IP: ${GREEN}$LOCAL_IP${NC}"
fi

# Validate IP format
if ! [[ $LOCAL_IP =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo -e "${RED}ERROR: Invalid IP address format: $LOCAL_IP${NC}"
    exit 1
fi

# ==============================================================================
# STEP 2: Generate SSL certificates for nip.io
# ==============================================================================
echo ""
echo -e "${YELLOW}[2/5] Generating SSL certificates for *.prf.${LOCAL_IP}.nip.io...${NC}"

mkdir -p "$CERTS_DIR"

# Check if the suite-level CA exists (prefer signing with shared CA)
SUITE_CA_CERT="$SCRIPT_DIR/../pol-proxy/certs/ca/pol-ca.crt"
SUITE_CA_KEY="$SCRIPT_DIR/../pol-proxy/certs/ca/pol-ca.key"

if [ -f "$SUITE_CA_CERT" ] && [ -f "$SUITE_CA_KEY" ]; then
    echo "  Using suite-level CA to sign certificate..."

    # Generate private key
    openssl genrsa -out "$CERTS_DIR/prf-proxy.key" 2048 2>/dev/null

    # Create certificate signing request with SAN
    cat > "$CERTS_DIR/openssl.cnf" << EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
req_extensions = req_ext

[dn]
C = US
ST = State
L = City
O = Polari Systems
OU = Development
CN = *.prf.${LOCAL_IP}.nip.io

[req_ext]
subjectAltName = @alt_names

[alt_names]
DNS.1 = ${LOCAL_IP}.nip.io
DNS.2 = prf.${LOCAL_IP}.nip.io
DNS.3 = api.prf.${LOCAL_IP}.nip.io
DNS.4 = files.prf.${LOCAL_IP}.nip.io
DNS.5 = s3.prf.${LOCAL_IP}.nip.io
DNS.6 = localhost
EOF

    # Generate CSR
    openssl req -new \
        -key "$CERTS_DIR/prf-proxy.key" \
        -out "$CERTS_DIR/prf-proxy.csr" \
        -config "$CERTS_DIR/openssl.cnf" \
        2>/dev/null

    # Sign with CA (valid for 365 days)
    openssl x509 -req \
        -in "$CERTS_DIR/prf-proxy.csr" \
        -CA "$SUITE_CA_CERT" \
        -CAkey "$SUITE_CA_KEY" \
        -CAcreateserial \
        -out "$CERTS_DIR/prf-proxy.crt" \
        -days 365 \
        -sha256 \
        -extfile "$CERTS_DIR/openssl.cnf" \
        -extensions req_ext \
        2>/dev/null

    echo -e "  Certificate signed by: ${GREEN}pol-ca${NC}"
else
    echo "  Suite CA not found, generating self-signed certificate..."

    # Generate self-signed certificate
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$CERTS_DIR/prf-proxy.key" \
        -out "$CERTS_DIR/prf-proxy.crt" \
        -subj "/C=US/ST=State/L=City/O=Polari Systems/OU=Dev/CN=*.prf.${LOCAL_IP}.nip.io" \
        -addext "subjectAltName=DNS:${LOCAL_IP}.nip.io,DNS:prf.${LOCAL_IP}.nip.io,DNS:api.prf.${LOCAL_IP}.nip.io,DNS:files.prf.${LOCAL_IP}.nip.io,DNS:s3.prf.${LOCAL_IP}.nip.io,DNS:localhost" \
        2>/dev/null

    echo -e "  ${YELLOW}Note: Self-signed certificate generated${NC}"
fi

echo -e "  Generated: ${GREEN}$CERTS_DIR/prf-proxy.crt${NC}"
echo -e "  Generated: ${GREEN}$CERTS_DIR/prf-proxy.key${NC}"

# ==============================================================================
# STEP 3: Generate nginx configuration
# ==============================================================================
echo ""
echo -e "${YELLOW}[3/5] Generating nginx configuration...${NC}"

mkdir -p "$GENERATED_DIR"

PROXY_DIR="$SCRIPT_DIR/prf-proxy"
TEMPLATE_FILE="$PROXY_DIR/nginx.staging.conf.template"
NGINX_OUTPUT="$GENERATED_DIR/nginx.staging.conf"

if [ ! -f "$TEMPLATE_FILE" ]; then
    echo -e "${RED}ERROR: Template file not found: $TEMPLATE_FILE${NC}"
    exit 1
fi

# Replace ${LOCAL_IP} placeholder with actual IP
sed "s/\${LOCAL_IP}/$LOCAL_IP/g" "$TEMPLATE_FILE" > "$NGINX_OUTPUT"

echo -e "  Generated: ${GREEN}$NGINX_OUTPUT${NC}"

# ==============================================================================
# STEP 4: Generate environment file
# ==============================================================================
echo ""
echo -e "${YELLOW}[4/5] Generating environment file...${NC}"

mkdir -p "$GENERATED_DIR"

ENV_FILE="$GENERATED_DIR/.env.staging"

cat > "$ENV_FILE" << EOF
# ==============================================================================
# POLARI RESEARCH FRAMEWORK - STAGING ENVIRONMENT
# ==============================================================================
# Generated by staging-setup.sh on $(date)
# IP Address: ${LOCAL_IP}
# ==============================================================================

LOCAL_IP=${LOCAL_IP}

# Base domain
NIP_DOMAIN=${LOCAL_IP}.nip.io

# Service URLs (via prf-proxy subdomain routing)
PRF_URL=https://prf.${LOCAL_IP}.nip.io
PRF_API_URL=https://api.prf.${LOCAL_IP}.nip.io

# CORS Origins (comma-separated)
CORS_ORIGINS=https://prf.${LOCAL_IP}.nip.io

# Deployment
DEPLOY_ENV=staging
IN_DOCKER_CONTAINER=true

# Backend
BACKEND_URL=localhost
BACKEND_HTTP_PORT=3000
BACKEND_HTTPS_PORT=2096

# Frontend
FRONTEND_URL=localhost
FRONTEND_HTTP_PORT=4201
FRONTEND_HTTPS_PORT=2087

# SSL
SSL_ENABLED=true

# WebSocket
WEBSOCKET_ENABLED=true
WEBSOCKET_PORT=3001

# Logging
LOG_LEVEL=INFO
CORS_ENABLED=true

# MinIO external URLs (pre-computed so compose file doesn't need LOCAL_IP interpolation)
MINIO_SERVER_URL=https://s3.prf.${LOCAL_IP}.nip.io
MINIO_BROWSER_REDIRECT_URL=https://files.prf.${LOCAL_IP}.nip.io
EOF

echo -e "  Generated: ${GREEN}$ENV_FILE${NC}"

# ==============================================================================
# STEP 5: Generate frontend runtime configuration
# ==============================================================================
echo ""
echo -e "${YELLOW}[5/5] Generating frontend runtime configuration...${NC}"

PRF_CONFIG_FILE="$GENERATED_DIR/prf-runtime-config.json"
cat > "$PRF_CONFIG_FILE" << EOF
{
  "_comment": "STAGING: Generated by staging-setup.sh for nip.io testing",
  "_generated": "$(date)",
  "_ip": "${LOCAL_IP}",

  "backend": {
    "http": {
      "protocol": "http",
      "url": "api.prf.${LOCAL_IP}.nip.io",
      "port": "80"
    },
    "https": {
      "protocol": "https",
      "url": "api.prf.${LOCAL_IP}.nip.io",
      "port": "443"
    },
    "ws": {
      "protocol": "wss",
      "url": "api.prf.${LOCAL_IP}.nip.io",
      "port": "443"
    },
    "preferHttps": true
  },

  "frontend": {
    "http": {
      "protocol": "http",
      "url": "prf.${LOCAL_IP}.nip.io",
      "port": "80"
    },
    "https": {
      "protocol": "https",
      "url": "prf.${LOCAL_IP}.nip.io",
      "port": "443"
    }
  },

  "connection": {
    "retryInterval": 3000,
    "maxRetryTime": 60000,
    "timeout": 30000
  },

  "features": {
    "enableHttps": true,
    "enableRuntimeConfig": true,
    "allowBackendChange": true
  }
}
EOF
echo -e "  Generated: ${GREEN}$PRF_CONFIG_FILE${NC}"

# ==============================================================================
# DONE
# ==============================================================================
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  Staging Setup Complete!${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo -e "Your staging URLs (via prf-proxy):"
echo -e "  ${BLUE}PRF Frontend:${NC}   https://prf.${LOCAL_IP}.nip.io"
echo -e "  ${BLUE}PRF API:${NC}        https://api.prf.${LOCAL_IP}.nip.io"
echo -e "  ${BLUE}MinIO Console:${NC}  https://files.prf.${LOCAL_IP}.nip.io"
echo -e "  ${BLUE}MinIO S3 API:${NC}   https://s3.prf.${LOCAL_IP}.nip.io"
echo ""
echo -e "To start the environment:"
echo -e "  ${YELLOW}sudo docker compose -f docker-compose.staging-nip.yml up -d --build${NC}"
echo ""
if [ -f "$SUITE_CA_CERT" ]; then
    echo -e "${YELLOW}TIP:${NC} If you haven't already, trust the suite CA certificate in your browser:"
    echo -e "  ${BLUE}$SUITE_CA_CERT${NC}"
fi
echo ""
