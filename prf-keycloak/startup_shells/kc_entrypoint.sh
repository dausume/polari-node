#!/bin/bash

# This script wraps the Keycloak startup process to include custom initialization steps.

if [ -d "/opt/keycloak/conf/" ]; then
    echo "listing keycloak configuration directory /opt/keycloak/conf/:"
    ls -la /opt/keycloak/conf/
else
    echo "/opt/keycloak/conf/ directory does not exist."
fi

# Ensure keycloak.conf exists before printing it.
if [ -f "/opt/keycloak/conf/keycloak.conf" ]; then
    echo "Printing /opt/keycloak/conf/keycloak.conf contents:"
    cat /opt/keycloak/conf/keycloak.conf
else
    echo "/opt/keycloak/conf/keycloak.conf file does not exist."
fi

# Start Keycloak in the background
echo "Starting Keycloak server..."
/opt/keycloak/bin/kc.sh "$@" start --features=admin-fine-grained-authz:v2 &
KEYCLOAK_PID=$!

# Wait a moment for Keycloak to initialize
sleep 5

# Import realms from realm-imports directory
echo "Attempting to import realms from /opt/realm-imports..."
if [ -d "/opt/realm-imports" ] && [ "$(ls -A /opt/realm-imports/*.json 2>/dev/null)" ]; then
    /opt/startup_shells/load_realms.sh
else
    echo "No realm files found in /opt/realm-imports, skipping realm import."
fi

# Configure client redirect URIs based on deployment mode
# This adds environment-specific redirect URIs (suite mode, standalone, etc.)
echo "Configuring client redirect URIs..."
if [ -f "/opt/startup_shells/configure_clients.sh" ]; then
    /opt/startup_shells/configure_clients.sh
else
    echo "configure_clients.sh not found, skipping client configuration."
fi

# Wait for the Keycloak process to keep container alive
wait $KEYCLOAK_PID