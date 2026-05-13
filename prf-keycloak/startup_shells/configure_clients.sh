#!/bin/bash

# ==============================================================================
# POLARI RESEARCH FRAMEWORK - KEYCLOAK CLIENT CONFIGURATION
# ==============================================================================
# PRF-only variant of the suite's configure_clients.sh. Only handles the
# Polari realm — polari-frontend gets its redirect URIs patched per
# deployment mode, and polari-backend's client secret is set from env vars
# alongside its realm-management service-account roles.
#
# Environment variables:
#   KEYCLOAK_MODE   - "suite", "standalone", "staging", or "production"
#   KC_HOSTNAME     - Keycloak hostname (e.g. auth.10.0.0.102.nip.io)
#                     Used by staging/production to derive subdomain URIs
#   KEYCLOAK_ADMIN  - master-realm admin username (default: admin)
#   KEYCLOAK_ADMIN_PASSWORD - master-realm admin password
#   KEYCLOAK_POLARI_BACKEND_CLIENT_SECRET - secret to set on polari-backend
#   PRF_REDIRECT_URIS - optional comma-separated extra URIs for polari-frontend
# ==============================================================================

KEYCLOAK_URL="http://localhost:8080"

MODE="${KEYCLOAK_MODE:-auto}"
if [ "$1" = "--suite" ]; then
    MODE="suite"
elif [ "$1" = "--standalone" ]; then
    MODE="standalone"
fi

echo "=============================================="
echo "PRF Keycloak Client Configuration"
echo "Mode: $MODE"
echo "=============================================="

# --- Redirect URI tiers ----------------------------------------------------
SUITE_REDIRECT_URIS=(
    "https://localhost:2087/*"
    "https://localhost:2087"
)
STANDALONE_REDIRECT_URIS=(
    "http://localhost:4200/*"
    "http://localhost:4200"
    "https://localhost:4200/*"
    "https://localhost:4200"
)
BASE_REDIRECT_URIS=(
    "http://localhost:4200/*"
    "http://localhost:4200"
)

PRF_SUBDOMAIN_REDIRECT_URIS=()
if [ -n "$KC_HOSTNAME" ]; then
    # Standalone-PRF subdomain layout: auth.prf.<ip>.nip.io + prf.<ip>.nip.io
    # — stripping "auth." from KC_HOSTNAME already yields the frontend host,
    # so we use it directly. (The suite's configure_clients.sh prepends
    # "prf." because its KC_HOSTNAME is auth.<ip>.nip.io without the prf.
    # tier; copying that pattern here would produce prf.prf.<ip>.nip.io.)
    FRONTEND_HOST="${KC_HOSTNAME#auth.}"
    echo "Deriving redirect URIs from KC_HOSTNAME=$KC_HOSTNAME (frontend host: $FRONTEND_HOST)"
    PRF_SUBDOMAIN_REDIRECT_URIS=(
        "https://${FRONTEND_HOST}/*"
        "https://${FRONTEND_HOST}"
        "https://app.${FRONTEND_HOST}/*"
        "https://app.${FRONTEND_HOST}"
    )
elif [ "$MODE" = "staging" ] || [ "$MODE" = "production" ]; then
    echo "WARNING: MODE=$MODE but KC_HOSTNAME is not set. Subdomain URIs skipped."
fi

if [ "$MODE" = "suite" ]; then
    POLARI_REDIRECT_URIS=("${BASE_REDIRECT_URIS[@]}" "${SUITE_REDIRECT_URIS[@]}")
elif [ "$MODE" = "standalone" ]; then
    POLARI_REDIRECT_URIS=("${BASE_REDIRECT_URIS[@]}" "${STANDALONE_REDIRECT_URIS[@]}")
elif [ "$MODE" = "staging" ] || [ "$MODE" = "production" ]; then
    POLARI_REDIRECT_URIS=("${BASE_REDIRECT_URIS[@]}" "${PRF_SUBDOMAIN_REDIRECT_URIS[@]}")
else
    POLARI_REDIRECT_URIS=("${BASE_REDIRECT_URIS[@]}" "${SUITE_REDIRECT_URIS[@]}" "${STANDALONE_REDIRECT_URIS[@]}" "${PRF_SUBDOMAIN_REDIRECT_URIS[@]}")
fi

if [ -n "$PRF_REDIRECT_URIS" ]; then
    IFS=',' read -ra CUSTOM_URIS <<< "$PRF_REDIRECT_URIS"
    POLARI_REDIRECT_URIS+=("${CUSTOM_URIS[@]}")
fi
POLARI_REDIRECT_URIS=($(printf '%s\n' "${POLARI_REDIRECT_URIS[@]}" | sort -u))

echo "polari-frontend redirect URIs:"
printf '  - %s\n' "${POLARI_REDIRECT_URIS[@]}"

# --- Wait for Keycloak readiness --------------------------------------------
MAX_RETRIES=30
RETRY_INTERVAL=5
KEYCLOAK_READY=false
echo ""
echo "Waiting for Keycloak to be ready..."
for ((i=1; i<=MAX_RETRIES; i++)); do
    if curl -s "$KEYCLOAK_URL/health/ready" > /dev/null 2>&1; then
        echo "Keycloak is ready."
        KEYCLOAK_READY=true
        break
    fi
    echo "Waiting for Keycloak... (Attempt: $i/$MAX_RETRIES)"
    sleep $RETRY_INTERVAL
done
if [ "$KEYCLOAK_READY" = false ]; then
    echo "ERROR: Keycloak is not reachable after $MAX_RETRIES attempts."
    exit 1
fi

# --- Obtain admin access token ----------------------------------------------
MASTER_USERNAME="${KEYCLOAK_ADMIN:-admin}"
MASTER_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-admin}"

echo ""
echo "Obtaining admin access token..."
TOKEN_RESPONSE=$(curl -s -X POST "$KEYCLOAK_URL/realms/master/protocol/openid-connect/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=$MASTER_USERNAME" \
    -d "password=$MASTER_PASSWORD" \
    -d "grant_type=password" \
    -d "client_id=admin-cli")

ACCESS_TOKEN=$(echo "$TOKEN_RESPONSE" | jq -r '.access_token')
if [ "$ACCESS_TOKEN" = "null" ] || [ -z "$ACCESS_TOKEN" ]; then
    echo "ERROR: Failed to obtain access token. Response: $TOKEN_RESPONSE"
    exit 1
fi

# --- Polari realm + clients --------------------------------------------------
POLARI_REALM="Polari"
POLARI_FE_CLIENT_ID="polari-frontend"
POLARI_BE_CLIENT_ID="polari-backend"

echo ""
echo "Configuring Polari realm clients..."

REALM_CHECK=$(curl -s -o /dev/null -w "%{http_code}" \
    -X GET "$KEYCLOAK_URL/admin/realms/$POLARI_REALM" \
    -H "Authorization: Bearer $ACCESS_TOKEN")

if [ "$REALM_CHECK" != "200" ]; then
    echo "ERROR: Polari realm not found (HTTP $REALM_CHECK). Did load_realms.sh run?"
    exit 1
fi

# 1. polari-frontend - patch redirect URIs ------------------------------------
PFE_RESPONSE=$(curl -s -X GET \
    "$KEYCLOAK_URL/admin/realms/$POLARI_REALM/clients?clientId=$POLARI_FE_CLIENT_ID" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -H "Content-Type: application/json")
PFE_UUID=$(echo "$PFE_RESPONSE" | jq -r '.[0].id')

if [ "$PFE_UUID" = "null" ] || [ -z "$PFE_UUID" ]; then
    echo "WARNING: '$POLARI_FE_CLIENT_ID' not found. Skipping."
else
    PFE_CURRENT=$(curl -s -X GET \
        "$KEYCLOAK_URL/admin/realms/$POLARI_REALM/clients/$PFE_UUID" \
        -H "Authorization: Bearer $ACCESS_TOKEN" \
        -H "Content-Type: application/json")

    URIS_JSON=$(printf '%s\n' "${POLARI_REDIRECT_URIS[@]}" | jq -R . | jq -s .)
    PAYLOAD=$(echo "$PFE_CURRENT" | jq --argjson uris "$URIS_JSON" '
        .redirectUris = $uris |
        .webOrigins = ["*"]
    ')

    UPDATE=$(curl -s -w "\n%{http_code}" -X PUT \
        "$KEYCLOAK_URL/admin/realms/$POLARI_REALM/clients/$PFE_UUID" \
        -H "Authorization: Bearer $ACCESS_TOKEN" \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD")
    HTTP_CODE=$(echo "$UPDATE" | tail -n1)
    if [ "$HTTP_CODE" = "204" ] || [ "$HTTP_CODE" = "200" ]; then
        echo "SUCCESS: polari-frontend redirect URIs updated."
    else
        echo "ERROR: polari-frontend update failed. HTTP $HTTP_CODE"
        echo "$UPDATE" | sed '$d'
    fi
fi

# 2. polari-backend - set client secret + service-account roles --------------
PBE_RESPONSE=$(curl -s -X GET \
    "$KEYCLOAK_URL/admin/realms/$POLARI_REALM/clients?clientId=$POLARI_BE_CLIENT_ID" \
    -H "Authorization: Bearer $ACCESS_TOKEN" \
    -H "Content-Type: application/json")
PBE_UUID=$(echo "$PBE_RESPONSE" | jq -r '.[0].id')

if [ "$PBE_UUID" = "null" ] || [ -z "$PBE_UUID" ]; then
    echo "WARNING: '$POLARI_BE_CLIENT_ID' not found. Skipping."
else
    # 2a. set secret
    if [ -n "$KEYCLOAK_POLARI_BACKEND_CLIENT_SECRET" ]; then
        PBE_DETAIL=$(curl -s -X GET \
            "$KEYCLOAK_URL/admin/realms/$POLARI_REALM/clients/$PBE_UUID" \
            -H "Authorization: Bearer $ACCESS_TOKEN" \
            -H "Content-Type: application/json")
        PBE_PATCHED=$(echo "$PBE_DETAIL" | jq --arg secret "$KEYCLOAK_POLARI_BACKEND_CLIENT_SECRET" '.secret = $secret')

        SECRET_UPDATE=$(curl -s -w "\n%{http_code}" -X PUT \
            "$KEYCLOAK_URL/admin/realms/$POLARI_REALM/clients/$PBE_UUID" \
            -H "Authorization: Bearer $ACCESS_TOKEN" \
            -H "Content-Type: application/json" \
            -d "$PBE_PATCHED")
        SECRET_HTTP=$(echo "$SECRET_UPDATE" | tail -n1)
        if [ "$SECRET_HTTP" = "204" ] || [ "$SECRET_HTTP" = "200" ]; then
            echo "SUCCESS: polari-backend client secret set."
        else
            echo "WARNING: polari-backend secret update failed. HTTP $SECRET_HTTP"
            echo "$SECRET_UPDATE" | sed '$d'
        fi
    else
        echo "WARNING: KEYCLOAK_POLARI_BACKEND_CLIENT_SECRET not set. Skipping secret."
    fi

    # 2b. assign realm-management roles to service-account user
    SA_RESPONSE=$(curl -s -X GET \
        "$KEYCLOAK_URL/admin/realms/$POLARI_REALM/clients/$PBE_UUID/service-account-user" \
        -H "Authorization: Bearer $ACCESS_TOKEN" \
        -H "Content-Type: application/json")
    SA_UUID=$(echo "$SA_RESPONSE" | jq -r '.id')

    if [ "$SA_UUID" = "null" ] || [ -z "$SA_UUID" ]; then
        echo "INFO: service-account-user unavailable for $POLARI_BE_CLIENT_ID. Skipping roles."
    else
        RM_RESPONSE=$(curl -s -X GET \
            "$KEYCLOAK_URL/admin/realms/$POLARI_REALM/clients?clientId=realm-management" \
            -H "Authorization: Bearer $ACCESS_TOKEN" \
            -H "Content-Type: application/json")
        RM_UUID=$(echo "$RM_RESPONSE" | jq -r '.[0].id')

        if [ "$RM_UUID" = "null" ] || [ -z "$RM_UUID" ]; then
            echo "WARNING: realm-management client not found."
        else
            ROLES=("view-users" "manage-users" "view-realm")
            ROLE_JSON="["
            for rn in "${ROLES[@]}"; do
                RR=$(curl -s -X GET \
                    "$KEYCLOAK_URL/admin/realms/$POLARI_REALM/clients/$RM_UUID/roles/$rn" \
                    -H "Authorization: Bearer $ACCESS_TOKEN" \
                    -H "Content-Type: application/json")
                RID=$(echo "$RR" | jq -r '.id')
                if [ "$RID" = "null" ] || [ -z "$RID" ]; then
                    echo "  WARN: role '$rn' missing."
                    continue
                fi
                if [ "$ROLE_JSON" != "[" ]; then ROLE_JSON="$ROLE_JSON,"; fi
                ROLE_JSON="$ROLE_JSON$(echo "$RR" | jq -c '{id: .id, name: .name}')"
            done
            ROLE_JSON="$ROLE_JSON]"

            if [ "$ROLE_JSON" != "[]" ]; then
                ASSIGN=$(curl -s -w "\n%{http_code}" -X POST \
                    "$KEYCLOAK_URL/admin/realms/$POLARI_REALM/users/$SA_UUID/role-mappings/clients/$RM_UUID" \
                    -H "Authorization: Bearer $ACCESS_TOKEN" \
                    -H "Content-Type: application/json" \
                    -d "$ROLE_JSON")
                AHTTP=$(echo "$ASSIGN" | tail -n1)
                if [ "$AHTTP" = "204" ] || [ "$AHTTP" = "200" ]; then
                    echo "SUCCESS: polari-backend service-account roles assigned."
                else
                    echo "WARNING: role grant failed. HTTP $AHTTP"
                    echo "$ASSIGN" | sed '$d'
                fi
            fi
        fi
    fi
fi

echo ""
echo "=============================================="
echo "PRF Keycloak client configuration complete."
echo "=============================================="
