#!/bin/bash
# ==============================================================================
# POLARI RESEARCH FRAMEWORK - PRODUCTION SETUP SCRIPT
# ==============================================================================
#
# This script sets up the standalone PRF production environment by prompting
# for configuration values and generating the necessary files.
#
# Run this script before deploying to production:
#   ./prod-setup.sh
#
# Then deploy:
#   sudo docker compose -f docker-compose.prod.yml up -d --build
#
# SECURITY NOTE: Generated files contain sensitive configuration and are
# gitignored. Never commit the .generated/ directory contents to version control.
#
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GENERATED_DIR="$SCRIPT_DIR/.generated"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}============================================${NC}"
echo -e "${BLUE}  Polari Research Framework - Production Setup${NC}"
echo -e "${BLUE}============================================${NC}"
echo ""

# ==============================================================================
# STEP 1: Prompt for Production Domain
# ==============================================================================
echo -e "${YELLOW}[1/3] Production Domain Configuration${NC}"
echo ""

if [[ -n "${POLARI_PROD_DOMAIN:-}" ]]; then
    PROD_DOMAIN="$POLARI_PROD_DOMAIN"
else
    # Check for existing config
    EXISTING_DOMAIN=""
    if [ -f "$GENERATED_DIR/.env.prod" ]; then
        EXISTING_DOMAIN=$(grep "^PROD_DOMAIN=" "$GENERATED_DIR/.env.prod" 2>/dev/null | cut -d'=' -f2)
    fi

    if [ -n "$EXISTING_DOMAIN" ]; then
        echo -e "  Existing domain found: ${GREEN}$EXISTING_DOMAIN${NC}"
        read -p "  Use existing domain? (Y/n): " USE_EXISTING
        if [[ "$USE_EXISTING" =~ ^[Nn] ]]; then
            EXISTING_DOMAIN=""
        fi
    fi

    if [ -z "$EXISTING_DOMAIN" ]; then
        echo "  Enter your production domain (e.g., example.com):"
        echo "  This will be used for:"
        echo "    - PRF Frontend:    https://prf.example.com (or direct IP)"
        echo "    - PRF API:         https://api.prf.example.com (or direct IP)"
        echo "    - MinIO Console:   https://files.example.com"
        echo "    - MinIO S3 API:    https://s3.example.com"
        echo ""
        read -p "  Production domain: " PROD_DOMAIN

        if [ -z "$PROD_DOMAIN" ]; then
            echo -e "${RED}ERROR: Domain is required${NC}"
            exit 1
        fi
    else
        PROD_DOMAIN="$EXISTING_DOMAIN"
    fi
fi

echo -e "  Using domain: ${GREEN}$PROD_DOMAIN${NC}"

# ==============================================================================
# STEP 2: Prompt for MinIO Credentials (optional)
# ==============================================================================
echo ""
echo -e "${YELLOW}[2/4] MinIO Credentials (optional)${NC}"
echo ""
echo "  Press Enter to use defaults, or enter custom values."
echo ""

# MinIO (S3-compatible object storage)
if [[ -n "${POLARI_MINIO_ROOT_USER:-}" ]]; then
    MINIO_ROOT_USER="$POLARI_MINIO_ROOT_USER"
    echo -e "  Using provided MinIO root user"
else
    read -p "  MinIO root user [polari-admin]: " MINIO_ROOT_USER
    MINIO_ROOT_USER="${MINIO_ROOT_USER:-polari-admin}"
fi

if [[ -n "${POLARI_MINIO_ROOT_PASS:-}" ]]; then
    MINIO_ROOT_PASSWORD="$POLARI_MINIO_ROOT_PASS"
    echo -e "  Using provided MinIO root password"
else
    read -p "  MinIO root password [polari-file-store-password]: " MINIO_ROOT_PASSWORD
    MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-polari-file-store-password}"
fi

echo ""

# ==============================================================================
# STEP 3: Generate nginx configuration
# ==============================================================================
echo -e "${YELLOW}[3/4] Generating nginx configuration...${NC}"

mkdir -p "$GENERATED_DIR"

PROXY_DIR="$SCRIPT_DIR/prf-proxy"
TEMPLATE_FILE="$PROXY_DIR/nginx.prod.conf.template"
NGINX_OUTPUT="$GENERATED_DIR/nginx.prod.conf"

if [ ! -f "$TEMPLATE_FILE" ]; then
    echo -e "${RED}ERROR: Template file not found: $TEMPLATE_FILE${NC}"
    exit 1
fi

# Replace ${PROD_DOMAIN} placeholder with actual domain
sed "s/\${PROD_DOMAIN}/$PROD_DOMAIN/g" "$TEMPLATE_FILE" > "$NGINX_OUTPUT"

echo -e "  Generated: ${GREEN}$NGINX_OUTPUT${NC}"

# ==============================================================================
# STEP 4: Generate environment and runtime config files
# ==============================================================================
echo ""
echo -e "${YELLOW}[4/4] Generating environment and runtime config files...${NC}"

# --- Environment file ---
ENV_FILE="$GENERATED_DIR/.env.prod"

cat > "$ENV_FILE" << EOF
# ==============================================================================
# POLARI RESEARCH FRAMEWORK - PRODUCTION ENVIRONMENT
# ==============================================================================
# Generated by prod-setup.sh on $(date)
# Domain: ${PROD_DOMAIN}
#
# SECURITY: This file contains sensitive configuration.
# DO NOT commit to version control.
# ==============================================================================

PROD_DOMAIN=${PROD_DOMAIN}

# Service URLs
PRF_URL=https://prf.${PROD_DOMAIN}
PRF_API_URL=https://api.prf.${PROD_DOMAIN}

# CORS Origins (comma-separated)
CORS_ORIGINS=https://prf.${PROD_DOMAIN},https://${PROD_DOMAIN}

# Deployment
DEPLOY_ENV=production
IN_DOCKER_CONTAINER=true

# Backend
BACKEND_URL=prf.${PROD_DOMAIN}
BACKEND_HTTP_PORT=3000
BACKEND_HTTPS_PORT=2096

# Frontend
FRONTEND_URL=prf.${PROD_DOMAIN}
FRONTEND_HTTP_PORT=4201
FRONTEND_HTTPS_PORT=2087

# SSL
SSL_ENABLED=true

# WebSocket
WEBSOCKET_ENABLED=true
WEBSOCKET_PORT=3001

# MinIO (S3-compatible object storage) credentials
MINIO_ROOT_USER=${MINIO_ROOT_USER}
MINIO_ROOT_PASSWORD=${MINIO_ROOT_PASSWORD}
MINIO_ACCESS_KEY=${MINIO_ROOT_USER}
MINIO_SECRET_KEY=${MINIO_ROOT_PASSWORD}

# Logging
LOG_LEVEL=WARNING
CORS_ENABLED=true

# MinIO external URLs (pre-computed so compose file doesn't need PROD_DOMAIN interpolation)
MINIO_SERVER_URL=https://s3.${PROD_DOMAIN}
MINIO_BROWSER_REDIRECT_URL=https://files.${PROD_DOMAIN}
EOF

echo -e "  Generated: ${GREEN}$ENV_FILE${NC}"

# --- Frontend runtime config ---
PRF_CONFIG_FILE="$GENERATED_DIR/prf-runtime-config.prod.json"
cat > "$PRF_CONFIG_FILE" << EOF
{
  "_comment": "PRODUCTION: Generated by prod-setup.sh",
  "_generated": "$(date)",
  "_domain": "${PROD_DOMAIN}",

  "backend": {
    "http": {
      "protocol": "http",
      "url": "api.prf.${PROD_DOMAIN}",
      "port": "80"
    },
    "https": {
      "protocol": "https",
      "url": "api.prf.${PROD_DOMAIN}",
      "port": "443"
    },
    "ws": {
      "protocol": "wss",
      "url": "api.prf.${PROD_DOMAIN}",
      "port": "443"
    },
    "preferHttps": true
  },

  "frontend": {
    "http": {
      "protocol": "http",
      "url": "prf.${PROD_DOMAIN}",
      "port": "80"
    },
    "https": {
      "protocol": "https",
      "url": "prf.${PROD_DOMAIN}",
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
    "enableRuntimeConfig": false,
    "allowBackendChange": false
  }
}
EOF
echo -e "  Generated: ${GREEN}$PRF_CONFIG_FILE${NC}"

# ==============================================================================
# DONE
# ==============================================================================
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  Production Setup Complete!${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo -e "Your production URLs:"
echo -e "  ${BLUE}PRF Frontend:${NC}  https://prf.${PROD_DOMAIN}"
echo -e "  ${BLUE}PRF API:${NC}       https://api.prf.${PROD_DOMAIN}"
echo -e "  ${BLUE}MinIO Console:${NC} https://files.${PROD_DOMAIN}"
echo -e "  ${BLUE}MinIO S3 API:${NC}  https://s3.${PROD_DOMAIN}"
echo ""
echo -e "Generated files (gitignored):"
echo -e "  ${BLUE}$GENERATED_DIR/nginx.prod.conf${NC}"
echo -e "  ${BLUE}$GENERATED_DIR/.env.prod${NC}"
echo -e "  ${BLUE}$GENERATED_DIR/prf-runtime-config.prod.json${NC}"
echo ""
echo -e "To deploy:"
echo -e "  ${YELLOW}sudo docker compose -f docker-compose.prod.yml up -d --build${NC}"
echo ""
echo -e "${RED}SECURITY REMINDER:${NC} The .generated/ directory contains sensitive"
echo -e "configuration. Ensure it remains gitignored and never committed."
echo ""
