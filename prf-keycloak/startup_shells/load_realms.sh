#!/bin/bash

# This script sets the Keycloak server URL for use in other scripts or configurations.
KEYCLOAK_URL="http://localhost:8080"

# Assume Keycloak is running on localhost and default port 8080
# We may need to wait for Keycloak to start before it is reachable

MAX_CHECK_KEYCLOAK_RETRIES=30
CHECK_KEYCLOAK_INTERVAL=10
REACHABLE_KEYCLOAK=false

# Polling loop to check if Keycloak is up
for ((i=1; i<=MAX_CHECK_KEYCLOAK_RETRIES; i++)); do
    if curl -s "$KEYCLOAK_URL/health/ready" > /dev/null; then
        echo "Keycloak is up and running at $KEYCLOAK_URL"
        REACHABLE_KEYCLOAK=true
        break
    else
        echo "Waiting for Keycloak to start... (Attempt: $i/$MAX_CHECK_KEYCLOAK_RETRIES)"
        sleep $CHECK_KEYCLOAK_INTERVAL
    fi
done

# If we exit loop and keycloak is not reachable, exit with error
if [ "$REACHABLE_KEYCLOAK" = false ]; then
    echo "Keycloak is not reachable after maximum retries. Exiting."
    exit 1
fi

# Now attempt to retrieve the access token using admin credentials
MASTER_REALM="master"
MASTER_USERNAME="${KEYCLOAK_ADMIN:-admin}"
MASTER_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD:-admin}"
MASTER_CLIENT="admin-cli"

TOKEN_RETRY_LIMIT=5
TOKEN_RETRY_INTERVAL=5

TOKEN_RETRIEVED=false

echo "Attempting to retrieve access token for realm '$MASTER_REALM'..."

for ((j=1; j<=TOKEN_RETRY_LIMIT; j++)); do
    RESPONSE=$(curl -s -X POST "$KEYCLOAK_URL/realms/$MASTER_REALM/protocol/openid-connect/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "username=$MASTER_USERNAME" \
        -d "password=$MASTER_PASSWORD" \
        -d "grant_type=password" \
        -d "client_id=$MASTER_CLIENT")

    ACCESS_TOKEN=$(echo "$RESPONSE" | jq -r '.access_token')

    if [ "$ACCESS_TOKEN" != "null" ] && [ -n "$ACCESS_TOKEN" ]; then
        echo "Successfully retrieved access token."
        TOKEN_RETRIEVED=true
        break
    else
        echo "Failed to retrieve access token. Response: $RESPONSE"
        echo "Retrying... (Attempt: $j/$TOKEN_RETRY_LIMIT)"
        sleep $TOKEN_RETRY_INTERVAL
    fi
done

if [ "$TOKEN_RETRIEVED" = false ]; then
    echo "Unable to retrieve access token after maximum retries. Exiting."
    exit 1
fi

REALM_IMPORTS_DIR="/opt/realm-imports"

# Import each realm JSON file found in the specified directory
for REALM_FILE in "$REALM_IMPORTS_DIR"/*.json; do
    if [ -f "$REALM_FILE" ]; then
        REALM_NAME=$(basename "$REALM_FILE" .json)
        echo "Importing realm from file: $REALM_FILE"

        IMPORT_RESPONSE=$(curl -s -X POST "$KEYCLOAK_URL/admin/realms" \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer $ACCESS_TOKEN" \
            --data-binary "@$REALM_FILE")

        if [ -z "$IMPORT_RESPONSE" ]; then
            echo "Successfully imported realm '$REALM_NAME'."
        else
            echo "Failed to import realm '$REALM_NAME'. Response: $IMPORT_RESPONSE"
        fi
    else
        echo "No realm JSON files found in directory: $REALM_IMPORTS_DIR"
    fi
done

echo "Realm import process completed."